"""KoSell integration safety tests: no real supplier or FunPay requests."""

import asyncio
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from app import kosell, kosell_api, message_templates, plugin_catalog, services
from app.integrated_config import packed
from app.models import (
    Account,
    Base,
    Chat,
    KosellMessage,
    KosellRental,
    Lot,
    Order,
    PluginBinding,
    utcnow,
)
from app.security import vault
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class KoSellTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.patches = [
            patch.object(m, "Session", self.Session)
            for m in (kosell, kosell_api, services, message_templates, plugin_catalog)
        ]
        self.patches += [
            patch.object(kosell, "log", AsyncMock()),
            patch.object(kosell, "notify", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
        await services.save_setting(
            "kosell", {"enabled": True, "api_key": vault.encrypt("test-key"), "currency": "RUB"}
        )
        async with self.Session() as db:
            db.add(
                Account(
                    id=1, title="Shop", username="Seller", golden_key="encrypted", user_agent="test"
                )
            )
            db.add(
                Chat(
                    id=1,
                    account_id=1,
                    funpay_chat_id="chat",
                    interlocutor_id=2,
                    interlocutor_name="Buyer",
                )
            )
            db.add(
                Lot(
                    id=1,
                    account_id=1,
                    funpay_lot_id="10",
                    node_id=1,
                    category_title="Steam",
                    title="Steam Rent",
                    price=Decimal("100"),
                )
            )
            db.add(
                Order(
                    id=1,
                    account_id=1,
                    funpay_order_id="ORDER",
                    funpay_lot_id="10",
                    description="Rent",
                    sum=Decimal("100"),
                    currency="RUB",
                    buyer_id=2,
                    buyer_name="Buyer",
                    status="paid",
                )
            )
            db.add(
                PluginBinding(
                    id=1,
                    kind="kosell",
                    lot_id=1,
                    quantity=2,
                    service_id="5",
                    provider_id="verified",
                )
            )
            await db.commit()
        self.client = AsyncMock()
        self.client.order_details.return_value = {
            "status": "paid",
            "buyer": {"user_id": 2},
            "offer_id": "10",
            "quantity": 1,
        }
        self.patches += [
            patch.object(kosell.accounts, "client", AsyncMock(return_value=self.client)),
            patch.object(kosell.accounts, "send", AsyncMock()),
        ]
        for p in self.patches[-2:]:
            p.start()
        self.end = utcnow() + timedelta(hours=2)
        self.quote = {"product_id": 5, "hours": 2, "total_rub": 50, "total_usd": 0.5}

    async def asyncTearDown(self):
        await kosell.stop()
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()

    async def seed_rental(self, status="ACTIVE"):
        async with self.Session() as db:
            db.add(
                KosellRental(
                    id=1,
                    order_id=1,
                    binding_id=1,
                    chat_id=1,
                    buyer_id=2,
                    kosell_uid="uid-1",
                    status=status,
                    end_time=self.end,
                    operation_key="unique-operation",
                    payload=packed(
                        {
                            "key": "test-key",
                            "product_id": 5,
                            "hours": 2,
                            "currency": "RUB",
                            "delivery": True,
                        }
                    ),
                )
            )
            await db.commit()

    async def claimed(self):
        await kosell.ensure_for_order(1)
        await asyncio.gather(*list(kosell.running.values()))

    async def test_timeout_never_repeats_debit_even_after_restart(self):
        with (
            patch.object(kosell, "price", AsyncMock(return_value=self.quote)),
            patch.object(
                kosell, "api", AsyncMock(side_effect=kosell.KoSellError("timeout"))
            ) as api,
        ):
            await self.claimed()
            await kosell.ensure_for_order(1)
            await kosell.recover()
            await kosell.tick()
            self.assertEqual(api.await_count, 1)
            async with self.Session() as db:
                self.assertEqual((await db.get(KosellRental, 1)).status, "MANUAL_CHECK")
            kosell.notify.assert_awaited_once()

    async def test_purchase_waits_for_ready_and_sends_credentials_once(self):
        async def api(method, path, **kwargs):
            if path == "/rental/rent":
                self.assertTrue(kwargs["operation_key"])
                self.assertTrue(kwargs["data"]["verify_before_issue"])
                return {
                    "rental_uid": "uid-1",
                    "expires_at": self.end.isoformat(),
                    "delivery": {"state": "queued"},
                }
            if "/delivery/" in path:
                return {"state": "checking"} if waiting[0] else {"state": "ready"}
            return {"steam_login": "login", "steam_password": "secret-password"}

        waiting = [True]
        with (
            patch.object(kosell, "price", AsyncMock(return_value=self.quote)),
            patch.object(kosell, "api", api),
        ):
            await self.claimed()
            await kosell.tick()
            kosell.accounts.send.assert_not_awaited()
            waiting[0] = False
            await kosell.tick()
            await kosell.tick()
            kosell.accounts.send.assert_awaited_once()
            self.assertIn("secret-password", kosell.accounts.send.call_args.args[1])
            async with self.Session() as db:
                entry = await db.scalar(select(KosellMessage))
                self.assertNotIn("secret-password", entry.body)
                self.assertTrue((await db.get(Order, 1)).auto_delivered)

    async def test_guard_wrong_sender_refund_and_expiry_never_fetch_code(self):
        await self.seed_rental()
        with patch.object(kosell, "api", AsyncMock()) as api:
            self.assertFalse(await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 999}))
            self.client.order_details.return_value["status"] = "refunded"
            await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 2, "id": 5})
            await kosell.tick()
            api.assert_not_awaited()
            self.assertIn("возвращен", kosell.accounts.send.call_args.args[1])

    async def test_guard_queue_refetches_fresh_code_after_funpay_error(self):
        await self.seed_rental()
        with patch.object(
            kosell,
            "api",
            AsyncMock(
                side_effect=[{"success": True, "code": "ABCDE"}, {"success": True, "code": "FGHIJ"}]
            ),
        ) as api:
            await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 2, "id": 6})
            await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 2, "id": 6})
            kosell.accounts.send.side_effect = [RuntimeError("FunPay unavailable"), None]
            await kosell.tick()
            async with self.Session() as db:
                entry = await db.scalar(select(KosellMessage))
                self.assertEqual(entry.state, "pending")
                entry.next_at = utcnow() - timedelta(seconds=1)
                await db.commit()
            await kosell.tick()
            self.assertEqual(api.await_count, 2)
            self.assertIn("FGHIJ", kosell.accounts.send.call_args.args[1])

    async def test_guard_rate_limit_is_template(self):
        await self.seed_rental()
        with patch.object(kosell, "api", AsyncMock(side_effect=kosell.KoSellError("limit", 429))):
            await kosell.handle_message(1, 1, {"text": "!код uid-1", "sender_id": 2, "id": 7})
            await kosell.tick()
            self.assertIn("ограничил", kosell.accounts.send.call_args.args[1])

    async def test_overpriced_quote_prevents_purchase(self):
        with (
            patch.object(kosell, "price", AsyncMock(return_value={**self.quote, "total_rub": 101})),
            patch.object(kosell, "api", AsyncMock()) as api,
        ):
            await self.claimed()
            api.assert_not_awaited()

    async def test_recovery_never_sends_purchasing_again(self):
        await self.seed_rental("PURCHASING")
        with patch.object(kosell, "api", AsyncMock()) as api:
            await kosell.recover()
            await kosell.tick()
            api.assert_not_awaited()
            async with self.Session() as db:
                self.assertEqual((await db.get(KosellRental, 1)).status, "MANUAL_CHECK")

    async def test_stock_and_expensive_lots_hide_only_after_funpay_ack(self):
        product = {"id": 5, "name": "Steam", "available_accounts": 0}
        with patch.object(kosell, "products", AsyncMock(return_value=[product])):
            self.client.update_lot.side_effect = RuntimeError("not saved")
            await kosell.stock_check()
            async with self.Session() as db:
                self.assertTrue((await db.get(Lot, 1)).is_active)
            self.client.update_lot.side_effect = None
            await kosell.stock_check()
            async with self.Session() as db:
                self.assertFalse((await db.get(Lot, 1)).is_active)

    async def test_binding_conflict_and_invalid_duration(self):
        products = [{"id": 5, "available_accounts": 2, "min_hours": 2, "max_hours": 10}]
        with (
            patch.object(kosell, "products", AsyncMock(return_value=products)),
            patch.object(kosell, "price", AsyncMock(return_value=self.quote)),
        ):
            with self.assertRaises(HTTPException) as error:
                await kosell_api.save_binding(kosell_api.Binding(lot_id=1, product_id=5, hours=1))
            self.assertEqual(error.exception.status_code, 400)
            with self.assertRaises(HTTPException) as error:
                await kosell_api.save_binding(kosell_api.Binding(lot_id=1, product_id=5, hours=2))
            self.assertEqual(error.exception.status_code, 409)

    async def test_config_is_encrypted_and_catalog_has_separate_card(self):
        await kosell_api.save_config(kosell_api.Config(api_key="replacement-key"))
        stored = await services.get_setting("kosell", {})
        self.assertNotIn("replacement-key", stored["api_key"])
        self.assertTrue((await kosell.settings())["has_api_key"])
        self.assertEqual((await kosell.settings())["api_key"], "")
        ids = [r["id"] for r in await plugin_catalog.catalog()]
        self.assertIn("kosell", ids)
        self.assertIn("steam-rent", ids)

    async def test_transport_uses_documented_base_header_and_honors_retry_after(self):
        kosell.next_call = 0
        response = httpx.Response(429, headers={"Retry-After": "45"}, json={})
        with patch.object(kosell.pool, "request", AsyncMock(return_value=response)) as request:
            with self.assertRaises(kosell.KoSellError) as error:
                await kosell.api("GET", "/account/balance")
            self.assertEqual(error.exception.retry_after, 45)
            self.assertEqual(
                request.call_args.args[1], "https://ru.kosell.store/api/v1/account/balance"
            )
            self.assertEqual(request.call_args.kwargs["headers"]["X-API-Key"], "test-key")
            with self.assertRaises(kosell.KoSellError):
                await kosell.api("GET", "/account/balance")
            request.assert_awaited_once()

    async def test_manual_attach_only_reads_supplier_and_confirms_parameters(self):
        await self.seed_rental("MANUAL_CHECK")
        response = [
            {
                "rental_uid": "confirmed",
                "product_id": 5,
                "duration_hours": 2,
                "expires_at": self.end.isoformat(),
                "delivery": {"state": "queued"},
            }
        ]
        with patch.object(kosell, "api", AsyncMock(return_value=response)) as api:
            await kosell_api.attach(1, kosell_api.Attach(uid="confirmed"))
            self.assertEqual(api.call_args.args, ("GET", "/rental/active"))
            async with self.Session() as db:
                rental = await db.get(KosellRental, 1)
                self.assertEqual((rental.kosell_uid, rental.status), ("confirmed", "PREPARING"))

    async def test_expired_lease_is_denied_without_guard_request(self):
        await self.seed_rental()
        async with self.Session() as db:
            (await db.get(KosellRental, 1)).end_time = utcnow() - timedelta(seconds=1)
            await db.commit()
        with patch.object(kosell, "api", AsyncMock()) as api:
            await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 2, "id": 8})
            await kosell.tick()
            api.assert_not_awaited()
            self.assertIn("истекло", kosell.accounts.send.call_args.args[1])

    async def test_paused_plugin_and_account_do_not_deliver(self):
        await self.seed_rental()
        await kosell.handle_message(1, 1, {"text": "!код", "sender_id": 2, "id": 8})
        async with self.Session() as db:
            (await db.get(Account, 1)).is_active = False
            await db.commit()
        with patch.object(kosell, "api", AsyncMock()) as api:
            await kosell.tick()
            api.assert_not_awaited()
            kosell.accounts.send.assert_not_awaited()

    async def test_missing_stock_does_not_disable_lot_when_option_off(self):
        config = await services.get_setting("kosell", {})
        await services.save_setting("kosell", {**config, "auto_hide": False})
        with patch.object(
            kosell, "products", AsyncMock(return_value=[{"id": 5, "available_accounts": 0}])
        ):
            await kosell.stock_check()
            self.client.update_lot.assert_not_awaited()
