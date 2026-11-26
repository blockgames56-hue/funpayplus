"""Integration tests requiring the complete backend requirements.

All storage is temporary; FunPay requests are mocked.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

temporary = tempfile.TemporaryDirectory(prefix="funpay-plus-tests-")
os.environ["STORAGE_DIR"] = temporary.name
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (Path(temporary.name) / "test.db").as_posix()
os.environ["WORKERS_ENABLED"] = "false"
os.environ["API_TOKEN"] = "sample-test-access-token-1234567890"
os.environ["SECRET_KEY"] = "sample-test-encryption-key-1234567890"

import httpx  # noqa: E402
from app.db import Session, engine  # noqa: E402
from app.funpay import USER_AGENT, FunPayError  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AutoDeliveryRule,
    Base,
    BlacklistEntry,
    Chat,
    ChatMessage,
    DeliveryReservation,
    Lot,
    Order,
)
from app.security import vault  # noqa: E402
from app.services import accounts, automation  # noqa: E402
from sqlalchemy import select  # noqa: E402


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def uncertain_reservation(self):
        account_id, _, order_id, rule_id = await self.seed(with_order=True)
        async with Session() as db:
            order = await db.get(Order, order_id)
            order.delivery_state = "uncertain"
            rule = await db.get(AutoDeliveryRule, rule_id)
            rule.keys_pool = vault.encrypt("sample-key-2")
            row = DeliveryReservation(order_id=order_id, rule_id=rule_id, state="uncertain",
                                      item=vault.encrypt("sample-key-1"))
            db.add(row)
            await db.commit()
            return account_id, order_id, rule_id, row.id

    async def test_resolution_releases_once_and_cancels_without_network(self):
        _, order_id, rule_id, ident = await self.uncertain_reservation()
        body = dict(action="release", confirmed=True, note="История проверена: отправки нет")
        with patch.object(accounts, "client", new=AsyncMock()) as client:
            response = await self.http.post(f"/api/automation/deliveries/{ident}/resolve", json=body)
            self.assertEqual(response.status_code, 200)
            response = await self.http.post(f"/api/automation/deliveries/{ident}/resolve", json=body)
            self.assertEqual(response.status_code, 409)
            await automation.deliver(order_id)
            client.assert_not_called()
        async with Session() as db:
            self.assertEqual((await db.get(Order, order_id)).delivery_state, "cancelled")
            row = await db.get(DeliveryReservation, ident)
            self.assertEqual(row.state, "released")
            self.assertEqual(vault.decrypt(row.resolution_note), body["note"])
            self.assertNotIn(body["note"], row.resolution_note)
            pool = vault.decrypt((await db.get(AutoDeliveryRule, rule_id)).keys_pool).splitlines()
            self.assertCountEqual(pool, ["sample-key-1", "sample-key-2"])

    async def test_resolution_confirms_sent_without_sending(self):
        _, order_id, rule_id, ident = await self.uncertain_reservation()
        with patch.object(accounts, "send", new=AsyncMock()) as send:
            response = await self.http.post(f"/api/automation/deliveries/{ident}/resolve", json={
                "action": "confirm_sent", "confirmed": True, "note": "Сообщение с товаром найдено в чате"})
            self.assertEqual(response.status_code, 200)
            send.assert_not_called()
        async with Session() as db:
            self.assertTrue((await db.get(Order, order_id)).auto_delivered)
            self.assertEqual(vault.decrypt((await db.get(AutoDeliveryRule, rule_id)).keys_pool), "sample-key-2")

    async def test_resolution_requires_confirmation_and_existing_pool(self):
        _, _, rule_id, ident = await self.uncertain_reservation()
        body = dict(action="release", confirmed=False, note="Проверка чата проведена")
        response = await self.http.post(f"/api/automation/deliveries/{ident}/resolve", json=body)
        self.assertEqual(response.status_code, 422)
        async with Session() as db:
            row = await db.get(DeliveryReservation, ident)
            row.rule_id = None
            await db.commit()
        response = await self.http.post(f"/api/automation/deliveries/{ident}/resolve", json=body | {"confirmed": True})
        self.assertEqual(response.status_code, 409)

    async def test_pool_rejects_keys_in_other_pools_and_reservations(self):
        account_id, _, _, _ = await self.uncertain_reservation()
        for key in ("sample-key-1", "sample-key-2"):
            response = await self.http.post("/api/rules", json={"account_id": account_id,
                "lot_title_pattern": "Other", "delivery_type": "pool_keys",
                "message_template": "{item}", "keys_pool": key})
            self.assertEqual(response.status_code, 409)
            self.assertNotIn(key, response.text)

    async def test_concurrent_pool_import_reserves_unique_key(self):
        account_id, _, _, _ = await self.seed()
        body = {"account_id": account_id, "lot_title_pattern": "Product",
                "delivery_type": "pool_keys", "message_template": "{item}",
                "keys_pool": "synthetic-unique-stock"}
        results = await asyncio.gather(*(self.http.post("/api/rules", json=body) for _ in range(2)))
        self.assertCountEqual([r.status_code for r in results], [201, 409])

    async def test_edit_dry_run_does_not_save_or_synchronize(self):
        from app.lot_editor import describe_form
        from test_offer_editor import FORM
        account_id, _, _, _ = await self.seed()
        async with Session() as db:
            lot = Lot(account_id=account_id, funpay_lot_id="123", node_id=81,
                      category_title="Game", title="Product", price=100)
            db.add(lot)
            await db.commit()
            ident = lot.id
        remote = AsyncMock()
        remote.offer_lock = lambda ident: asyncio.Lock()
        with (patch("app.main.load_form", new=AsyncMock(return_value=describe_form(FORM.format(offer=123), offer_id="123"))),
              patch("app.main.save_form", new=AsyncMock()) as save,
              patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
              patch.object(accounts, "sync_lots", new=AsyncMock()) as sync):
            response = await self.http.put(f"/api/lots/{ident}?dry_run=true", json={"fields": {"price": "120"}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["changed_fields"], ["price"])
            save.assert_not_called()
            sync.assert_not_called()

    async def test_raise_dry_run_keeps_local_schedule_and_never_raises(self):
        account_id, _, _, _ = await self.seed()
        async with Session() as db:
            lot = Lot(account_id=account_id, funpay_lot_id="17", node_id=17,
                      game_id=4, category_title="Game", title="Product", price=10)
            db.add(lot)
            await db.commit()
            lot_id = lot.id
        remote = AsyncMock()
        remote.raise_lots.return_value = dict(raised=False, dry_run=True, cooldown=0,
                                             message="Dry-run")
        with patch.object(accounts, "client", new=AsyncMock(return_value=remote)):
            response = await self.http.post("/api/lots/bulk", json={
                "ids": [lot_id], "action": "raise", "dry_run": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()[0]["dry_run"])
        self.assertFalse(response.json()[0]["raised"])
        remote.raise_lots.assert_awaited_once_with(17, 4, dry_run=True)
        async with Session() as db:
            lot = await db.get(Lot, lot_id)
            self.assertIsNone(lot.last_raised_at)
            self.assertIsNone(lot.next_raise_at)

    async def test_dry_run_rejects_unsupported_mutations(self):
        with patch.object(accounts, "client", new=AsyncMock()) as client:
            response = await self.http.post("/api/lots/bulk", json={
                "ids": [1], "action": "delete", "dry_run": True})
        self.assertEqual(response.status_code, 422)
        client.assert_not_called()

    async def test_core_toggle_and_diagnostics_with_plugin_catalog(self):
        response = await self.http.put("/api/workspaces/automation/responder",
                                       json={"is_enabled": True})
        self.assertEqual(response.status_code, 200)
        response = await self.http.get("/api/workspaces/responder")
        self.assertTrue(response.json()["enabled"])
        response = await self.http.get("/api/automation/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unknown_sends"], 0)
        # Слой плагинов возвращён в ядро: каталог и страницы модулей отвечают.
        for path in ("/api/plugins/catalog", "/api/app-route/suppliers",
                     "/api/auto-robux/settings", "/api/auto-dump/rules"):
            response = await self.http.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn("token", response.text.lower())

    async def asyncSetUp(self):
        automation.delivery_lock = asyncio.Lock()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        )

    async def asyncTearDown(self):
        await self.http.aclose()
        await engine.dispose()

    async def seed(self, with_order=False, *, funpay_id=42, order_reference="SAMPLE"):
        async with Session() as db:
            account = Account(
                title="Sample Store",
                golden_key=vault.encrypt("sample-golden-key"),
                funpay_id=funpay_id,
                username="Sample",
                user_agent=USER_AGENT,
            )
            db.add(account)
            await db.flush()
            chat = Chat(
                account_id=account.id,
                funpay_chat_id=f"users-8-{funpay_id}",
                interlocutor_id=8,
                interlocutor_name="Buyer",
            )
            db.add(chat)
            await db.flush()
            db.add(
                ChatMessage(
                    chat_id=chat.id,
                    sender_id=8,
                    sender_name="Buyer",
                    text="Sample?",
                    funpay_message_id=1,
                )
            )
            if with_order:
                rule = AutoDeliveryRule(
                    account_id=account.id,
                    lot_title_pattern="Sample Game",
                    delivery_type="pool_keys",
                    message_template="{buyer}: {item}",
                    keys_pool=vault.encrypt("sample-key-1\nsample-key-2"),
                )
                order = Order(
                    account_id=account.id,
                    funpay_order_id=order_reference,
                    description="Sample Game",
                    sum=Decimal("10.25"),
                    currency="RUB",
                    buyer_id=8,
                    buyer_name="Buyer",
                    status="paid",
                )
                db.add_all([rule, order])
                await db.flush()
                identifiers = account.id, chat.id, order.id, rule.id
            else:
                identifiers = account.id, chat.id, None, None
            await db.commit()
            return identifiers

    async def test_unauthenticated_api_rejected(self):
        response = await self.http.get("/api/accounts", headers={"Authorization": ""})
        self.assertEqual(response.status_code, 401)

    async def test_cross_origin_write_rejected(self):
        response = await self.http.post(
            "/api/telegram/test", headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(response.status_code, 403)

    async def test_validation_does_not_echo_secret(self):
        secret = "sample-secret;cookie=injection"
        response = await self.http.post(
            "/api/accounts", json={"title": "Sample", "golden_key": secret}
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)

    async def test_account_secret_never_returned(self):
        await self.seed()
        response = await self.http.get("/api/accounts")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("golden_key", response.text)
        self.assertNotIn("sample-golden-key", response.text)

    async def test_foreign_key_cascade(self):
        account_id, chat_id, _, _ = await self.seed()
        response = await self.http.delete(f"/api/accounts/{account_id}")
        self.assertEqual(response.status_code, 200)
        async with Session() as db:
            self.assertIsNone(await db.get(Chat, chat_id))
            self.assertIsNone(await db.scalar(select(ChatMessage.id)))

    async def test_duplicate_pool_keys_rejected(self):
        account_id, _, _, _ = await self.seed()
        response = await self.http.post(
            "/api/rules",
            json={
                "account_id": account_id,
                "lot_title_pattern": "Sample",
                "delivery_type": "pool_keys",
                "message_template": "{item}",
                "keys_pool": "sample\nsample",
            },
        )
        self.assertEqual(response.status_code, 422)

    async def test_concurrent_delivery_sends_one_key(self):
        _, _, order_id, rule_id = await self.seed(with_order=True)
        remote = AsyncMock()
        remote.order_details.return_value = {"status": "paid", "buyer": {"user_id": 8}}
        with (
            patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
            patch.object(accounts, "send", new=AsyncMock()) as send,
        ):
            await asyncio.gather(automation.deliver(order_id), automation.deliver(order_id))
            self.assertEqual(send.await_count, 1)
            self.assertEqual(send.call_args.kwargs["source"], "delivery")
            self.assertEqual(send.call_args.kwargs["idempotency_key"], "delivery:1:SAMPLE")
        async with Session() as db:
            order = await db.get(Order, order_id)
            rule = await db.get(AutoDeliveryRule, rule_id)
            self.assertTrue(order.auto_delivered)
            self.assertEqual(vault.decrypt(rule.keys_pool), "sample-key-2")
            reservation = await db.scalar(select(DeliveryReservation))
            self.assertEqual(reservation.state, "sent")
            self.assertEqual(vault.decrypt(reservation.item), "sample-key-1")
            self.assertNotIn("sample-key-1", reservation.item)

    async def test_account_blacklist_blocks_only_its_own_delivery(self):
        first_account, _, first_order, _ = await self.seed(with_order=True)
        _, second_chat, second_order, second_rule = await self.seed(
            with_order=True, funpay_id=43, order_reference="SAMPLE-SECOND"
        )
        async with Session() as db:
            (await db.get(AutoDeliveryRule, second_rule)).keys_pool = vault.encrypt("second-account-key")
            db.add(BlacklistEntry(user_id=8, username="Buyer", account_id=first_account))
            await db.commit()
        remote = AsyncMock()
        remote.order_details.return_value = {"status": "paid", "buyer": {"user_id": 8}}
        with (
            patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
            patch.object(accounts, "send", new=AsyncMock()) as send,
        ):
            await automation.deliver(first_order)
            await automation.deliver(second_order)
        send.assert_awaited_once()
        self.assertEqual(send.call_args.args[0], second_chat)
        async with Session() as db:
            self.assertEqual((await db.get(Order, first_order)).delivery_state, "pending")
            self.assertTrue((await db.get(Order, second_order)).auto_delivered)

    async def test_ambiguous_delivery_is_not_retried(self):
        _, _, order_id, rule_id = await self.seed(with_order=True)
        remote = AsyncMock()
        remote.order_details.return_value = {"status": "paid", "buyer": {"user_id": 8}}
        with (
            patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
            patch.object(
                accounts, "send", new=AsyncMock(side_effect=FunPayError("unknown result"))
            ) as send,
        ):
            await automation.deliver(order_id)
            await automation.deliver(order_id)
            self.assertEqual(send.await_count, 1)
        async with Session() as db:
            self.assertEqual((await db.get(Order, order_id)).delivery_state, "uncertain")
            self.assertEqual((await db.scalar(select(DeliveryReservation))).state, "uncertain")
            self.assertEqual(
                vault.decrypt((await db.get(AutoDeliveryRule, rule_id)).keys_pool), "sample-key-2"
            )


    async def test_cancelled_delivery_is_quarantined_on_restart(self):
        _, _, order_id, rule_id = await self.seed(with_order=True)
        remote = AsyncMock()
        remote.order_details.return_value = {"status": "paid", "buyer": {"user_id": 8}}
        with (patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
              patch.object(accounts, "send", new=AsyncMock(side_effect=asyncio.CancelledError)) as send):
            with self.assertRaises(asyncio.CancelledError):
                await automation.deliver(order_id)
            await automation.recover_deliveries()
            await automation.deliver(order_id)
            self.assertEqual(send.await_count, 1)
        async with Session() as db:
            self.assertEqual((await db.get(Order, order_id)).delivery_state, "uncertain")
            self.assertEqual((await db.scalar(select(DeliveryReservation))).state, "uncertain")
            self.assertEqual(vault.decrypt((await db.get(AutoDeliveryRule, rule_id)).keys_pool), "sample-key-2")
        response = await self.http.get("/api/automation/deliveries")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["state"], "uncertain")
        self.assertNotIn("item", response.json()[0])
        self.assertNotIn("sample-key", response.text)

    async def test_template_failure_rolls_back_pool_without_reservation(self):
        _, _, order_id, rule_id = await self.seed(with_order=True)
        async with Session() as db:
            rule = await db.get(AutoDeliveryRule, rule_id)
            rule.message_template = "{missing}"
            await db.commit()
        remote = AsyncMock()
        remote.order_details.return_value = {"status": "paid", "buyer": {"user_id": 8}}
        with (patch.object(accounts, "client", new=AsyncMock(return_value=remote)),
              patch.object(accounts, "send", new=AsyncMock()) as send):
            with self.assertRaises(KeyError):
                await automation.deliver(order_id)
            send.assert_not_called()
        async with Session() as db:
            self.assertIsNone(await db.scalar(select(DeliveryReservation)))
            self.assertEqual((await db.get(Order, order_id)).delivery_state, "pending")
            self.assertEqual(vault.decrypt((await db.get(AutoDeliveryRule, rule_id)).keys_pool),
                             "sample-key-1\nsample-key-2")


if __name__ == "__main__":
    unittest.main()
