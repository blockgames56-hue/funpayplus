"""Offline checks for repricing floors and Roblox price verification."""

import asyncio
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import auto_dump, auto_robux
from app.models import Account, AutoDumpRule, Base, Lot
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

HTML = """<a class='tc-item' href='/lots/offer?id=1' data-user='10'><div class='tc-price' data-s='100'>100 ₽</div></a>
<a class='tc-item' href='/lots/offer?id=2' data-user='20'><div class='tc-price' data-s='90'>90 ₽</div></a>
<a class='tc-item' href='/lots/offer?id=3' data-user='30'><div class='tc-price' data-s='80'>80 ₽</div></a>
<a class='tc-item' href='/lots/offer?id=4'><div class='tc-price' data-s='1'>1 ₽</div></a>"""


class PricingMathTests(unittest.TestCase):
    def test_competitor_filter_and_floor(self):
        self.assertEqual(auto_dump.parse_competitor_prices(HTML, {10, 30}, "1"), [Decimal("90.00")])
        self.assertEqual(auto_dump.target_price(Decimal("90"), Decimal("80"), Decimal("1")), Decimal("89.00"))
        self.assertIsNone(auto_dump.target_price(Decimal("80"), Decimal("80"), Decimal("1")))
        self.assertIsNone(auto_dump.target_price(Decimal("81"), Decimal("80.50"), Decimal("1")))
        with self.assertRaises(ValueError):
            auto_dump.category_path("https://evil.example/lots/81/")

    def test_robux_fee_math(self):
        self.assertEqual(auto_robux.price_for_target(100, True), 143)
        self.assertEqual(auto_robux.price_for_target(100, False), 100)
        self.assertEqual(auto_robux.estimated_net(143), 100)
        self.assertEqual(auto_robux.estimated_net(100), 70)


class DumpScanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controls_patch = patch.object(auto_dump, "get_setting", AsyncMock(return_value={}))
        self.controls_patch.start()
        self.temp = tempfile.TemporaryDirectory(prefix="auto-dump-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "dump.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patch_session = patch.object(auto_dump, "Session", self.session)
        self.patch_session.start()
        async with self.session() as db:
            account = Account(title="Seller", golden_key="encrypted", funpay_id=10, username="Seller", user_agent="test")
            db.add(account)
            await db.flush()
            lot = Lot(account_id=account.id, funpay_lot_id="1", node_id=81, category_title="Game", title="Gems", price=Decimal("95"))
            db.add(lot)
            await db.flush()
            rule = AutoDumpRule(lot_id=lot.id, options='{"convert_commission":false}', category_url="https://funpay.com/lots/81/", absolute_min=Decimal("80"), step=Decimal("1"), is_enabled=True)
            db.add(rule)
            await db.commit()
            self.lot_id, self.rule_id = lot.id, rule.id

    async def asyncTearDown(self):
        self.controls_patch.stop()
        self.patch_session.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_reprices_only_after_remote_ack(self):
        client = AsyncMock()
        client.request.return_value = httpx.Response(200, text=HTML)
        with (patch.object(auto_dump.accounts, "client", AsyncMock(return_value=client)),
              patch.object(auto_dump.bus, "log", AsyncMock())):
            await auto_dump.scan_rule(self.rule_id, {10, 30}, asyncio.Semaphore(5))
        client.update_lot.assert_awaited_once_with("1", price=Decimal("89.00"))
        async with self.session() as db:
            self.assertEqual((await db.get(Lot, self.lot_id)).price, Decimal("89.00"))
            self.assertEqual((await db.get(AutoDumpRule, self.rule_id)).status, "updated")

    async def test_floor_blocks_update(self):
        async with self.session() as db:
            rule = await db.get(AutoDumpRule, self.rule_id)
            rule.absolute_min = Decimal("90")
            await db.commit()
        client = AsyncMock()
        client.request.return_value = httpx.Response(200, text=HTML)
        with patch.object(auto_dump.accounts, "client", AsyncMock(return_value=client)):
            await auto_dump.scan_rule(self.rule_id, {10, 30}, asyncio.Semaphore(5))
        client.update_lot.assert_not_awaited()
        async with self.session() as db:
            self.assertEqual((await db.get(Lot, self.lot_id)).price, Decimal("95.00"))

    async def test_buyer_price_is_converted_to_seller_price_without_crossing_floor(self):
        async with self.session() as db:
            rule = await db.get(AutoDumpRule, self.rule_id)
            rule.options = '{"convert_commission":true,"divider":"0.01"}'
            rule.absolute_min = Decimal("70")
            await db.commit()
        client = AsyncMock()
        client.request.return_value = httpx.Response(200, text=HTML)
        with (patch.object(auto_dump.accounts, "client", AsyncMock(return_value=client)),
              patch.object(auto_dump, "commission_ratio", AsyncMock(return_value=Decimal("1.16"))),
              patch.object(auto_dump.bus, "log", AsyncMock())):
            await auto_dump.scan_rule(self.rule_id, {10, 30}, asyncio.Semaphore(5))
        client.update_lot.assert_awaited_once_with("1", price=Decimal("76.72"))
        async with self.session() as db:
            rule = await db.get(AutoDumpRule, self.rule_id)
            rule.absolute_min = Decimal("77")
            await db.commit()
        client.update_lot.reset_mock()
        with (patch.object(auto_dump.accounts, "client", AsyncMock(return_value=client)),
              patch.object(auto_dump, "commission_ratio", AsyncMock(return_value=Decimal("1.16")))):
            await auto_dump.scan_rule(self.rule_id, {10, 30}, asyncio.Semaphore(5))
        client.update_lot.assert_not_awaited()

    async def test_same_category_is_fetched_once_per_scan(self):
        async with self.session() as db:
            existing = await db.get(AutoDumpRule, self.rule_id)
            existing.absolute_min = Decimal("70")
            lot = Lot(account_id=1, funpay_lot_id="5", node_id=81, category_title="Game", title="Other", price=Decimal("95"))
            db.add(lot)
            await db.flush()
            db.add(AutoDumpRule(lot_id=lot.id, options='{"convert_commission":false}', category_url="https://funpay.com/lots/81/", absolute_min=Decimal("70"), step=Decimal("1"), is_enabled=True))
            await db.commit()
        client = AsyncMock()
        client.request.return_value = httpx.Response(200, text=HTML)
        with (patch.object(auto_dump.accounts, "client", AsyncMock(return_value=client)),
              patch.object(auto_dump.bus, "log", AsyncMock())):
            await auto_dump.scan_all()
        client.request.assert_awaited_once()
        self.assertEqual(client.update_lot.await_count, 2)


class RobuxVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saves_cookie_encrypted_and_verifies_without_purchase(self):
        saved = {}
        async def get_setting(_key, _default):
            return saved
        async def save_setting(_key, value):
            saved.update(value)
        cookie = "_|WARNING:-DO-NOT-SHARE-THIS.TEST"
        with (patch.object(auto_robux, "get_setting", get_setting), patch.object(auto_robux, "save_setting", save_setting)):
            public = await auto_robux.configure({"universe_id": 1, "game_pass_id": 2, "target_amount": 100, "cover_fee": True, "cookie": cookie})
            self.assertNotIn(cookie, str(saved))
            self.assertNotIn(cookie, str(public))
            real_client = httpx.AsyncClient
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"name": "Test Pass", "priceInRobux": 143}))
            with patch.object(auto_robux.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=transport, **kw)):
                result = await auto_robux.verify_pass()
            self.assertTrue(result["matches"])
            self.assertFalse(result["purchase_available"])
