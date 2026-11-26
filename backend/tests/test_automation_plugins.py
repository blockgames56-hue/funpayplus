"""No network: arbitration detection, duplicate protection and delayed reply safety."""

import unittest
from unittest.mock import AsyncMock, patch

from app import automation_plugins, events, message_service, services
from app.events import EventBus
from app.funpay import FunPayError, parse_messages
from app.models import Account, Base, Chat, ChatMessage, TicketEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def event_html(badge: str) -> str:
    return (f'<div class="chat-msg-item"><div class="media-user-name">'
            f'<span class="chat-msg-author-label label label-success">{badge}</span>'
            '<a href="/users/45/">Staff</a></div><div class="chat-msg-text">Помогите с заказом</div></div>')


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_listener_receives_event_once(self):
        bus = EventBus()
        listener = AsyncMock()
        bus.on("new_message", listener)
        bus.on("new_message", listener)
        await bus.emit("new_message", 10, 1, {"text": "пример"})
        listener.assert_awaited_once_with(10, 1, {"text": "пример"})
        bus.off("new_message", listener)
        await bus.emit("new_message", 10, 1, {})
        self.assertEqual(listener.await_count, 1)

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.events_patch = patch.object(events, "Session", self.session)
        self.events_patch.start()
        async with self.session() as db:
            db.add(Account(id=1, title="Sample", golden_key="encrypted", user_agent="Test"))
            db.add(Chat(id=10, account_id=1, funpay_chat_id="users-1-45",
                        interlocutor_id=45, interlocutor_name="Staff"))
            await db.commit()

    async def asyncTearDown(self):
        self.events_patch.stop()
        await self.engine.dispose()

    async def test_arbitration_badge_is_strict(self):
        data = {"node": {"id": "users-1-45"}, "messages": [
            {"id": 11, "author": 45, "html": event_html("Арбитраж")},
            {"id": 12, "author": 45, "html": event_html("Покупатель")},
        ]}
        messages = parse_messages(data, 1)
        self.assertTrue(messages[0]["is_arbitration"])
        self.assertFalse(messages[1]["is_arbitration"])

    async def test_ticket_notified_and_sent_once(self):
        handler = automation_plugins.AutoTicketHandler()
        message = {"is_arbitration": True, "funpay_message_id": 11,
                   "text": "Помогите с заказом"}
        settings = {"auto_ticket": True, "ticket_templates": [
            {"pattern": "заказом", "reply": "Разберёмся", "is_active": True}]}
        with (patch.object(automation_plugins, "Session", self.session),
              patch.object(services, "get_setting", AsyncMock(return_value=settings)),
              patch.object(services.telegram, "notify", AsyncMock()) as notify,
              patch.object(automation_plugins.rate_limiter, "send", AsyncMock()) as send,
              patch.object(automation_plugins.bus, "publish", AsyncMock()),
              patch.object(automation_plugins.bus, "log", AsyncMock())):
            await handler.on_new_message(10, 1, message)
            await handler.on_new_message(10, 1, message)
            notify.assert_awaited_once()
            send.assert_awaited_once_with(1, 10, "Разберёмся")
            async with self.session() as db:
                self.assertEqual(len((await db.scalars(select(TicketEvent))).all()), 1)
                self.assertEqual((await db.scalar(select(TicketEvent))).state, "sent")

    async def test_disabled_account_blocks_queued_send(self):
        async with self.session() as db:
            account = await db.get(Account, 1)
            account.is_active = False
            await db.commit()
        with (patch.object(message_service, "Session", self.session),
              patch.object(services.accounts, "send", AsyncMock()) as send):
            with self.assertRaises(FunPayError):
                await automation_plugins.AccountRateLimiter().send(1, 10, "Пример")
            send.assert_not_awaited()

    async def test_delayed_reply_skips_when_operator_responded(self):
        async with self.session() as db:
            db.add(ChatMessage(id=5, chat_id=10, funpay_message_id=11, sender_id=45,
                               sender_name="Staff", text="Привет", is_my=False))
            db.add(ChatMessage(id=6, chat_id=10, funpay_message_id=12, sender_id=1,
                               sender_name="Sample", text="Уже отвечено", is_my=True))
            await db.commit()
        handler = automation_plugins.AutoResponderHandler()
        with (patch.object(automation_plugins, "Session", self.session),
              patch.object(automation_plugins.random, "uniform", return_value=0),
              patch.object(automation_plugins.asyncio, "sleep", AsyncMock()),
              patch.object(automation_plugins.rate_limiter, "send", AsyncMock()) as send):
            await handler._reply(10, 1, {"sender_id": 45, "local_id": 5}, "Ответ", {})
            send.assert_not_awaited()
