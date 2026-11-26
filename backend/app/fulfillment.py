"""Manual-confirmed Discord boost and Steam gift fulfillment queue."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta, timezone

import httpx
from sqlalchemy import select

from .db import Session
from .events import bus
from .message_templates import notify, order_context, render, send
from .models import Activity, Chat, ChatMessage, FulfillmentTask, Order, utcnow
from .security import vault

SETTINGS_KEY = "fulfillment_plugins"
_confirm_locks: dict[int, asyncio.Lock] = {}


def parse_invite(text: str) -> str | None:
    match = re.search(
        r"https?://(?:www\.)?(?:discord\.gg/|discord(?:app)?\.com/invite/)([A-Za-z0-9-]{2,32})",
        text,
        re.I,
    )
    return match.group(1) if match else None


def parse_steam_target(text: str) -> str | None:
    match = re.search(r"https?://steamcommunity\.com/profiles/(7656119\d{10})/?", text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"https?://steamcommunity\.com/id/([A-Za-z0-9_-]{2,64})/?", text, re.I)
    if match:
        return "id/" + match.group(1)
    match = re.search(r"(?:friend\s*code|код\s*друга)\s*[:#-]?\s*(\d{6,12})\b", text, re.I)
    return match.group(1) if match else None


async def ensure_for_order(order_id: int) -> bool:
    from .services import get_setting

    settings = await get_setting(SETTINGS_KEY, {})
    async with Session() as db:
        order = await db.get(Order, order_id)
        if not order or order.status != "paid" or order.delivery_state != "pending":
            return False
        title = order.description.casefold()
        kind = next(
            (
                name
                for name in ("discord_boost", "steam_gift")
                if settings.get(name, {}).get("enabled")
                and settings[name].get("lot_phrase", "").strip().casefold() in title
                and settings[name].get("lot_phrase", "").strip()
            ),
            None,
        )
        if not kind:
            return False
        task = await db.scalar(select(FulfillmentTask).where(FulfillmentTask.order_id == order_id))
        if not task:
            task = FulfillmentTask(
                order_id=order_id,
                kind=kind,
                boost_count=int(settings.get("discord_boost", {}).get("boost_count", 1)),
            )
            db.add(task)
        chat = None
        if not task.target:
            chat = await db.scalar(
                select(Chat).where(
                    Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
                )
            )
            if chat:
                messages = (
                    await db.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.chat_id == chat.id, ChatMessage.is_my.is_(False))
                        .order_by(ChatMessage.id.desc())
                        .limit(20)
                    )
                ).all()
                for message in messages:
                    sent_at = (
                        message.created_at
                        if message.created_at.tzinfo
                        else message.created_at.replace(tzinfo=timezone.utc)
                    )
                    if utcnow() - sent_at > timedelta(minutes=10):
                        continue
                    target = (
                        parse_invite(message.text)
                        if kind == "discord_boost"
                        else parse_steam_target(message.text)
                    )
                    if target:
                        task.target = target
                        task.status = "ready"
                        break
        order.delivery_state = "external_pending"
        await db.commit()
    if chat:
        await send(
            kind.replace("_", "-"),
            "discord_start" if kind == "discord_boost" else "gift_start",
            chat.id,
            order_context(order),
        )
    await bus.publish("orders", {"event": "FULFILLMENT_TASK", "order_id": order_id})
    return True


async def on_new_message(chat_id: int, account_id: int, message: dict):
    if message.get("is_my") or not message.get("text"):
        return
    async with Session() as db:
        chat = await db.get(Chat, chat_id)
        if not chat or chat.account_id != account_id or not chat.current_order_id:
            return
        order = await db.scalar(
            select(Order).where(
                Order.funpay_order_id == chat.current_order_id, Order.account_id == account_id
            )
        )
        if not order:
            return
        task = await db.scalar(
            select(FulfillmentTask).where(
                FulfillmentTask.order_id == order.id, FulfillmentTask.status == "waiting_target"
            )
        )
        if not task:
            return
        target = (
            parse_invite(message["text"])
            if task.kind == "discord_boost"
            else parse_steam_target(message["text"])
        )
        if not target:
            return
        task.target = target
        task.status = "ready"
        await db.commit()
    await bus.publish("orders", {"event": "FULFILLMENT_TARGET", "order_id": order.id})


async def verify_invite(task_id: int) -> str:
    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        if not task or task.kind != "discord_boost" or not task.target:
            raise ValueError("Приглашение не указано")
        code = task.target
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(f"https://discord.com/api/v10/invites/{code}")
    if response.status_code != 200:
        raise ValueError("Discord не подтвердил приглашение")
    data = response.json()
    guild_id = str(data.get("guild", {}).get("id", ""))
    if not guild_id.isdigit():
        raise ValueError("Discord не вернул ID сервера")
    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        task.guild_id = guild_id
        await db.commit()
    return guild_id


async def mark_friend_requested(task_id: int):
    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        if (
            not task
            or task.kind != "steam_gift"
            or not task.target
            or task.status not in {"ready", "attention"}
        ):
            raise ValueError("Сначала получите корректный код друга или ссылку")
        task.status = "friend_requested"
        task.friend_requested_at = utcnow()
        order = await db.get(Order, task.order_id)
        chat = await db.scalar(
            select(Chat).where(
                Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
            )
        )
        await db.commit()
    if chat:
        await send("steam-gift", "gift_friend_request", chat.id, order_context(order))


async def set_target(task_id: int, value: str):
    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        if not task or task.status not in {"waiting_target", "ready"}:
            raise ValueError("Задача не принимает новые данные покупателя")
        target = parse_invite(value) if task.kind == "discord_boost" else parse_steam_target(value)
        if not target:
            raise ValueError("Не удалось распознать приглашение или код друга")
        task.target = target
        task.status = "ready"
        task.guild_id = None
        await db.commit()


async def report_issue(task_id, key):
    """Operator reports a real delivery problem, without claiming a purchase."""
    from .models import Settings

    async with _confirm_locks.setdefault(task_id, asyncio.Lock()):
        async with Session() as db:
            task = await db.get(FulfillmentTask, task_id)
            if not task or task.status in {"completed", "confirming", "uncertain"}:
                raise ValueError("В этом состоянии нельзя сообщить об ошибке")
            allowed = {
                "discord_boost": {"discord_invalid_invite", "discord_no_tokens"},
                "steam_gift": {"gift_region_error"},
            }
            if key not in allowed[task.kind]:
                raise ValueError("Неизвестная ошибка выдачи")
            order = await db.get(Order, task.order_id)
            if order.status not in {"paid", "closed"}:
                raise ValueError("Заказ отменён или возвращён")
            chat = await db.scalar(
                select(Chat).where(
                    Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
                )
            )
            if not chat:
                raise ValueError("Чат не найден")
            setting_key = f"fulfillment:report:{task.id}:{key}"
            if await db.get(Settings, setting_key):
                raise ValueError("Уведомление уже отправлялось; проверьте чат перед повтором")
            db.add(Settings(key=setting_key, value="true"))
            task.status = "waiting_target" if key == "discord_invalid_invite" else "attention"
            if key == "discord_invalid_invite":
                task.target, task.guild_id = None, None
            text = await render(task.kind.replace("_", "-"), key, order_context(order), db=db)
            await db.commit()
        from .services import accounts

        await accounts.send(chat.id, text)


async def check_pending():
    from .services import get_setting

    settings = await get_setting(SETTINGS_KEY, {})
    steam = settings.get("steam_gift", {})
    key = vault.decrypt(steam["api_key"]) if steam.get("api_key") else ""
    seller_id = steam.get("seller_steamid", "")
    async with Session() as db:
        tasks = (
            await db.scalars(
                select(FulfillmentTask).where(
                    FulfillmentTask.kind == "steam_gift",
                    FulfillmentTask.status == "friend_requested",
                )
            )
        ).all()
        snapshot = [
            (task.id, task.target, task.friend_requested_at, task.alerted_at, task.order_id)
            for task in tasks
        ]
    friends = set()
    if (
        key
        and re.fullmatch(r"7656119\d{10}", seller_id)
        and any(re.fullmatch(r"7656119\d{10}", target or "") for _, target, _, _, _ in snapshot)
    ):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    "https://api.steampowered.com/ISteamUser/GetFriendList/v1/",
                    params={"key": key, "steamid": seller_id, "relationship": "friend"},
                )
            response.raise_for_status()
            friends = {
                str(row.get("steamid"))
                for row in response.json().get("friendslist", {}).get("friends", [])
                if row.get("relationship") == "friend"
            }
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    for task_id, target, requested_at, alerted_at, order_id in snapshot:
        if target in friends:
            async with Session() as db:
                task = await db.get(FulfillmentTask, task_id)
                if task and task.status == "friend_requested":
                    task.status = "ready_to_gift"
                    await db.commit()
            await bus.publish("orders", {"event": "STEAM_FRIEND_ACCEPTED", "order_id": order_id})
        elif (
            requested_at
            and not alerted_at
            and utcnow()
            - (requested_at if requested_at.tzinfo else requested_at.replace(tzinfo=timezone.utc))
            >= timedelta(minutes=10)
        ):
            async with Session() as db:
                task = await db.get(FulfillmentTask, task_id)
                order = await db.get(Order, order_id)
                if not task or task.alerted_at:
                    continue
                task.alerted_at = utcnow()
                task.status = "attention"
                number = order.funpay_order_id if order else str(order_id)
                await db.commit()
            try:
                chat = None
                if order:
                    async with Session() as db:
                        chat = await db.scalar(
                            select(Chat).where(
                                Chat.account_id == order.account_id,
                                Chat.interlocutor_id == order.buyer_id,
                            )
                        )
                if chat:
                    await send("steam-gift", "gift_timeout", chat.id, order_context(order))
                await notify("steam-gift", "gift_admin_timeout", {"order_id": number})
            except Exception:
                await bus.log(
                    "orders",
                    f"Заказ №{number}: не удалось отправить Telegram-алерт",
                    level="error",
                    source_plugin="steam-gift",
                )


async def confirm_sent(task_id: int):
    async with _confirm_locks.setdefault(task_id, asyncio.Lock()):
        await _confirm_sent(task_id)


async def _confirm_sent(task_id: int):
    from .funpay import FunPayError
    from .services import accounts, get_setting

    settings = await get_setting(SETTINGS_KEY, {})

    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        if not task or task.status not in {"ready", "ready_to_gift", "attention"}:
            raise ValueError("Задача не готова к подтверждению")
        if task.kind == "discord_boost" and not task.guild_id:
            raise ValueError("Сначала проверьте приглашение Discord")
        order = await db.get(Order, task.order_id)
        if not order or order.status != "paid":
            raise ValueError("Заказ больше не оплачен")
        chat = await db.scalar(
            select(Chat).where(
                Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
            )
        )
        if not chat:
            raise ValueError("Диалог покупателя не найден")
        task.status = "confirming"
        order.delivery_state = "sending"
        await db.commit()
        chat_id = chat.id
        defaults = {"steam_gift": "Подарок отправлен!", "discord_boost": "Бусты активированы!"}
        legacy = settings.get(task.kind, {}).get("success_message")
        text = await render(
            task.kind.replace("_", "-"),
            "discord_success" if task.kind == "discord_boost" else "gift_success",
            order_context(order, count=task.boost_count),
            db=db,
            legacy=legacy if legacy != defaults[task.kind] else None,
        )
    try:
        await accounts.send(chat_id, text)
    except FunPayError:
        async with Session() as db:
            task = await db.get(FulfillmentTask, task_id)
            order = await db.get(Order, task.order_id)
            task.status = "uncertain"
            order.delivery_state = "uncertain"
            await db.commit()
        raise
    async with Session() as db:
        task = await db.get(FulfillmentTask, task_id)
        order = await db.get(Order, task.order_id)
        task.status = "completed"
        task.completed_at = utcnow()
        order.auto_delivered = True
        order.delivery_state = "sent_manual"
        db.add(
            Activity(
                channel="orders",
                text=f"Выдача заказа #{order.funpay_order_id} подтверждена",
                account_id=order.account_id,
                source_plugin=task.kind.replace("_", "-"),
            )
        )
        await db.commit()
    await bus.publish("orders", {"event": "FULFILLMENT_COMPLETE", "order_id": order.id})


async def resolve_sent(task_id: int):
    """Owner verified the result after an ambiguous chat write; never resend."""
    async with _confirm_locks.setdefault(task_id, asyncio.Lock()):
        async with Session() as db:
            task = await db.get(FulfillmentTask, task_id)
            if not task or task.status != "uncertain":
                raise ValueError("Ручная сверка доступна только для неопределённой выдачи")
            order = await db.get(Order, task.order_id)
            if not order or order.status not in {"paid", "closed"}:
                raise ValueError("Заказ отменён или возвращён")
            task.status, task.completed_at = "completed", utcnow()
            order.auto_delivered, order.delivery_state = True, "sent_manual"
            await db.commit()
    await bus.publish("orders", {"event": "FULFILLMENT_COMPLETE", "order_id": order.id})
