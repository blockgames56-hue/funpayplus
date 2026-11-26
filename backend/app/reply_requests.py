"""Buyer refund requests require an authenticated owner's explicit decision."""

import asyncio
import re
from urllib.parse import quote

from sqlalchemy import select

from .db import Session
from .integrated_config import packed
from .models import ChatMessage, Order, ReplyEvent, utcnow
from .security import vault
from .services import accounts, get_setting, telegram

lock = asyncio.Lock()


async def create_request(db, chat, text):
    found = re.search(r"#([A-Za-z0-9]+)", text)
    query = select(Order).where(
        Order.account_id == chat.account_id,
        Order.buyer_id == chat.interlocutor_id,
        Order.status == "paid",
    )
    if found:
        query = query.where(Order.funpay_order_id == found[1])
    rows = (await db.scalars(query.order_by(Order.id.desc()).limit(2))).all()
    if len(rows) != 1:
        return "Укажите номер оплаченного заказа: !возврат #НОМЕР. Заявка будет передана продавцу."
    order = rows[0]
    if await db.scalar(select(ReplyEvent.id).where(ReplyEvent.event_key == f"refund:{order.id}")):
        return "Заявка по этому заказу уже передана продавцу."
    row = ReplyEvent(
        account_id=chat.account_id,
        chat_id=chat.id,
        order_id=order.id,
        event_key=f"refund:{order.id}",
        kind="refund",
        state="awaiting_owner",
        payload=packed({}),
        due_at=utcnow(),
    )
    db.add(row)
    await db.commit()
    recent = (
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat.id)
            .order_by(ChatMessage.id.desc())
            .limit(4)
        )
    ).all()
    history = "\n".join(f"{m.sender_name}: {m.text[:700]}" for m in reversed(recent))
    settings = await get_setting("telegram", {})
    if settings.get("enabled") and settings.get("token") and settings.get("chat_id"):
        await telegram.request(
            vault.decrypt(settings["token"]),
            "sendMessage",
            {
                "chat_id": settings["chat_id"],
                "text": f"Заявка на возврат #{order.funpay_order_id}\nПокупатель: {order.buyer_name}\n{order.description[:1000]}\n{order.sum} {order.currency}\n\nПоследние сообщения:\n{history}",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Вернуть деньги",
                                "callback_data": f"reply-refund:{row.id}:approve",
                            },
                            {"text": "Отказать", "callback_data": f"reply-refund:{row.id}:reject"},
                        ],
                        [
                            {
                                "text": "Заказ",
                                "url": f"https://funpay.com/orders/{quote(order.funpay_order_id, safe='')}/",
                            },
                            {
                                "text": "Чат",
                                "url": f"https://funpay.com/chat/?node={quote(chat.funpay_chat_id, safe='')}",
                            },
                        ],
                    ]
                },
            },
        )

    return "Заявка на возврат передана продавцу. Ожидайте его решения."


async def decide(ident, decision):
    async with lock, Session() as db:
        row = await db.get(ReplyEvent, ident)
        if not row or row.kind != "refund":
            raise ValueError("Заявка не найдена")
        if row.state != "awaiting_owner":
            raise ValueError("Решение уже принято или требует ручной сверки")
        if decision == "reject":
            row.state = "rejected"
            await db.commit()
            return
        order = await db.get(Order, row.order_id)
        if not order:
            raise ValueError("Заказ удалён")
        client = await accounts.client(order.account_id)
        detail = await client.order_details(order.funpay_order_id)
        if detail.get("status") == "refunded":
            row.state = "refunded"
            order.status = "refunded"
            await db.commit()
            return
        if (
            detail.get("status") != "paid"
            or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
        ):
            raise ValueError("Оплата или покупатель не подтверждены")
        row.state = "sending"
        await db.commit()
        try:
            await client.refund(order.funpay_order_id)
        except Exception:
            row.state = "uncertain"
            await db.commit()
            raise ValueError(
                "Результат возврата неизвестен. Проверьте заказ на FunPay; повторный запрос заблокирован"
            ) from None
        row.state = "refunded"
        order.status = "refunded"
        await db.commit()


async def handle_callback(callback, settings):
    data = callback.get("data", "")
    if not data.startswith("reply-refund:"):
        return
    message = callback.get("message", {})
    owner = str(settings.get("chat_id"))
    # Financial callbacks are accepted only in the configured owner's private chat.
    allowed = (
        str(message.get("chat", {}).get("id")) == owner == str(callback.get("from", {}).get("id"))
    )
    text = "Подтвердите решение в приложении" if not allowed else ""
    if allowed:
        try:
            _, ident, decision = data.split(":")
            if decision not in {"approve", "reject"}:
                return
            await decide(int(ident), decision)
            text = "Решение сохранено"
        except (ValueError, TypeError):
            text = "Проверьте заявку в приложении: решение не выполнено или требует сверки"
    await telegram.request(
        vault.decrypt(settings["token"]),
        "answerCallbackQuery",
        {"callback_query_id": callback["id"], "text": text, "show_alert": True},
    )
