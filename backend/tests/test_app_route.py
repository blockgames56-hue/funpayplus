"""Supplier routing uses exact lot IDs and persists ambiguous delivery states."""

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import app_route, main, message_templates, services
from app.funpay import FunPayError
from app.models import (
    Account,
    AppRouteDelivery,
    AppRouteMapping,
    AppRouteSupplier,
    Base,
    Chat,
    Lot,
    Order,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class RouteValidationTests(unittest.TestCase):
    def test_endpoint_and_lot_id(self):
        self.assertEqual(app_route.validate_endpoint("https://supplier.example/api"), "https://supplier.example/api")
        for url in ("http://supplier.example/api", "https://localhost/api", "https://127.0.0.1/api", "https://user:pass@supplier.example/api"):
            with self.assertRaises(ValueError):
                app_route.validate_endpoint(url)
        self.assertEqual(app_route.detail_lot_id({"details": {"offer_id": 123}}), "123")
        self.assertIsNone(app_route.detail_lot_id({"id": "ORDER-123"}))


class RouteDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controls_patch = patch.object(app_route, "get_setting", AsyncMock(return_value={}))
        self.controls_patch.start()
        self.temp = tempfile.TemporaryDirectory(prefix="app-route-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "route.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session_patch = patch.object(app_route, "Session", self.session)
        self.session_patch.start()
        self.template_patch = patch.object(message_templates, "Session", self.session)
        self.template_patch.start()
        async with self.session() as db:
            account = Account(title="Seller", golden_key="encrypted", funpay_id=1, username="Seller", user_agent="test")
            db.add(account)
            await db.flush()
            supplier = AppRouteSupplier(name="Test", api_type="webhook", endpoint_url="https://supplier.example/api", is_active=True)
            db.add(supplier)
            await db.flush()
            db.add(AppRouteMapping(account_id=account.id, funpay_item_id="123", supplier_id=supplier.id, supplier_item_id="gems", quantity=1))
            order = Order(account_id=account.id, funpay_order_id="ORDER-1", description="Gems", sum=Decimal("10"), currency="RUB", buyer_id=42, buyer_name="Buyer", status="paid")
            db.add(order)
            await db.flush()
            db.add(Chat(account_id=account.id, funpay_chat_id="users-1-42", interlocutor_id=42, interlocutor_name="Buyer"))
            await db.commit()
            self.order_id = order.id

    async def asyncTearDown(self):
        self.controls_patch.stop()
        self.session_patch.stop()
        self.template_patch.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_success_is_sent_once(self):
        client = AsyncMock()
        client.order_details.return_value = {"status": "paid", "buyer": {"user_id": 42}, "details": {"offer_id": 123}}
        real_client = httpx.AsyncClient
        calls = []

        def supplier_response(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "success", "data": "KEY-123"})

        with (patch.object(app_route.accounts, "client", AsyncMock(return_value=client)),
              patch.object(app_route.accounts, "send", AsyncMock()) as send,
              patch.object(app_route.bus, "log", AsyncMock()),
              patch.object(app_route.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(supplier_response), **kw))):
            self.assertTrue(await app_route.deliver(self.order_id))
            self.assertTrue(await app_route.deliver(self.order_id))
            send.assert_awaited_once()
            self.assertIn("Ваш товар: KEY-123", send.call_args.args[1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].headers["Idempotency-Key"], "funpay-plus-ORDER-1")
        self.assertEqual(calls[0].read().decode().count('"gems"'), 1)
        async with self.session() as db:
            order = await db.get(Order, self.order_id)
            delivery = await db.scalar(select(AppRouteDelivery).where(AppRouteDelivery.order_id == self.order_id))
            self.assertEqual(order.delivery_state, "sent")
            self.assertTrue(order.auto_delivered)
            self.assertEqual(delivery.status, "sent")
            self.assertNotIn("KEY-123", delivery.result_data)

    async def test_supplier_failure_retries_once_and_alerts(self):
        client = AsyncMock()
        client.order_details.return_value = {"status": "paid", "buyer": {"user_id": 42}, "details": {"offer_id": 123}}
        real_client = httpx.AsyncClient
        calls = []

        def supplier_response(request):
            calls.append(request)
            return httpx.Response(503)

        with (patch.object(app_route.accounts, "client", AsyncMock(return_value=client)),
              patch.object(app_route.accounts, "send", AsyncMock()) as send,
              patch.object(services.telegram, "notify", AsyncMock()) as alert,
              patch.object(app_route.bus, "log", AsyncMock()),
              patch.object(app_route.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(supplier_response), **kw))):
            self.assertTrue(await app_route.deliver(self.order_id))
            self.assertEqual(len(calls), 2)
            send.assert_awaited_once()
            self.assertIn("не подтвердил", send.call_args.args[1])
            alert.assert_awaited_once()
        async with self.session() as db:
            delivery = await db.scalar(select(AppRouteDelivery).where(AppRouteDelivery.order_id == self.order_id))
            self.assertEqual(delivery.status, "uncertain")
            self.assertEqual(delivery.attempts, 2)

    async def test_unmapped_lot_never_calls_supplier(self):
        client = AsyncMock()
        client.order_details.return_value = {"status": "paid", "buyer": {"user_id": 42}, "details": {"offer_id": 999}}
        with (patch.object(app_route.accounts, "client", AsyncMock(return_value=client)),
              patch.object(app_route.httpx, "AsyncClient") as supplier_http):
            self.assertFalse(await app_route.deliver(self.order_id))
            supplier_http.assert_not_called()
        async with self.session() as db:
            self.assertIsNone(await db.scalar(select(AppRouteDelivery).where(AppRouteDelivery.order_id == self.order_id)))

    async def test_shutdown_hides_lots_only_after_funpay_acknowledges(self):
        async with self.session() as db:
            db.add(Lot(account_id=1, funpay_lot_id="123", node_id=1, category_title="Game", title="Gems", price=Decimal("10")))
            await db.commit()
        client = AsyncMock()
        with (patch.object(main, "get_setting", AsyncMock(return_value={"hide_on_exit": True})),
              patch.object(main.accounts, "client", AsyncMock(return_value=client))):
            async with self.session() as db:
                result = await main.prepare_shutdown(db)
            self.assertEqual(result["hidden"], 1)
            client.update_lot.assert_awaited_once_with("123", active=False)
        async with self.session() as db:
            lot = await db.scalar(select(Lot))
            self.assertFalse(lot.is_active)

    async def test_shutdown_failure_preserves_active_lot(self):
        async with self.session() as db:
            db.add(Lot(account_id=1, funpay_lot_id="123", node_id=1, category_title="Game", title="Gems", price=Decimal("10")))
            await db.commit()
        client = AsyncMock()
        client.update_lot.side_effect = FunPayError("remote failure")
        with (patch.object(main, "get_setting", AsyncMock(return_value={"hide_on_exit": True})),
              patch.object(main.accounts, "client", AsyncMock(return_value=client)),
              patch.object(main.bus, "log", AsyncMock())):
            async with self.session() as db:
                with self.assertRaises(main.HTTPException) as error:
                    await main.prepare_shutdown(db)
                self.assertEqual(error.exception.status_code, 502)
        async with self.session() as db:
            lot = await db.scalar(select(Lot))
            self.assertTrue(lot.is_active)
