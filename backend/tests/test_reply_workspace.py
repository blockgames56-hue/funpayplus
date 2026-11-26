"""Isolated account/reply tests; all provider writes are mocked."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import ANY, AsyncMock, patch
from zoneinfo import ZoneInfo

from app import (
    account_workspace as api,
)
from app import (
    reply_ai,
    services,
    workers,
)
from app import (
    reply_config as rc,
)
from app import (
    reply_engine as engine,
)
from app import (
    reply_requests as refunds,
)
from app.message_service import rate_limiter
from app.models import (
    Account,
    Base,
    BlacklistEntry,
    Chat,
    ChatMessage,
    Lot,
    Order,
    ReplyEvent,
    utcnow,
)
from app.security import vault
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dbengine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.dbengine, expire_on_commit=False)
        async with self.dbengine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.client = AsyncMock()
        self.client.order_details.return_value = {
            "status": "paid",
            "buyer": {"user_id": 2},
            "quantity": 1,
        }
        self.send = AsyncMock()
        self.patches = [
            patch.object(m, "Session", self.Session)
            for m in (services, engine, refunds, api, workers)
        ] + [
            patch.object(services.accounts, "client", AsyncMock(return_value=self.client)),
            patch.object(rate_limiter, "send", self.send),
            patch.object(engine, "log", AsyncMock()),
            patch.object(services.telegram, "request", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
        await services.save_setting("plugins", {"responder": True, "auto_thanks": True})
        await services.save_setting(
            "responder:v2",
            {
                **rc.ReplyConfig(greeting_delay=0).model_dump(),
                "version": 2,
                "enabled_at": (utcnow() - timedelta(hours=2)).isoformat(),
            },
        )
        async with self.Session() as db:
            db.add_all(
                [
                    Account(
                        id=1,
                        title="Shop",
                        username="Seller",
                        funpay_id=7,
                        golden_key="cipher",
                        user_agent="Test User Agent",
                    ),
                    Account(
                        id=2,
                        title="Other",
                        username="Other",
                        golden_key="cipher",
                        user_agent="Test User Agent",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    Chat(
                        id=1,
                        account_id=1,
                        funpay_chat_id="chat",
                        interlocutor_id=2,
                        interlocutor_name="Buyer",
                    ),
                    Chat(
                        id=2,
                        account_id=1,
                        funpay_chat_id="second-chat",
                        interlocutor_id=2,
                        interlocutor_name="Buyer",
                    ),
                    Chat(
                        id=3,
                        account_id=1,
                        funpay_chat_id="other-buyer",
                        interlocutor_id=3,
                        interlocutor_name="Other buyer",
                    ),
                    Chat(
                        id=4,
                        account_id=2,
                        funpay_chat_id="other-account-chat",
                        interlocutor_id=2,
                        interlocutor_name="Buyer",
                    ),
                ]
            )
            await db.flush()
            db.add(
                Lot(
                    id=1,
                    account_id=1,
                    funpay_lot_id="10",
                    node_id=3,
                    category_title="Steam",
                    title="Lot",
                    price=100,
                    auto_raise=False,
                )
            )
            db.add(
                Order(
                    id=1,
                    account_id=1,
                    funpay_order_id="ORDER",
                    description="Lot",
                    sum=100,
                    currency="RUB",
                    buyer_id=2,
                    buyer_name="Buyer",
                    status="paid",
                    date_source="observed",
                    created_at=utcnow() - timedelta(minutes=10),
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.dbengine.dispose()

    async def cfg(self, **values):
        cfg = await rc.config()
        cfg.update(values)
        await api.save_responder(rc.ReplyConfig.model_validate(cfg))

    async def incoming(self, text="hello", ident=10, chat_id=1, sender_id=2):
        await engine.on_message(
            chat_id,
            {"sender_id": sender_id, "is_my": False, "text": text, "local_id": ident},
        )

    async def rows(self):
        async with self.Session() as db:
            return (await db.scalars(select(ReplyEvent).order_by(ReplyEvent.id))).all()

    async def due(self):
        async with self.Session() as db:
            for row in (
                await db.scalars(select(ReplyEvent).where(ReplyEvent.state == "pending"))
            ).all():
                row.due_at = utcnow() - timedelta(seconds=1)
            await db.commit()

    async def test_greeting_durable_dedup(self):
        await self.incoming()
        await self.incoming(ident=11)
        await engine.flush()
        await engine.flush()
        self.send.assert_awaited_once()
        self.assertEqual((await self.rows())[0].state, "sent")
        self.assertIn("Buyer", self.send.call_args.args[2])
        self.assertNotIn("Привет", (await self.rows())[0].payload)

    async def test_daily_rolling_window(self):
        await self.cfg(greeting_mode="daily")
        await self.incoming()
        async with self.Session() as db:
            r = await db.get(ReplyEvent, 1)
            r.created_at = utcnow() - timedelta(hours=23)
            await db.commit()
        await self.incoming(ident=11)
        self.assertEqual(len(await self.rows()), 1)

    async def test_command_priority_alias(self):
        r = rc.Rule(
            id="cmd", triggers=["help", "помощь"], reply="Help {buyer_name}", match="exact"
        ).model_dump()
        await self.cfg(
            commands=[r], keywords=[{**r, "id": "key", "triggers": ["help"], "reply": "Keyword"}]
        )
        await self.incoming("!HELP")
        await self.due()
        await engine.flush()
        self.assertEqual((await self.rows())[0].kind, "command")
        self.assertEqual(self.send.call_args.args[2], "Help Buyer")

    async def test_keyword_cooldown(self):
        await self.cfg(
            keywords=[rc.Rule(id="key", triggers=["online"], reply="Yes", cooldown=60).model_dump()]
        )
        await self.incoming("online")
        await self.incoming("online", 11)
        self.assertEqual(len(await self.rows()), 1)

    async def test_account_cooldown_blocks_same_rule_in_another_chat(self):
        await self.cfg(
            keywords=[
                rc.Rule(
                    id="account-rule",
                    triggers=["online"],
                    reply="Yes",
                    cooldown=0,
                    account_cooldown=60,
                ).model_dump()
            ]
        )
        await self.incoming("online")
        await self.incoming("online", 11, chat_id=3, sender_id=3)
        self.assertEqual(len(await self.rows()), 1)

    async def test_buyer_cooldown_blocks_same_buyer_but_not_another_buyer(self):
        await self.cfg(
            keywords=[
                rc.Rule(
                    id="buyer-rule",
                    triggers=["online"],
                    reply="Yes",
                    cooldown=0,
                    buyer_cooldown=60,
                ).model_dump()
            ]
        )
        await self.incoming("online")
        await self.incoming("online", 11, chat_id=2)
        await self.incoming("online", 12, chat_id=3, sender_id=3)
        self.assertEqual(len(await self.rows()), 2)

    def test_rule_schedule_honours_weekdays_and_overnight_periods(self):
        rule = rc.Rule(
            id="night", triggers=["online"], reply="Yes", active_days=[0],
            active_from="22:00", active_until="06:00",
        ).model_dump()
        tz = ZoneInfo("Europe/Tallinn")
        self.assertTrue(engine.rule_active(rule, datetime(2026, 11, 23, 23, 0, tzinfo=tz)))
        self.assertTrue(engine.rule_active(rule, datetime(2026, 11, 24, 1, 0, tzinfo=tz)))
        self.assertFalse(engine.rule_active(rule, datetime(2026, 11, 24, 7, 0, tzinfo=tz)))
        self.assertFalse(engine.rule_active(rule, datetime(2026, 11, 25, 1, 0, tzinfo=tz)))

    def test_rule_schedule_rejects_half_window_and_invalid_day(self):
        with self.assertRaises(ValueError):
            rc.Rule(id="invalid-window", triggers=["online"], reply="Yes", active_from="09:00")
        with self.assertRaises(ValueError):
            rc.Rule(id="invalid-day", triggers=["online"], reply="Yes", active_days=[7])

    async def test_scope_blacklist(self):
        await self.cfg(account_scope="selected", account_ids=[2])
        await self.incoming()
        self.assertFalse(await self.rows())
        await self.cfg(account_scope="all")
        async with self.Session() as db:
            db.add(BlacklistEntry(user_id=2, username="Buyer"))
            await db.commit()
        await self.incoming()
        self.assertFalse(await self.rows())

    async def test_account_scoped_blacklist_does_not_cross_accounts_end_to_end(self):
        async with self.Session() as db:
            db.add(BlacklistEntry(user_id=2, username="Buyer", account_id=1))
            await db.commit()
        await self.incoming("hello", ident=201, chat_id=1)
        await self.incoming("hello", ident=202, chat_id=4)
        events = await self.rows()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].account_id, events[0].chat_id), (2, 4))
        await self.due()
        await engine.flush()
        self.send.assert_awaited_once_with(2, 4, ANY, idempotency_key=ANY)

    async def test_selected_account_scope_sends_only_from_selected_account(self):
        await self.cfg(account_scope="selected", account_ids=[2])
        await self.incoming("hello", ident=301, chat_id=1)
        await self.incoming("hello", ident=302, chat_id=4)
        events = await self.rows()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].account_id, events[0].chat_id), (2, 4))
        await self.due()
        await engine.flush()
        self.send.assert_awaited_once_with(2, 4, ANY, idempotency_key=ANY)

    async def test_operator_cancels_reply(self):
        await self.incoming()
        async with self.Session() as db:
            db.add(
                ChatMessage(
                    id=11,
                    chat_id=1,
                    funpay_message_id=101,
                    sender_name="Seller",
                    sender_id=7,
                    is_my=True,
                    text="Handled",
                )
            )
            await db.commit()
        await engine.flush()
        self.send.assert_not_awaited()
        self.assertEqual((await self.rows())[0].state, "cancelled")

    async def test_master_pause_cancels_pending(self):
        await self.incoming()
        await services.save_setting("plugins", {"responder": False})
        await engine.flush()
        self.send.assert_not_awaited()
        self.assertEqual((await self.rows())[0].state, "cancelled")

    async def test_feature_off_cancels_pending(self):
        await self.incoming()
        await self.cfg(greeting_enabled=False)
        await engine.flush()
        self.send.assert_not_awaited()

    async def test_stopped_account_pauses(self):
        await self.incoming()
        async with self.Session() as db:
            a = await db.get(Account, 1)
            a.is_active = False
            await db.commit()
        await engine.flush()
        self.send.assert_not_awaited()
        self.assertEqual((await self.rows())[0].state, "pending")

    async def test_shared_policy_lot_flags(self):
        result = await api.save_policy(1, rc.AccountPolicy(auto_raise=True, confirmation=True))
        self.assertTrue((await rc.policy(1))["confirmation"])
        self.assertEqual(result["version"], 2)
        async with self.Session() as db:
            self.assertTrue((await db.get(Lot, 1)).auto_raise)

    async def test_manual_lot_override_preserved(self):
        await api.save_policy(1, rc.AccountPolicy(auto_raise=True))
        async with self.Session() as db:
            lot = await db.get(Lot, 1)
            lot.auto_raise = False
            await db.commit()
        await api.save_policy(1, rc.AccountPolicy(auto_raise=True, confirmation=True))
        async with self.Session() as db:
            self.assertFalse((await db.get(Lot, 1)).auto_raise)

    async def test_auto_raise_worker_groups_due_active_lots_only(self):
        async with self.Session() as db:
            primary = await db.get(Lot, 1)
            primary.auto_raise = True
            db.add(
                Lot(
                    id=2,
                    account_id=1,
                    funpay_lot_id="11",
                    node_id=3,
                    category_title="Steam",
                    title="Same category",
                    price=100,
                    auto_raise=True,
                )
            )
            db.add(
                Lot(
                    id=3,
                    account_id=2,
                    funpay_lot_id="12",
                    node_id=4,
                    category_title="Steam",
                    title="Inactive account",
                    price=100,
                    auto_raise=True,
                )
            )
            db.add(
                Lot(
                    id=4,
                    account_id=1,
                    funpay_lot_id="13",
                    node_id=5,
                    category_title="Steam",
                    title="Not due yet",
                    price=100,
                    auto_raise=True,
                    next_raise_at=utcnow() + timedelta(hours=1),
                )
            )
            (await db.get(Account, 2)).is_active = False
            await db.commit()
        with patch.object(workers.accounts, "raise_group", AsyncMock()) as raise_group:
            await workers.WorkerManager().auto_raise()
        raise_group.assert_awaited_once_with(1, 3, 0)

    async def test_paid_dedup_remote_status(self):
        await self.cfg(paid_enabled=True)
        await engine.observe_orders()
        await engine.observe_orders()
        await self.due()
        self.client.order_details.return_value = {"status": "refunded", "buyer": {"user_id": 2}}
        await engine.flush()
        self.send.assert_not_awaited()
        self.assertEqual(len(await self.rows()), 1)
        self.assertEqual((await self.rows())[0].state, "cancelled")

    async def test_history_not_replayed(self):
        await self.cfg(paid_enabled=True)
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.date_source = "history_unknown"
            await db.commit()
        await engine.observe_orders()
        self.assertFalse(await self.rows())

    async def test_confirmation_survives_review_lookup_error(self):
        await api.save_policy(1, rc.AccountPolicy(confirmation=True, review_reply=True))
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.status = "closed"
            await db.commit()
        self.client.order_details.side_effect = TimeoutError()
        await engine.observe_orders()
        self.assertEqual((await self.rows())[0].kind, "confirmation")

    async def test_five_star_review(self):
        await self.cfg(reviews_enabled=True, review_texts={"5": "Great {buyer_name}"})
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.status = "closed"
            await db.commit()
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 2},
            "review": {"rating": 5, "text": "Good"},
        }
        await engine.observe_orders()
        await self.due()
        await engine.flush()
        self.client.reply_review.assert_awaited_once_with("ORDER", "Great Buyer")
        self.send.assert_not_awaited()

    async def test_review_event_bypasses_recent_negative_scan_and_stale_paid_status(self):
        await self.cfg(reviews_enabled=True, review_texts={"5": "Great {buyer_name}"})
        async with self.Session() as db:
            db.add(ReplyEvent(event_key="review-inspect:1", kind="review_inspect", state="ignored", account_id=1, chat_id=1, order_id=1, payload="{}"))
            await db.commit()
        self.client.order_details.return_value = {"status": "closed", "buyer": {"user_id": 2}, "review": {"rating": 5, "text": "Good"}}
        await engine.observe_orders(account_id=1, buyer_id=2)
        await engine.flush()
        await engine.observe_orders(account_id=1, buyer_id=2)
        await engine.flush()
        self.client.reply_review.assert_awaited_once_with("ORDER", "Great Buyer")

    async def test_reminder_rechecks_status(self):
        await api.save_policy(1, rc.AccountPolicy(confirmation_reminder=True, reminder_minutes=5))
        await engine.observe_orders()
        self.client.order_details.return_value = {"status": "closed", "buyer": {"user_id": 2}}
        await engine.flush()
        self.send.assert_not_awaited()

    async def test_missed_reminders_not_burst(self):
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.created_at = utcnow() - timedelta(hours=1)
            await db.commit()
        await api.save_policy(
            1, rc.AccountPolicy(confirmation_reminder=True, reminder_minutes=5, reminder_count=3)
        )
        await engine.observe_orders()
        self.assertFalse(await self.rows())

    async def test_handoff_persistent_and_manual_resume(self):
        await self.cfg(
            greeting_enabled=False, ai_enabled=True, ai_model="example", ai_key="private-test-key"
        )
        with patch.object(
            reply_ai,
            "answer",
            AsyncMock(return_value={"text": "Уточню", "handoff": True, "reason": "Нужен продавец"}),
        ):
            await self.incoming()
            await engine.flush()
        self.assertTrue((await services.get_setting("reply:handoff:1"))["paused"])
        await self.incoming(ident=11)
        self.assertEqual(len(await self.rows()), 1)
        await api.resume_handoff(1)
        await self.incoming(ident=12)
        self.assertEqual(len(await self.rows()), 2)

    async def test_encrypted_ai_secret_retained_on_blank(self):
        await self.cfg(ai_key="private-test-key")
        self.assertEqual((await rc.config())["ai_key"], "")
        self.assertNotIn("private-test-key", str(await services.get_setting("responder:v2")))
        await self.cfg(greeting_delay=3)
        self.assertEqual((await rc.config(True))["ai_key"], "private-test-key")

    async def test_refund_requires_owner_and_dedup(self):
        async with self.Session() as db:
            await refunds.create_request(db, await db.get(Chat, 1), "!возврат #ORDER")
        self.client.refund.assert_not_awaited()
        await refunds.decide(1, "approve")
        self.client.refund.assert_awaited_once_with("ORDER")
        with self.assertRaises(ValueError):
            await refunds.decide(1, "approve")

    async def test_refund_uncertain_no_retry(self):
        async with self.Session() as db:
            await refunds.create_request(db, await db.get(Chat, 1), "!возврат")
        self.client.refund.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            await refunds.decide(1, "approve")
        with self.assertRaises(ValueError):
            await refunds.decide(1, "approve")
        self.client.refund.assert_awaited_once()
        self.assertEqual((await self.rows())[0].state, "uncertain")

    async def test_foreign_telegram_callback_rejected(self):
        await refunds.handle_callback(
            {
                "id": "callback",
                "data": "reply-refund:1:approve",
                "from": {"id": 111},
                "message": {"chat": {"id": 222}},
            },
            {"chat_id": "222", "token": vault.encrypt("test")},
        )
        self.client.refund.assert_not_awaited()

    async def test_validation_tags_stars_prefix(self):
        for c in [
            rc.ReplyConfig(greeting_text="{password}"),
            rc.ReplyConfig(review_texts={"9": "Hi"}),
            rc.ReplyConfig(prefix="abc"),
        ]:
            with self.assertRaises(ValueError):
                rc.validate(c)

    async def test_selected_scope_rejects_duplicate_and_missing_accounts(self):
        with self.assertRaises(ValueError):
            rc.ReplyConfig(account_scope="selected", account_ids=[1, 1])
        with self.assertRaises(ValueError):
            rc.ReplyConfig(account_scope="selected", account_ids=[0])
        with self.assertRaises(HTTPException) as error:
            await api.save_responder(rc.ReplyConfig(account_scope="selected", account_ids=[999]))
        self.assertEqual(error.exception.status_code, 422)

    async def test_escaped_variant_pipe(self):
        self.assertEqual(rc.variants(r"Hello \| there"), "Hello | there")

    async def test_account_metrics_and_secrets_are_safe(self):
        rows = await api.account_rows()
        self.assertEqual(rows[0]["today"]["RUB"]["count"], 1)
        self.assertEqual(rows[0]["orders_in_work"], 1)
        self.assertIsNone(rows[0]["rating"])
        self.assertNotIn("golden_key", rows[0])
        self.assertNotIn("private-test-key", str(await api.responder_workspace()))

    async def test_key_replacement_rejects_different_identity(self):
        self.client.sync_profile.return_value = {"funpay_id": 99, "username": "Foreign"}
        with patch.object(api, "FunPayClient", return_value=self.client):
            with self.assertRaises(HTTPException) as error:
                await api.update_key(1, api.KeyUpdate(golden_key="new_golden_key_123456"))
        self.assertEqual(error.exception.status_code, 409)
        async with self.Session() as db:
            self.assertEqual((await db.get(Account, 1)).golden_key, "cipher")

    async def test_key_replacement_preserves_related_data(self):
        from app.config import config
        from app.main import workers

        self.client.sync_profile.return_value = {"funpay_id": 7, "username": "Seller"}
        with (
            patch.object(api, "FunPayClient", return_value=self.client),
            patch.object(workers, "stop_account", AsyncMock()),
            patch.object(config, "workers_enabled", False),
        ):
            await api.update_key(1, api.KeyUpdate(golden_key="new_golden_key_123456"))
        async with self.Session() as db:
            self.assertEqual(
                vault.decrypt((await db.get(Account, 1)).golden_key), "new_golden_key_123456"
            )
            self.assertIsNotNone(await db.get(Chat, 1))
            self.assertIsNotNone(await db.get(Lot, 1))

    async def test_review_chat_target_does_not_post_public_review(self):
        await self.cfg(
            reviews_enabled=True, review_target="chat", review_texts={"4": "Thanks {stars}"}
        )
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.status = "closed"
            await db.commit()
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 2},
            "review": {"rating": 4, "text": "Good"},
        }
        await engine.observe_orders()
        await self.due()
        await engine.flush()
        self.send.assert_awaited_once_with(1, 1, "Thanks 4", idempotency_key="reply:review:1")
        self.client.reply_review.assert_not_awaited()

    async def test_review_scope_excludes_other_accounts(self):
        await self.cfg(reviews_enabled=True, account_scope="selected", account_ids=[2])
        await api.save_policy(1, rc.AccountPolicy(review_reply=True, review_reminder=True))
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.status = "closed"
            await db.commit()
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 2},
            "review": {"rating": 5, "text": "Good"},
        }
        await engine.observe_orders()
        await self.due()
        await engine.flush()
        self.send.assert_not_awaited()
        self.client.reply_review.assert_not_awaited()

    async def test_selected_account_review_reply_uses_only_its_chat(self):
        await self.cfg(
            reviews_enabled=True,
            account_scope="selected",
            account_ids=[2],
            review_target="chat",
            review_delay=0,
            review_texts={"5": "Thanks {stars}"},
        )
        await api.save_policy(2, rc.AccountPolicy(review_reply=True))
        async with self.Session() as db:
            db.add(
                Order(
                    id=2,
                    account_id=2,
                    funpay_order_id="OTHER-ORDER",
                    description="Lot",
                    sum=100,
                    currency="RUB",
                    buyer_id=2,
                    buyer_name="Buyer",
                    status="closed",
                    date_source="observed",
                    created_at=utcnow(),
                )
            )
            await db.commit()
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 2},
            "review": {"rating": 5, "text": "Good"},
        }
        await engine.observe_orders()
        await self.due()
        await engine.flush()
        self.send.assert_awaited_once_with(2, 4, "Thanks 5", idempotency_key="reply:review:2")
        self.client.reply_review.assert_not_awaited()

    async def test_uncertain_send_never_retries_after_recovery(self):
        await self.incoming()
        self.send.side_effect = TimeoutError()
        await engine.flush()
        await engine.recover()
        await engine.flush()
        self.send.assert_awaited_once()
        self.assertEqual((await self.rows())[0].state, "uncertain")

    async def test_paid_template_uses_verified_quantity(self):
        await self.cfg(paid_enabled=True, paid_text="Bought {lot_amount}")
        self.client.order_details.return_value = {
            "status": "paid",
            "buyer": {"user_id": 2},
            "quantity": 3,
        }
        await engine.observe_orders()
        await self.due()
        await engine.flush()
        self.send.assert_awaited_once_with(1, 1, "Bought 3", idempotency_key="reply:paid:1")

    async def test_deleted_account_does_not_leak_policy_to_reused_id(self):
        await api.save_policy(1, rc.AccountPolicy(confirmation=True))
        await services.save_setting("reply:handoff:1", {"paused": True})
        await self.cfg(account_scope="selected", account_ids=[1, 2])
        async with self.Session() as db:
            await api.remove_account_settings(db, 1)
            await db.commit()
        self.assertIsNone(await services.get_setting("account-automation:1", None))
        self.assertIsNone(await services.get_setting("reply:handoff:1", None))
        self.assertEqual((await rc.config())["account_ids"], [2])

    async def test_account_can_disable_inherited_review_replies(self):
        await self.cfg(reviews_enabled=True)
        await api.save_policy(1, rc.AccountPolicy(review_reply=False))
        async with self.Session() as db:
            o = await db.get(Order, 1)
            o.status = "closed"
            await db.commit()
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 2},
            "review": {"rating": 5, "text": "Good"},
        }
        await engine.observe_orders()
        self.assertFalse(await self.rows())

    async def test_first_save_can_disable_legacy_review_master(self):
        from app.models import Settings

        async with self.Session() as db:
            await db.delete(await db.get(Settings, "responder:v2"))
            await db.commit()
        await api.save_responder(rc.ReplyConfig(reviews_enabled=False))
        self.assertFalse((await services.get_setting("plugins"))["auto_thanks"])

    async def test_refund_commands_disabled_does_not_create_requests(self):
        await self.cfg(commands_enabled=False, refunds_enabled=True, greeting_enabled=False)
        await self.incoming("!возврат #ORDER")
        self.assertFalse(await self.rows())

    async def test_viewed_lot_ignores_profile_and_foreign_links(self):
        from app.funpay import FunPayClient

        client = FunPayClient("test-key", "Test User Agent", None, "httpx")
        try:
            html = '<a href="/users/42/?id=88">Buyer</a><a href="https://evil.example/lots/offer?id=99">Bad</a><a href="/lots/offer?id=10">Lot</a>'
            with patch.object(
                client,
                "runner",
                AsyncMock(
                    return_value={
                        "objects": [
                            {"type": "c-p-u", "id": "2", "data": {"html": {"desktop": html}}}
                        ]
                    }
                ),
            ):
                self.assertEqual(await client.viewed_lot(2), "10")
        finally:
            await client.close()
