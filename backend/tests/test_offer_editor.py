"""Bilingual offer editing and batches always create fresh offers."""

import asyncio
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import main
from app.funpay import FunPayClient, FunPayError
from app.lot_editor import create_offers, describe_form, merge_fields, quote_prices, save_form
from app.models import Account, Base, Lot
from app.schemas import LotCopies
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

FORM = """<form><input name="offer_id" value="{offer}"><input name="node_id" value="81">
<input name="csrf_token" value="fresh-csrf"><input name="fields[summary][ru]" value="Русский текст" required maxlength="100">
<input name="fields[summary][en]" value="English text"><textarea name="fields[desc][ru]">Описание</textarea>
<textarea name="fields[desc][en]">Description</textarea>
<textarea name="fields[payment_msg][ru]">RU payment</textarea><textarea name="fields[payment_msg][en]">EN payment</textarea>
<input name="price" value="100"><input name="price_buyer" value="116" readonly>
<input name="amount" value="999"><input name="active" type="checkbox" checked>
<input name="auto_delivery" type="checkbox" value="1" checked><textarea name="secrets">PRIVATE-KEY</textarea>
<input name="fields[images]" type="hidden" value="own-image"><input name="query" type="hidden" value="protocol">
<select name="fields[server]"><option value="one" selected>Server One</option><option value="two">Server Two</option></select></form>"""


class FakeClient:
    csrf = "fresh-csrf"
    json_response = staticmethod(FunPayClient.json_response)

    def __init__(self, fail_at=None):
        self.posts = []
        self.fail_at = fail_at
        self.forms = []

    def offer_lock(self, offer_id):
        return asyncio.Lock()

    async def request(self, method, path, **kwargs):
        if method == "GET":
            self.forms.append(kwargs["params"])
            return httpx.Response(200, text=FORM.format(offer=kwargs["params"].get("offer", "0")))
        self.posts.append(kwargs["data"].copy())
        if self.fail_at == len(self.posts):
            raise FunPayError("Unknown remote outcome")
        return httpx.Response(200, json={"error": False, "done": True})


class FormTests(unittest.TestCase):
    def test_bilingual_fields_and_checkbox_values_round_trip(self):
        metadata, fields = describe_form(FORM.format(offer="123"), offer_id="123")
        self.assertEqual(fields["active"], "on")
        self.assertEqual(fields["auto_delivery"], "1")
        changed = merge_fields(
            metadata,
            fields,
            {"fields[summary][en]": "Edited English", "active": "", "auto_delivery": ""},
        )
        self.assertEqual(changed["fields[summary][ru]"], "Русский текст")
        self.assertEqual(changed["fields[payment_msg][en]"], "EN payment")
        self.assertEqual(changed["fields[summary][en]"], "Edited English")
        with self.assertRaises(FunPayError):
            merge_fields(metadata, fields, {"price_buyer": "200"})
        for price in ("Infinity", "NaN", "-1"):
            with self.assertRaises(FunPayError):
                merge_fields(metadata, fields, {"price": price})


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_done_does_not_claim_saved(self):
        client = FakeClient()
        with patch.object(
            client, "request", AsyncMock(return_value=httpx.Response(200, json={"error": False}))
        ):
            with self.assertRaises(FunPayError):
                await save_form(client, {"offer_id": "123"})

    async def test_payment_quotes_use_funpay_calculation(self):
        client = FakeClient()
        response = httpx.Response(
            200,
            json={
                "methods": [
                    {"name": "Card", "price": "121", "unit": "₽", "pos": 2},
                    {"name": "SBP", "price": "116", "unit": "₽", "pos": 1},
                ]
            },
        )
        with patch.object(client, "request", AsyncMock(return_value=response)) as remote:
            prices = await quote_prices(client, 81, Decimal("100"))
            self.assertEqual(prices[0]["name"], "SBP")
            self.assertEqual(remote.call_args.args, ("POST", "lots/calc"))
            self.assertEqual(remote.call_args.kwargs["data"], {"nodeId": 81, "price": "100"})

    async def test_fresh_form_for_each_copy_and_stop_after_uncertain_result(self):
        client = FakeClient(fail_at=2)
        result = await create_offers(client, 81, 3, {"price": "150", "fields[summary][en]": "Copy"})
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["requested"], 3)
        self.assertTrue(result["error"])
        self.assertEqual(len(client.posts), 2)
        self.assertTrue(all(post["offer_id"] == "0" for post in client.posts))
        self.assertTrue(all(form == {"node": 81} for form in client.forms))

    async def test_copy_existing_offer_preserves_languages_without_reusing_keys(self):
        with tempfile.TemporaryDirectory(prefix="offer-editor-") as directory:
            engine = create_async_engine(
                "sqlite+aiosqlite:///" + (Path(directory) / "offers.db").as_posix()
            )
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session = async_sessionmaker(engine, expire_on_commit=False)
            async with session() as db:
                account = Account(
                    title="Seller",
                    golden_key="encrypted",
                    funpay_id=42,
                    username="Seller",
                    user_agent="test",
                )
                db.add(account)
                await db.flush()
                lot = Lot(
                    account_id=account.id,
                    funpay_lot_id="123",
                    node_id=81,
                    category_title="Game",
                    title="Offer",
                    price=Decimal("100"),
                )
                db.add(lot)
                await db.commit()
                client = FakeClient()
                with (
                    patch.object(main.accounts, "client", AsyncMock(return_value=client)),
                    patch.object(main.accounts, "sync_lots", AsyncMock()),
                ):
                    result = await main.copy_own_lot(
                        lot.id,
                        LotCopies(
                            count=3, fields={"fields[summary][ru]": "Новый текст", "price": "200"}
                        ),
                        db,
                    )
                self.assertEqual(result["created"], 3)
                self.assertEqual(client.forms[0], {"offer": "123"})
                for post in client.posts:
                    self.assertEqual(post["offer_id"], "0")
                    self.assertEqual(post["fields[summary][ru]"], "Новый текст")
                    self.assertEqual(post["fields[summary][en]"], "English text")
                    self.assertEqual(post["fields[payment_msg][en]"], "EN payment")
                    self.assertEqual(post["fields[server]"], "one")
                    self.assertEqual(post["price"], "200")
                    self.assertEqual(post["fields[images]"], "own-image")
                    self.assertEqual(post["secrets"], "")
                    self.assertEqual(post["auto_delivery"], "")
                with (
                    patch.object(main.accounts, "client", AsyncMock(return_value=client)),
                    patch.object(main.bus, "publish", AsyncMock()),
                ):
                    await main.delete_lot(lot.id, db)
                self.assertEqual(client.posts[-1]["offer_id"], "123")
                self.assertEqual(client.posts[-1]["deleted"], "1")
                self.assertIsNone(await db.get(Lot, lot.id))
            await engine.dispose()
