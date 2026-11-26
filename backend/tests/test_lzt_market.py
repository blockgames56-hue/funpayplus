"""Offline LZT buyer tests; no real account or purchase requests."""

import json
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import lzt_market
from app.models import Base, MarketPurchase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class MarketParserTests(unittest.TestCase):
    def test_filter_uses_official_category_and_price_parameter(self):
        category, params = lzt_market.parse_filter("https://lzt.market/steam/?max_price=100&mm_ban=nomatter")
        self.assertEqual(category, "steam")
        self.assertIn(("pmax", "100"), params)
        self.assertIn(("mm_ban", "nomatter"), params)
        self.assertIn(("order_by", "pdate_to_down"), params)
        self.assertIn(("currency", "rub"), params)

    def test_filter_rejects_external_host(self):
        with self.assertRaises(ValueError):
            lzt_market.parse_filter("https://lzt.market.evil.example/steam/?pmax=100")

    def test_retry_after_seconds(self):
        self.assertEqual(lzt_market.retry_seconds(httpx.Headers({"Retry-After": "12"})), 12)


class MarketPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_is_encrypted_before_settings_write(self):
        config = {"token": "secret-token", "filter_url": "https://lzt.market/steam/?pmax=50", "max_price": "50", "reserve_balance": "0", "enabled": False}
        with (patch.object(lzt_market, "get_setting", AsyncMock(return_value={"token": "old"})),
              patch.object(lzt_market, "save_setting", AsyncMock()) as save,
              patch.object(lzt_market.worker, "stop", AsyncMock()),
              patch.object(lzt_market.worker, "start", AsyncMock())):
            await lzt_market.save_config(config)
        stored = save.call_args.args[1]
        self.assertNotIn("secret-token", str(stored))
        self.assertEqual(lzt_market.vault.decrypt(stored["token"]), "secret-token")

    async def test_rate_limit_freezes_requests(self):
        client = lzt_market.MarketClient("test-token", transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "9"})))
        client.gate.interval = 0
        with self.assertRaises(lzt_market.RateLimited):
            await client.request("GET", "/steam")
        self.assertGreater(client.gate.next_at - time.monotonic(), 8)
        await client.close()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lzt-market-test-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patch_session = patch.object(lzt_market, "Session", self.session)
        self.patch_session.start()

    async def asyncTearDown(self):
        self.patch_session.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_ordered_purchase_and_encrypted_credentials(self):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            if request.url.path == "/101":
                return httpx.Response(200, json={"item": {"login": "buyer", "password": "private"}})
            return httpx.Response(200, json={"success": True})
        client = lzt_market.MarketClient("test-token", transport=httpx.MockTransport(handler))
        client.gate.interval = 0
        worker = lzt_market.MarketWorker()
        worker.balance = Decimal("200")
        with patch.object(lzt_market.bus, "publish", return_value=None) as publish:
            async def emit(*args, **kwargs):
                return None
            publish.side_effect = emit
            await worker._consider(client, "steam", {"item_id": 101, "title": "Steam item", "price": 40}, {"max_price": "50", "reserve_balance": "100"})
        self.assertEqual(calls, [("POST", "/cart"), ("POST", "/101/check-account"), ("POST", "/101/fast-buy"), ("GET", "/101")])
        async with self.session() as db:
            row = await db.scalar(select(MarketPurchase).where(MarketPurchase.item_id == 101))
            self.assertEqual(row.status, "confirmed")
            self.assertNotIn("private", row.credentials)
            self.assertEqual(json.loads(lzt_market.vault.decrypt(row.credentials))["password"], "private")
        await client.close()

    async def test_ambiguous_buy_is_not_repeated(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/fast-buy"):
                raise httpx.ReadTimeout("unknown outcome")
            return httpx.Response(200, json={"success": True})
        client = lzt_market.MarketClient("test-token", transport=httpx.MockTransport(handler))
        client.gate.interval = 0
        worker = lzt_market.MarketWorker()
        worker.balance = Decimal("200")
        settings = {"max_price": "50", "reserve_balance": "100"}
        item = {"item_id": 102, "title": "Steam item", "price": 40}
        await worker._consider(client, "steam", item, settings)
        await worker._consider(client, "steam", item, settings)
        self.assertEqual(calls.count("/102/fast-buy"), 1)
        async with self.session() as db:
            row = await db.scalar(select(MarketPurchase).where(MarketPurchase.item_id == 102))
            self.assertEqual(row.status, "uncertain")
        await client.close()
