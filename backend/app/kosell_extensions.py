"""Paid extensions and one-time review rewards, with a claim before every debit."""

import contextlib
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import select

from . import kosell
from .db import Session
from .integrated_config import packed, unpacked
from .message_templates import notify, order_context, render
from .models import KosellMessage, KosellOperation, KosellRental, Order, utcnow
from .services import accounts, get_setting, save_setting


def receipt(response, wallet):
    # RentalInfo records price_paid in USD even when the RUB wallet was used.
    field = (
        "price_rub" if wallet == "RUB" and response.get("price_rub") is not None else "price_paid"
    )
    code = "RUB" if field == "price_rub" else "USD"
    try:
        amount = Decimal(str(response[field]))
        return (
            {"currency": code, "amount": str(amount)}
            if amount.is_finite() and amount >= 0
            else None
        )
    except (KeyError, ValueError, ArithmeticError):
        return None


async def mark_intent(rental):
    await save_setting(
        f"kosell:extend:{rental.chat_id}",
        {"rental_id": rental.id, "expires_at": (utcnow() + timedelta(minutes=20)).isoformat()},
    )


async def command(chat_id, account_id, message):
    import re

    from .models import Chat

    match = re.fullmatch(
        r"!(time|kosell\s+продлить)(?:\s+(\S+))?", message.get("text", "").strip(), re.I
    )
    if not match:
        return None
    config = await kosell.settings()
    if not config["enabled"]:
        return False
    async with Session() as db:
        chat = await db.get(Chat, chat_id)
        if (
            not chat
            or chat.account_id != account_id
            or chat.interlocutor_id != message.get("sender_id")
        ):
            return False
        rows = (
            await db.scalars(
                select(KosellRental)
                .join(Order)
                .where(
                    KosellRental.chat_id == chat_id,
                    KosellRental.buyer_id == chat.interlocutor_id,
                    Order.account_id == account_id,
                    KosellRental.status == "ACTIVE",
                )
            )
        ).all()
        rows = [
            r
            for r in rows
            if r.end_time
            and kosell.aware(r.end_time) > utcnow()
            and (not match[2] or match[2] in {r.kosell_uid, unpacked(r.payload).get("login")})
        ]
        if not rows:
            return False
        if len(rows) != 1:
            await accounts.send(
                chat_id,
                await render(
                    "kosell",
                    "kosell_select",
                    {"uids": ", ".join(r.kosell_uid for r in rows)},
                    db=db,
                ),
            )
            return True
        rental = rows[0]
        order = await db.get(Order, rental.order_id)
        live = await (await accounts.client(account_id)).order_details(order.funpay_order_id)
        if (
            live.get("status") not in {"paid", "closed"}
            or int(live.get("buyer", {}).get("user_id", 0)) != rental.buyer_id
        ):
            return True
        if match[1].casefold() == "time":
            minutes = max(0, int((kosell.aware(rental.end_time) - utcnow()).total_seconds() // 60))
            text = await render(
                "kosell",
                "kosell_time",
                order_context(
                    order,
                    minutes=minutes,
                    expires=rental.end_time.isoformat(),
                    uid=rental.kosell_uid,
                ),
                db=db,
            )
        else:
            await mark_intent(rental)
            text = await render(
                "kosell", "kosell_extend_intent", order_context(order, uid=rental.kosell_uid), db=db
            )
        await accounts.send(chat_id, text)
    return True


async def consume_intent(db, order, chat, binding, hours):
    intent = await get_setting(f"kosell:extend:{chat.id}", {})
    if not intent or kosell.aware(datetime.fromisoformat(intent["expires_at"])) <= utcnow():
        return None
    if kosell.aware(order.created_at) < kosell.aware(
        datetime.fromisoformat(intent["expires_at"])
    ) - timedelta(minutes=20):
        return None
    rental = await db.get(KosellRental, intent["rental_id"])
    if (
        not rental
        or rental.status != "ACTIVE"
        or not rental.end_time
        or kosell.aware(rental.end_time) <= utcnow()
    ):
        return None
    payload = unpacked(rental.payload)
    if rental.buyer_id != order.buyer_id or payload["product_id"] != int(binding.service_id):
        return None
    op = KosellOperation(
        rental_id=rental.id,
        order_id=order.id,
        event_key=f"paid:{order.id}",
        kind="extension",
        hours=hours,
        payload=packed(
            {
                "key": payload["key"],
                "currency": payload["currency"],
                "previous_end": kosell.aware(rental.end_time).isoformat(),
            }
        ),
    )
    db.add(op)
    order.delivery_state = "kosell_pending"
    await db.commit()
    # Clearing intent after the claim cannot permit another debit for this order.
    await save_setting(f"kosell:extend:{chat.id}", {})
    return op.id


async def execute(ident):
    async with Session() as db:
        op = await db.get(KosellOperation, ident)
        if not op or op.state != "CLAIMED":
            return
        rental = await db.get(KosellRental, op.rental_id)
        order = await db.get(Order, op.order_id or rental.order_id)
        context_order_id = order.id
        data = unpacked(op.payload)
        try:
            from .kosell_sync import in_scope
            from .models import Account, BlacklistEntry

            config = await kosell.settings()
            account = await db.get(Account, order.account_id)
            if (
                not config["enabled"]
                or not account
                or not account.is_active
                or not in_scope(config, order.account_id)
                or await db.scalar(
                    select(BlacklistEntry.id).where(BlacklistEntry.user_id == rental.buyer_id)
                )
            ):
                raise kosell.KoSellError("Плагин, аккаунт или покупатель остановлен")
            if rental.status != "ACTIVE" or kosell.aware(rental.end_time) <= utcnow():
                raise kosell.KoSellError("Исходная аренда завершена")
            live = await (await accounts.client(order.account_id)).order_details(
                order.funpay_order_id
            )
            if (
                live.get("status") not in ({"paid"} if op.order_id else {"closed"})
                or int(live.get("buyer", {}).get("user_id", 0)) != rental.buyer_id
            ):
                raise kosell.KoSellError("Покупатель или оплата не подтверждены")
            cost = await kosell.api(
                "POST",
                "/rental/calculate-extend-price",
                key=data["key"],
                data={"rental_uid": rental.kosell_uid, "hours": op.hours},
            )
            config = await kosell.settings()
            field = {"RUB": "total_rub", "USD": "total_usd"}.get(order.currency)
            if not field or not Decimal(str(cost.get(field, "NaN"))).is_finite():
                raise kosell.KoSellError("Стоимость продления не подтверждена")
            if op.order_id and not config["allow_loss"] and Decimal(str(cost[field])) > order.sum:
                raise kosell.KoSellError("Продление дороже оплаты. Списание не отправлялось")
            result = await kosell.api(
                "POST",
                f"/rental/{quote(rental.kosell_uid, safe='')}/extend",
                key=data["key"],
                data={"hours": op.hours, "currency": data["currency"]},
            )
            if (
                result.get("success") is not True
                or result.get("rental_uid") != rental.kosell_uid
                or result.get("hours_added") != op.hours
            ):
                raise kosell.KoSellError("Результат продления не подтверждён")
            end = kosell.end_time(result["new_expires_at"])
            if end <= kosell.aware(rental.end_time):
                raise kosell.KoSellError("Поставщик не подтвердил новый срок")
            # Extension response price_paid uses the response currency.
            currency = result.get("currency")
            amount = Decimal(str(result.get("price_paid", "NaN")))
            if currency in {"RUB", "USD"} and amount.is_finite() and amount >= 0:
                data["receipt"] = {"currency": currency, "amount": str(amount)}
            op.payload, op.state = packed(data), "DONE"
            rental.end_time = end
            rental.status = "ACTIVE"
            template = "kosell_review_bonus" if op.kind == "review" else "kosell_extended"
            text = await render(
                "kosell",
                template,
                order_context(
                    order, hours=op.hours, expires=end.isoformat(), uid=rental.kosell_uid
                ),
                db=db,
            )
            db.add(
                KosellMessage(
                    rental_id=rental.id,
                    event_key=f"operation:{op.id}",
                    kind="notice",
                    body=kosell.vault.encrypt(text),
                    expires_at=end,
                )
            )
            if op.order_id:
                order.auto_delivered, order.delivery_state = True, "sent"
            await db.commit()
            await kosell.log(f"UID {rental.kosell_uid}: продление +{op.hours} ч подтверждено")
        except Exception:
            await db.rollback()
            op = await db.get(KosellOperation, ident, populate_existing=True)
            order = await db.get(Order, context_order_id, populate_existing=True)
            op.state = "MANUAL_CHECK"
            await db.commit()
            await kosell.log(
                f"Операция #{ident}: результат продления требует ручной сверки, повторное списание заблокировано",
                "error",
            )
            with contextlib.suppress(Exception):
                await notify(
                    "kosell",
                    "kosell_manual_check",
                    order_context(order, reason="Неоднозначный результат продления"),
                )


async def review_bonus():
    config = await kosell.settings()
    if not config["enabled"] or not config["review_bonus"]:
        return
    cursor = await get_setting("kosell:review_cursor", 2147483647)
    async with Session() as db:
        candidates = (
            await db.execute(
                select(KosellRental, Order)
                .join(Order)
                .where(
                    KosellRental.status == "ACTIVE",
                    Order.status == "closed",
                    KosellRental.id < cursor,
                )
                .order_by(KosellRental.id.desc())
                .limit(30)
            )
        ).all()
        ids = [(r.id, o.id) for r, o in candidates]
    await save_setting("kosell:review_cursor", ids[-1][0] if ids else 2147483647)
    for rental_id, order_id in ids:
        async with kosell.claim_lock, Session() as db:
            if await db.scalar(
                select(KosellOperation.id).where(KosellOperation.event_key == f"review:{rental_id}")
            ):
                continue
            rental = await db.get(KosellRental, rental_id)
            order = await db.get(Order, order_id)
            if not rental.end_time or kosell.aware(rental.end_time) <= utcnow():
                continue
            live = await (await accounts.client(order.account_id)).order_details(
                order.funpay_order_id
            )
            review = live.get("review") or {}
            if (
                live.get("status") != "closed"
                or int(live.get("buyer", {}).get("user_id", 0)) != rental.buyer_id
                or int(review.get("rating", 0) or 0) < 5
            ):
                continue
            payload = unpacked(rental.payload)
            op = KosellOperation(
                rental_id=rental.id,
                event_key=f"review:{rental.id}",
                kind="review",
                hours=1,
                payload=packed(
                    {
                        "key": payload["key"],
                        "currency": payload["currency"],
                        "previous_end": kosell.aware(rental.end_time).isoformat(),
                    }
                ),
            )
            db.add(op)
            await db.commit()
            ident = op.id
        await execute(ident)


async def recover():
    async with Session() as db:
        for row in (
            await db.scalars(select(KosellOperation).where(KosellOperation.state == "CLAIMED"))
        ).all():
            row.state = "MANUAL_CHECK"
            await kosell.log(
                f"Операция #{row.id}: прервана при перезапуске, требуется сверка", "warning"
            )
        await db.commit()
