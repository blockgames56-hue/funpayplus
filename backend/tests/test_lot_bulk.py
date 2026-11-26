"""Bulk selection actions never write to unselected offers or fake remote success."""

import asyncio
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from app import main
from app.funpay import FunPayClient, FunPayError, ResponseFormatError
from app.models import Account, Base, Lot, PluginBinding
from app.schemas import LotBulk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_offer_editor import FORM


class LotProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FunPayClient("test-session")
        self.client.csrf = "outdated-token"
        self.posts = []

    async def asyncTearDown(self):
        await self.client.close()

    async def test_update_uses_fresh_form_csrf_ajax_and_preserves_other_fields(self):
        async def remote(method, path, **kwargs):
            if method == "GET":
                return httpx.Response(200, text=FORM.format(offer=123))
            self.posts.append(kwargs)
            return httpx.Response(200, json={"done": True})

        with patch.object(self.client, "request", side_effect=remote):
            await self.client.update_lot("123", price=Decimal("125.50"), active=False)
        post = self.posts[0]
        self.assertEqual(post["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(post["data"]["csrf_token"], "fresh-csrf")
        self.assertEqual(post["data"]["price"], "125.50")
        self.assertEqual(post["data"]["active"], "")
        self.assertEqual(post["data"]["fields[summary][en]"], "English text")
        self.assertEqual(post["data"]["fields[payment_msg][ru]"], "RU payment")
        self.assertEqual(post["data"]["secrets"], "PRIVATE-KEY")

    async def test_html_or_unconfirmed_update_is_not_success_or_retried(self):
        for response in (
            httpx.Response(200, text="<html>Not JSON</html>"),
            httpx.Response(200, json={}),
        ):

            async def remote(method, path, **kwargs):
                return (
                    httpx.Response(200, text=FORM.format(offer=123))
                    if method == "GET"
                    else response
                )

            with patch.object(self.client, "request", side_effect=remote) as request:
                with self.assertRaises(ResponseFormatError):
                    await self.client.update_lot("123", active=True)
                self.assertEqual(request.await_count, 2)

    async def test_raise_accepts_success_without_optional_message(self):
        with patch.object(
            self.client,
            "request",
            AsyncMock(return_value=httpx.Response(200, json={"error": False, "wait": 3600})),
        ):
            result = await self.client.raise_lots(81, 1)
        self.assertTrue(result["raised"])
        self.assertEqual(result["cooldown"], 3600)

    async def test_save_accepts_explicit_no_error_ack_without_done(self):
        async def remote(method, path, **kwargs):
            return (
                httpx.Response(200, text=FORM.format(offer=123))
                if method == "GET"
                else httpx.Response(200, json={"error": False})
            )

        with patch.object(self.client, "request", side_effect=remote):
            await self.client.update_lot("123", active=True)


class BulkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.client = AsyncMock()
        self.patches = [
            patch.object(main.accounts, "client", AsyncMock(return_value=self.client)),
            patch.object(main.bus, "publish", AsyncMock()),
        ]
        for item in self.patches:
            item.start()
        async with self.session() as db:
            account = Account(
                title="Store", golden_key="encrypted", username="Seller", user_agent="Test"
            )
            db.add(account)
            await db.flush()
            rows = [
                Lot(
                    account_id=account.id,
                    funpay_lot_id=str(100 + i),
                    node_id=81 if i < 2 else 82,
                    game_id=1,
                    category_title="Game",
                    title=f"Offer {i}",
                    price=Decimal("100"),
                    is_active=False,
                )
                for i in range(3)
            ]
            db.add_all(rows)
            await db.commit()
            self.ids = [r.id for r in rows]

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()

    async def execute(self, action, ids=None, **extra):
        async with self.session() as db:
            return await main.bulk_lots(
                LotBulk(ids=self.ids if ids is None else ids, action=action, **extra), db
            )

    async def test_price_changes_only_selected_ids_and_deduplicates(self):
        result = await self.execute("price", [self.ids[0], self.ids[0]], percent=10)
        self.assertEqual(len(result), 1)
        self.client.update_lot.assert_awaited_once_with("100", price=Decimal("110.00"))
        async with self.session() as db:
            self.assertEqual((await db.get(Lot, self.ids[0])).price, Decimal("110"))
            self.assertEqual((await db.get(Lot, self.ids[1])).price, Decimal("100"))

    async def test_activate_and_deactivate_are_confirmed_before_local_changes(self):
        await self.execute("activate", [self.ids[1]])
        async with self.session() as db:
            self.assertFalse((await db.get(Lot, self.ids[0])).is_active)
            self.assertTrue((await db.get(Lot, self.ids[1])).is_active)
        await self.execute("deactivate", [self.ids[1]])
        async with self.session() as db:
            self.assertFalse((await db.get(Lot, self.ids[1])).is_active)

    async def test_partial_failure_keeps_failed_price_and_reports_each_id(self):
        self.client.update_lot.side_effect = [None, FunPayError("Отклонено"), None]
        rows = await self.execute("price", percent=20)
        self.assertEqual([r["id"] for r in rows], self.ids)
        self.assertEqual(sum(bool(r.get("ok")) for r in rows), 2)
        async with self.session() as db:
            self.assertEqual((await db.get(Lot, self.ids[1])).price, Decimal("100"))

    async def test_html_response_stops_remaining_batch_without_replaying_writes(self):
        self.client.update_lot.side_effect = ResponseFormatError("FunPay вернул HTML вместо JSON")
        rows = await self.execute("activate")
        self.assertEqual(self.client.update_lot.await_count, 1)
        self.assertTrue(all(r.get("skipped") for r in rows[1:]))
        async with self.session() as db:
            self.assertTrue(all(not r.is_active for r in (await db.scalars(select(Lot))).all()))

    async def test_raising_category_is_called_once_and_reports_each_selected_lot(self):
        with patch.object(
            main.accounts,
            "raise_group",
            AsyncMock(return_value={"raised": True, "cooldown": 3600, "message": ""}),
        ) as raise_group:
            rows = await self.execute("raise")
        self.assertEqual(raise_group.await_count, 2)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["ok"] for r in rows))

    async def test_missing_ids_are_reported_instead_of_silent_success(self):
        rows = await self.execute("activate", [999, self.ids[0]])
        self.assertIn("error", rows[0])
        self.assertTrue(rows[1]["ok"])

    async def test_delete_waits_for_remote_ack_and_only_deletes_selected_rows(self):
        posts = []
        client = FunPayClient("test-session")

        async def remote(method, path, **kwargs):
            if method == "GET":
                return httpx.Response(200, text=FORM.format(offer=kwargs["params"]["offer"]))
            posts.append(kwargs)
            return httpx.Response(200, json={"done": True})

        try:
            with (
                patch.object(main.accounts, "client", AsyncMock(return_value=client)),
                patch.object(client, "request", side_effect=remote),
            ):
                rows = await self.execute("delete", [self.ids[1]])
            self.assertTrue(rows[0]["deleted"])
            self.assertEqual(posts[0]["data"]["offer_id"], "101")
            self.assertEqual(posts[0]["data"]["deleted"], "1")
            self.assertEqual(posts[0]["headers"]["X-Requested-With"], "XMLHttpRequest")
            async with self.session() as db:
                self.assertIsNone(await db.get(Lot, self.ids[1]))
                self.assertIsNotNone(await db.get(Lot, self.ids[0]))
                self.assertIsNotNone(await db.get(Lot, self.ids[2]))
        finally:
            await client.close()

    async def test_delete_failure_keeps_local_lot(self):
        with (
            patch.object(main, "save_form", AsyncMock(side_effect=FunPayError("Не подтверждено"))),
            patch.object(main, "load_form", AsyncMock(return_value=({}, {}))),
        ):
            self.client.offer_lock = lambda _: asyncio.Lock()
            rows = await self.execute("delete", [self.ids[0]])
        self.assertIn("error", rows[0])
        async with self.session() as db:
            self.assertIsNotNone(await db.get(Lot, self.ids[0]))

    async def test_plugin_binding_blocks_delete_before_remote_request(self):
        async with self.session() as db:
            db.add(PluginBinding(kind="steam-rent", lot_id=self.ids[0]))
            await db.commit()
        rows = await self.execute("delete", [self.ids[0]])
        self.assertIn("привязку", rows[0]["error"])
        main.accounts.client.assert_not_awaited()
