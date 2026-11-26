"""Offline tests for external delivery queue and alert timing."""

import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import fulfillment, message_templates, services
from app.models import Account, Base, Chat, ChatMessage, FulfillmentTask, Order, utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ParsingTests(unittest.TestCase):
    def test_invite_and_steam_target(self):
        self.assertEqual(fulfillment.parse_invite("https://discord.gg/abc1234"), "abc1234")
        self.assertEqual(fulfillment.parse_steam_target("Код друга: 12345678"), "12345678")
        self.assertEqual(fulfillment.parse_steam_target("https://steamcommunity.com/profiles/76561198000000000"), "76561198000000000")
        self.assertIsNone(fulfillment.parse_invite("https://discord.gg.evil.example/abc"))


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fulfillment-test-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patch_session = patch.object(fulfillment, "Session", self.session)
        self.patch_session.start()
        self.template_patch = patch.object(message_templates, "Session", self.session)
        self.template_patch.start()
        self.send_patch = patch.object(services.accounts, "send", AsyncMock())
        self.send_patch.start()
        async with self.session() as db:
            account = Account(title="Seller", golden_key="encrypted", funpay_id=1, username="Seller", user_agent="test")
            db.add(account)
            await db.flush()
            order = Order(account_id=account.id, funpay_order_id="TEST-1", description="Steam Gift Game", sum=Decimal("10"), currency="RUB", buyer_id=42, buyer_name="Buyer", status="paid")
            db.add(order)
            await db.flush()
            chat = Chat(account_id=account.id, funpay_chat_id="users-1-42", interlocutor_id=42, interlocutor_name="Buyer", current_order_id="TEST-1")
            db.add(chat)
            await db.flush()
            db.add(ChatMessage(chat_id=chat.id, sender_id=42, sender_name="Buyer", text="Код друга: 12345678"))
            await db.commit()
            self.order_id, self.chat_id = order.id, chat.id

    async def asyncTearDown(self):
        self.patch_session.stop()
        self.template_patch.stop()
        self.send_patch.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_paid_order_enters_queue_and_skips_plain_delivery(self):
        settings = {"steam_gift": {"enabled": True, "lot_phrase": "Steam Gift"}}
        with (patch.object(services, "get_setting", AsyncMock(return_value=settings)), patch.object(fulfillment.bus, "publish", AsyncMock())):
            self.assertTrue(await fulfillment.ensure_for_order(self.order_id))
        async with self.session() as db:
            order = await db.get(Order, self.order_id)
            task = await db.scalar(select(FulfillmentTask).where(FulfillmentTask.order_id == self.order_id))
            self.assertEqual(order.delivery_state, "external_pending")
            self.assertEqual(task.target, "12345678")
            self.assertEqual(task.status, "ready")

    async def test_ten_minute_alert_is_sent_once(self):
        async with self.session() as db:
            task = FulfillmentTask(order_id=self.order_id, kind="steam_gift", status="friend_requested", target="12345678", friend_requested_at=utcnow() - timedelta(minutes=11))
            db.add(task)
            await db.commit()
        with (patch.object(services, "get_setting", AsyncMock(return_value={})), patch.object(services.telegram, "notify", AsyncMock()) as notify):
            await fulfillment.check_pending()
            await fulfillment.check_pending()
            notify.assert_awaited_once()
            self.assertIn("TEST-1", notify.call_args.args[1])

    async def test_friend_list_marks_task_ready_to_gift(self):
        async with self.session() as db:
            db.add(FulfillmentTask(order_id=self.order_id, kind="steam_gift", status="friend_requested", target="76561198000000000", friend_requested_at=utcnow()))
            await db.commit()
        original = httpx.AsyncClient
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"friendslist": {"friends": [{"steamid": "76561198000000000", "relationship": "friend"}]}}))
        settings = {"steam_gift": {"api_key": fulfillment.vault.encrypt("test-key"), "seller_steamid": "76561198000000001"}}
        with (patch.object(services, "get_setting", AsyncMock(return_value=settings)),
              patch.object(fulfillment.httpx, "AsyncClient", side_effect=lambda **kwargs: original(transport=transport, **kwargs)),
              patch.object(fulfillment.bus, "publish", AsyncMock())):
            await fulfillment.check_pending()
        async with self.session() as db:
            task = await db.scalar(select(FulfillmentTask).where(FulfillmentTask.order_id == self.order_id))
            self.assertEqual(task.status, "ready_to_gift")

    async def test_success_message_only_after_manual_confirmation(self):
        async with self.session() as db:
            task = FulfillmentTask(order_id=self.order_id, kind="steam_gift", status="ready", target="12345678")
            db.add(task)
            await db.commit()
            task_id = task.id
        with (patch.object(services, "get_setting", AsyncMock(return_value={})), patch.object(services.accounts, "send", AsyncMock()) as send, patch.object(fulfillment.bus, "publish", AsyncMock())):
            await fulfillment.confirm_sent(task_id)
            send.assert_awaited_once_with(self.chat_id, message_templates.CATALOG["steam-gift", "gift_success"].default)
        async with self.session() as db:
            order = await db.get(Order, self.order_id)
            self.assertTrue(order.auto_delivered)
            self.assertEqual(order.delivery_state, "sent_manual")
