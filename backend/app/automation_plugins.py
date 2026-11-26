"""Opt-in message and arbitration handlers attached to the existing chat poller."""

from __future__ import annotations

import asyncio
import random

import regex
from sqlalchemy import select

from .blacklist import is_blocked
from .db import Session
from .events import bus

# Compatibility exports for archived integrations; implementation belongs to core.
from .message_service import AccountRateLimiter, rate_limiter  # noqa: F401
from .models import Chat, ChatMessage, TicketEvent


class AutoResponderHandler:
    def __init__(self):
        self.pending: dict[int, asyncio.Task] = {}

    async def on_new_message(self, chat_id: int, account_id: int, message: dict, reply: str, settings: dict):
        if chat_id in self.pending and not self.pending[chat_id].done():
            return  # One queued answer per conversation.
        task = asyncio.create_task(
            self._reply(chat_id, account_id, message, reply, settings),
            name=f"auto-reply-{chat_id}",
        )
        self.pending[chat_id] = task
        task.add_done_callback(lambda done: self._finished(chat_id, done))

    def _finished(self, chat_id: int, task: asyncio.Task):
        if self.pending.get(chat_id) is task:
            self.pending.pop(chat_id, None)
        if not task.cancelled():
            # Retrieve failures even if logging itself failed, avoiding orphan task errors.
            task.exception()

    async def _reply(self, chat_id: int, account_id: int, message: dict, reply: str, settings: dict):
        from .funpay import FunPayError

        try:
            await asyncio.sleep(random.uniform(settings.get("reply_delay_min", 2), settings.get("reply_delay_max", 5)))
            # FunPay's supported protocol does not expose a verified typing action.
            # The reading time is applied locally and no fictitious typing request is sent.
            await asyncio.sleep(min(12, max(1, len(reply) / 12)))
            async with Session() as db:
                chat = await db.get(Chat, chat_id)
                if not chat or chat.account_id != account_id or await is_blocked(
                    db, account_id=account_id, user_id=message["sender_id"], chat_id=chat_id
                ):
                    return
                newer = await db.scalar(
                    select(ChatMessage.id).where(
                        ChatMessage.chat_id == chat_id,
                        ChatMessage.is_my.is_(True),
                        ChatMessage.id > message["local_id"],
                    ).limit(1)
                )
                if newer:
                    return  # Operator already answered.
            await rate_limiter.send(account_id, chat_id, reply)
            await bus.log("chats", "Автоответ отправлен", account_id, source_plugin="responder")
        except FunPayError as error:
            await bus.log("chats", f"Автоответ не отправлен: {error}", account_id, "error", source_plugin='responder')

    async def stop(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()


class AutoTicketHandler:
    """Handle only messages explicitly marked as arbitration by FunPay markup."""

    async def on_new_message(self, chat_id: int, account_id: int, message: dict):
        from .funpay import FunPayError
        from .services import get_setting, telegram

        settings = await get_setting("plugins", {})
        if not settings.get("auto_ticket") or not message.get("is_arbitration"):
            return
        async with Session() as db:
            if await db.scalar(
                select(TicketEvent.id).where(
                    TicketEvent.account_id == account_id,
                    TicketEvent.funpay_message_id == message["funpay_message_id"],
                )
            ):
                return
            ticket = TicketEvent(account_id=account_id, chat_id=chat_id,
                                 funpay_message_id=message["funpay_message_id"], state="seen")
            db.add(ticket)
            await db.commit()  # No blind re-send after restart or ambiguous timeout.
            ticket_id = ticket.id
        await bus.publish("tickets", {"account_id": account_id, "chat_id": chat_id})
        await telegram.notify("tickets", f"Арбитраж: {message['text'][:3000]}", chat_id=chat_id)
        for rule in settings.get("ticket_templates", []):
            if not rule.get("is_active", True):
                continue
            try:
                matched = regex.search(rule["pattern"], message["text"][:10000], regex.I, timeout=0.05)
            except (regex.error, TimeoutError):
                continue
            if matched:
                await self.set_state(ticket_id, "sending")
                try:
                    await rate_limiter.send(account_id, chat_id, rule["reply"])
                except FunPayError as error:
                    await self.set_state(ticket_id, "uncertain")
                    await bus.log("tickets", f"Проверьте ответ в арбитраже: {error}", account_id, "error", source_plugin='auto-ticket')
                else:
                    await self.set_state(ticket_id, "sent")
                    await bus.log("tickets", "Шаблон ответа отправлен в арбитраж", account_id, source_plugin='auto-ticket')
                break

    async def set_state(self, ticket_id: int, state: str):
        async with Session() as db:
            ticket = await db.get(TicketEvent, ticket_id)
            if ticket:
                ticket.state = state
                await db.commit()


responder = AutoResponderHandler()
auto_ticket = AutoTicketHandler()


async def handle_new_message(chat_id: int, account_id: int, message: dict):
    if message.get("is_arbitration"):
        await auto_ticket.on_new_message(chat_id, account_id, message)
    else:
        from .services import automation

        await automation.respond(chat_id, message)
