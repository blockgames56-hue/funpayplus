"""Offline integration: real SQLite, real dispatcher, fake network boundary."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import (
    event_pipeline,
    events,
    integrated_jobs,
    kosell,
    kosell_extensions,
    reply_engine,
    services,
    steam_rentals,
)
from app import message_service as automation_plugins
from app.funpay import FunPayError
from app.funpay_gateway import FunPayGateway, normalize_message
from app.models import (
    Account,
    Base,
    Chat,
    ChatMessage,
    IncomingEvent,
    MessageSend,
    ReplyEvent,
    utcnow,
)
from app.reply_config import ReplyConfig, Rule
from FunPayAPI import enums
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class CorePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.url = "sqlite+aiosqlite:///" + (Path(self.tmp.name) / "core.db").as_posix()
        self.engine = create_async_engine(self.url)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.patches = [
            patch.object(module, "Session", self.session)
            for module in (services, event_pipeline, reply_engine, events, automation_plugins,
                           integrated_jobs, kosell, kosell_extensions, steam_rentals)
        ]
        self.client = AsyncMock()
        self.client.user_id = 7
        self.client.send_message.return_value = {"message_id": 100, "objects": []}
        self.patches += [
            patch.object(services.accounts, "client", AsyncMock(return_value=self.client)),
            patch.object(services.telegram, "notify", AsyncMock()),
            patch.object(automation_plugins.rate_limiter, "interval", 0),
        ]
        for item in self.patches:
            item.start()
        services.accounts.send_locks.clear()
        services.accounts.ingest_locks.clear()
        automation_plugins.rate_limiter.locks.clear()
        automation_plugins.rate_limiter.next_at.clear()
        event_pipeline.dispatch_lock = asyncio.Lock()
        reply_engine.lock, reply_engine.send_lock = asyncio.Lock(), asyncio.Lock()
        async with self.session() as db:
            db.add(
                Account(
                    id=1,
                    title="Seller",
                    username="Seller",
                    funpay_id=7,
                    golden_key="fake-cipher",
                    user_agent="offline-test",
                )
            )
            await db.commit()
        await services.save_setting("plugins", {"responder": True})
        await self.configure(
            commands=[
                Rule(
                    id="hello", triggers=["!hello"], reply="Hi {buyer_name}", match="exact"
                ).model_dump()
            ]
        )

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def configure(self, **kwargs):
        value = ReplyConfig(greeting_enabled=False, **kwargs).model_dump()
        value.update(version=2, enabled_at=utcnow().isoformat())
        await services.save_setting("responder:v2", value)

    async def incoming(self, text="!hello", ident=10, author=2, initial=False, confirmed_new=True):
        message = normalize_message(
            SimpleNamespace(
                id=ident, chat_id=42, author_id=author, author="Buyer", text=text, image_link=None
            ),
            7,
        )
        await services.accounts.ingest(
            1,
            [{"type": "chat_node", "data": {
                "normalized_messages": [message],
                "confirmed_new_ids": [ident] if confirmed_new else [],
                "initial": initial,
                "reconcile": not initial,
                "history_checked": True,
            }}],
            automate=True, confirmed_new=confirmed_new,
        )

    async def flush(self):
        async with self.session() as db:
            for row in (await db.scalars(select(ReplyEvent))).all():
                row.due_at = utcnow() - timedelta(seconds=1)
            await db.commit()
        await reply_engine.flush()

    async def test_command_restart_and_duplicate_single_send(self):
        await self.engine.dispose()  # New DB connections must load persisted configuration.
        await reply_engine.recover()
        await self.incoming()
        await self.flush()
        await self.incoming()
        await event_pipeline.dispatch()
        await self.flush()
        self.client.send_message.assert_awaited_once_with("42", "Hi Buyer")
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(IncomingEvent))).state, "PROCESSED")
            self.assertEqual((await db.scalar(select(MessageSend))).state, "SENT")

    async def test_native_runner_poll_reaches_inbox_and_one_confirmed_reply(self):
        """Exercise the production poll shape across the durable core boundary.

        FunPay transport stays mocked: this is intentionally an offline P0
        acceptance test and never creates a real remote message.
        """
        gateway = FunPayGateway("offline-not-a-real-key")
        self.addAsyncCleanup(gateway.close)
        gateway.user_id = 7
        gateway.refreshed_at = monotonic()
        message = SimpleNamespace(
            id=10, chat_id=42, chat_name="Buyer", author_id=2, author="Buyer",
            text="!hello", image_link=None, html="",
        )
        runner = Mock()
        runner.parse_updates.return_value = [
            SimpleNamespace(type=enums.EventTypes.NEW_MESSAGE, message=message)
        ]
        gateway.api_runner = runner
        gateway.api_account.get_chats_histories = Mock(return_value={42: [message]})

        result = await gateway.poll([], {})
        await services.accounts.ingest(
            1, result["objects"], automate=True, confirmed_new=True
        )
        await self.flush()
        # Replaying the same Runner payload after a process restart cannot add
        # another IncomingEvent, ReplyEvent, or network send.
        result = await gateway.poll([], {"42": 10})
        await services.accounts.ingest(
            1, result["objects"], automate=True, confirmed_new=True
        )
        await event_pipeline.dispatch()
        await self.flush()

        self.client.send_message.assert_awaited_once_with("42", "Hi Buyer")
        async with self.session() as db:
            self.assertEqual(len((await db.scalars(select(ChatMessage))).all()), 1)
            self.assertEqual(len((await db.scalars(select(IncomingEvent))).all()), 1)
            self.assertEqual(len((await db.scalars(select(ReplyEvent))).all()), 1)
            self.assertEqual((await db.scalar(select(IncomingEvent))).state, "PROCESSED")

    async def test_history_before_runner_new_event_does_not_lose_command(self):
        await self.incoming(initial=True, confirmed_new=False)
        await self.incoming(confirmed_new=True)
        await self.flush()
        await self.incoming(confirmed_new=True)
        await self.flush()
        self.client.send_message.assert_awaited_once_with("42", "Hi Buyer")

    async def test_unconfirmed_history_establishes_cursor_without_answer(self):
        await self.incoming(ident=10, initial=True, confirmed_new=False)
        await self.flush()
        self.client.send_message.assert_not_awaited()
        async with self.session() as db:
            chat = await db.scalar(select(Chat))
            event = await db.scalar(select(IncomingEvent))
            self.assertEqual(chat.last_message_cursor, 10)
            self.assertEqual(event.state, "IGNORED")
            self.assertEqual(event.reason, "initial_or_history")

    async def test_durable_reconcile_answers_only_message_above_cursor(self):
        await self.incoming(ident=10, initial=True, confirmed_new=False)
        await self.incoming("!hello", ident=11, confirmed_new=False)
        await self.flush()
        self.client.send_message.assert_awaited_once_with("42", "Hi Buyer")

    async def test_history_and_own_messages_never_answer(self):
        await self.incoming(initial=True)
        await self.incoming(ident=11, author=7)
        await self.flush()
        self.client.send_message.assert_not_awaited()
        async with self.session() as db:
            rows = (await db.scalars(select(IncomingEvent))).all()
            self.assertEqual([r.reason for r in rows], ["initial_or_history", "own_message"])

    async def test_disabled_responder_marks_new_event_as_ignored(self):
        await services.save_setting("plugins", {"responder": False})
        await self.incoming()
        await self.flush()
        self.client.send_message.assert_not_awaited()
        async with self.session() as db:
            row = await db.scalar(select(IncomingEvent))
            self.assertEqual((row.state, row.reason), ("IGNORED", "responder_disabled"))
        await services.save_setting("plugins", {"responder": True})
        await event_pipeline.dispatch()
        await self.flush()
        self.client.send_message.assert_not_awaited()

    async def test_plugin_command_works_with_responder_disabled(self):
        await services.save_setting("plugins", {"responder": False})
        with patch.object(integrated_jobs, "handle_message", AsyncMock(return_value=True)) as plugin:
            await self.incoming("!код")
        plugin.assert_awaited_once()
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(IncomingEvent))).state, "PROCESSED")
            self.assertIsNone(await db.scalar(select(ReplyEvent)))

    async def test_kosell_command_works_with_responder_disabled(self):
        await services.save_setting("plugins", {"responder": False})
        with (patch.object(integrated_jobs, "handle_message", AsyncMock(return_value=False)),
              patch.object(kosell, "handle_message", AsyncMock(return_value=True)) as plugin):
            await self.incoming("!time")
        plugin.assert_awaited_once()
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(IncomingEvent))).state, "PROCESSED")

    async def test_background_order_scan_does_not_block_new_command(self):
        reply_engine.observe_lock = asyncio.Lock()
        async with reply_engine.observe_lock:
            await asyncio.wait_for(self.incoming(), timeout=5)
        self.client.send_message.assert_awaited_once_with("42", "Hi Buyer")

    async def test_similar_command_does_not_match(self):
        await self.incoming("!hello2")
        await self.flush()
        self.client.send_message.assert_not_awaited()

    async def test_unknown_delivery_is_never_retried(self):
        self.client.send_message.side_effect = TimeoutError("secret must not appear in logs")
        await self.incoming()
        await self.flush()
        await reply_engine.recover()
        await self.incoming()
        await self.flush()
        self.client.send_message.assert_awaited_once()
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(MessageSend))).state, "UNKNOWN")
            self.assertEqual((await db.scalar(select(ReplyEvent))).state, "uncertain")

    async def test_failure_recorded_and_explicit_retry(self):
        with patch.object(reply_engine, "on_message", AsyncMock(side_effect=ValueError("private"))):
            await self.incoming()
        async with self.session() as db:
            row = await db.scalar(select(IncomingEvent))
            self.assertEqual((row.state, row.reason), ("FAILED", "ValueError"))
            ident = row.id
        await event_pipeline.retry_failed(ident)
        await self.flush()
        self.client.send_message.assert_awaited_once()

    async def test_keyword_cooldown(self):
        await self.configure(
            keywords=[Rule(id="key", triggers=["online"], reply="Yes").model_dump()]
        )
        await self.incoming("online")
        await self.incoming("online", ident=11)
        await self.flush()
        self.client.send_message.assert_awaited_once_with("42", "Yes")

    async def test_dry_run_never_sends(self):
        await self.configure(
            dry_run=True, commands=[Rule(id="cmd", triggers=["hello"], reply="Hello").model_dump()]
        )
        await self.incoming()
        await self.flush()
        self.client.send_message.assert_not_awaited()
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(ReplyEvent))).state, "dry_run")

    async def test_manual_idempotency_and_payload_conflict(self):
        await self.incoming(initial=True)
        await services.accounts.send(1, "manual", idempotency_key="manual-1")
        await services.accounts.send(1, "manual", idempotency_key="manual-1")
        with self.assertRaises(FunPayError):
            await services.accounts.send(1, "changed", idempotency_key="manual-1")
        self.client.send_message.assert_awaited_once()

    async def test_dispatch_survives_crash_after_message_commit(self):
        with patch.object(event_pipeline, "dispatch", AsyncMock()):
            await self.incoming()
        await self.engine.dispose()
        await event_pipeline.recover()
        await event_pipeline.dispatch()
        await self.flush()
        self.client.send_message.assert_awaited_once()

    async def test_blacklist_preserves_inbox_without_answer(self):
        from app.models import BlacklistEntry

        async with self.session() as db:
            db.add(BlacklistEntry(user_id=2, username="Buyer"))
            await db.commit()
        await self.incoming()
        await self.flush()
        self.client.send_message.assert_not_awaited()
        async with self.session() as db:
            self.assertIsNotNone(await db.scalar(select(ChatMessage)))
            self.assertEqual((await db.scalar(select(IncomingEvent))).reason, "blacklist")


class MatcherTests(unittest.TestCase):
    def test_arguments_alias_case_and_boundary(self):
        rule = Rule(
            id="cmd", triggers=["!help", "помощь"], reply="{args}", allow_arguments=True
        ).model_dump()
        self.assertTrue(reply_engine.matches(rule, "HELP test", command=True))
        self.assertTrue(reply_engine.matches(rule, "помощь", command=True))
        self.assertFalse(reply_engine.matches(rule, "help2", command=True))
        rule["case_sensitive"] = True
        self.assertFalse(reply_engine.matches(rule, "HELP", command=True))
