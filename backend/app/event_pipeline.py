"""Single-process durable dispatcher. Never replays historical messages."""

import asyncio

from sqlalchemy import select

from .blacklist import is_blocked
from .db import Session
from .events import bus
from .models import Account, Chat, ChatMessage, IncomingEvent

dispatch_lock = asyncio.Lock()


async def recover():
    async with Session() as db:
        for row in (
            await db.scalars(select(IncomingEvent).where(IncomingEvent.state == "PROCESSING"))
        ).all():
            # The command engine only enqueues durable idempotent replies.
            row.state = "RECEIVED"
        await db.commit()


async def dispatch():
    from . import integrated_jobs, kosell, kosellrent
    from .reply_engine import on_message

    async with dispatch_lock, Session() as db:
        rows = (
            await db.scalars(
                select(IncomingEvent)
                .where(IncomingEvent.state == "RECEIVED")
                .order_by(IncomingEvent.id)
                .limit(100)
            )
        ).all()
        for event in rows:
            message = await db.get(ChatMessage, event.message_id)
            chat = await db.get(Chat, event.chat_id)
            account = await db.get(Account, event.account_id)
            reason = None
            if not message or not account or not account.is_active:
                reason = "account_inactive_or_message_missing"
            elif message.is_my or message.sender_id == account.funpay_id:
                reason = "own_message"
            elif not message.funpay_message_id or (
                not message.sender_id and not kosellrent.is_review(message.text)
            ):
                reason = "missing_identity"
            elif not chat or await is_blocked(
                db,
                account_id=event.account_id,
                user_id=message.sender_id or chat.interlocutor_id,
                chat_id=event.chat_id,
            ):
                reason = "blacklist"
            if reason:
                event.state, event.reason = "IGNORED", reason
                await db.commit()
                continue
            event.state = "PROCESSING"
            event.attempts += 1
            await db.commit()
            try:
                payload = {
                    "sender_id": message.sender_id,
                    "sender_name": message.sender_name,
                    "text": message.text,
                    "is_my": message.is_my,
                    "local_id": message.id,
                    "funpay_message_id": message.funpay_message_id,
                }
                # Команды плагинов отвечают напрямую, без очереди автоответчика,
                # поэтому обрабатываем их первыми.
                handled = await kosellrent.handle_message(
                    event.chat_id,
                    event.account_id,
                    payload,
                    system=not message.sender_id,
                )
                if not message.sender_id:
                    from . import reply_engine, steam_rentals

                    await steam_rentals.on_review_message(event.chat_id, event.account_id)
                    async with reply_engine.observe_lock:
                        await reply_engine.observe_orders(
                            account_id=event.account_id, buyer_id=chat.interlocutor_id
                        )
                    await reply_engine.flush()
                    event.state, event.reason = (
                        "PROCESSED" if handled else "IGNORED",
                        "system_review",
                    )
                    await db.commit()
                    continue
                if not handled:
                    handled = await integrated_jobs.handle_message(
                        event.chat_id, event.account_id, payload
                    )
                if not handled:
                    handled = await kosell.handle_message(event.chat_id, event.account_id, payload)
                if handled:
                    event.state, event.reason = "PROCESSED", None
                    await db.commit()
                    continue
                from .services import get_setting

                if not (await get_setting("plugins", {})).get("responder"):
                    # Plugin commands have their own switches. Only generic
                    # auto-replies depend on the responder's master switch.
                    event.state, event.reason = "IGNORED", "responder_disabled"
                    await db.commit()
                    continue
                await on_message(event.chat_id, payload)
                from .reply_engine import flush

                await flush()
            except Exception as error:
                event.state = "FAILED"
                # Exception strings may contain credentials or remote message bodies.
                event.reason = type(error).__name__
                await db.commit()
                await bus.log(
                    "events", f"Событие #{event.id}: {event.reason}", event.account_id, "error"
                )
            else:
                event.state, event.reason = "PROCESSED", None
                await db.commit()


async def retry_failed(ident: int):
    async with Session() as db:
        event = await db.get(IncomingEvent, ident)
        if not event or event.state != "FAILED":
            raise ValueError("Повтор доступен только для FAILED")
        event.state, event.reason = "RECEIVED", None
        await db.commit()
    await dispatch()
