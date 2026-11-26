"""Workspace operations are tested against mocked networks and isolated SQLite."""

import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import test_kosell as fixtures
from app import (
    kosell,
    services,
)
from app import (
    kosell_extensions as ext,
)
from app import (
    kosell_sync as sync,
)
from app import (
    kosell_workspace as ws,
)
from app.integrated_config import packed, unpacked
from app.models import KosellOperation, KosellRental, Lot, Order, PluginBinding, utcnow
from fastapi import HTTPException
from sqlalchemy import select


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.KoSellTests.asyncSetUp(self)
        for module in (ws, ext):
            item = patch.object(module, "Session", self.Session)
            item.start()
            self.patches.append(item)

    async def asyncTearDown(self):
        await ws.stop()
        await fixtures.KoSellTests.asyncTearDown(self)

    seed_rental = fixtures.KoSellTests.seed_rental

    def studio(self, **kwargs):
        return ws.Studio(source_lot_id=1, product_ids=[5], hours=[2], markup=40, **kwargs)

    async def preview(self):
        items = [
            {"id": 5, "name": "Steam", "available_accounts": 2, "min_hours": 1, "max_hours": 72}
        ]
        with (
            patch.object(kosell, "products", AsyncMock(return_value=items)),
            patch.object(kosell, "price", AsyncMock(return_value=self.quote)),
        ):
            return await ws.studio_preview(self.studio())

    async def test_workspace_is_separate_and_never_exposes_keys(self):
        data = await ws.workspace()
        self.assertIn("finance", data)
        self.assertIn("studio_defaults", data)
        self.assertNotIn("test-key", str(data))
        self.assertEqual(len(data["bindings"]), 1)

    async def test_saved_text_template_crud_keeps_both_languages(self):
        body = ws.SavedTemplate(
            name="Daily", hours=[1, 24], title="{game} · {duration_hours}", title_en="{game} rental"
        )
        result = await ws.save_template(body)
        row = (await ws.templates())[0]
        self.assertEqual(row["title_en"], "{game} rental")
        self.assertNotIn("source_lot_id", row)
        body.name = "Renamed"
        await ws.save_template(body, result["id"])
        self.assertEqual((await ws.templates())[0]["name"], "Renamed")
        await ws.remove_template(result["id"])
        self.assertEqual(await ws.templates(), [])

    async def test_studio_long_description_and_invalid_tags_have_correct_limits(self):
        ws.validate_texts(ws.SavedTemplate(name="Long", description="x" * 12000))
        with self.assertRaises(HTTPException) as error:
            ws.validate_texts(ws.SavedTemplate(name="Bad", title="{unknown}"))
        self.assertEqual(error.exception.status_code, 422)

    async def test_preview_renders_fields_and_never_posts_to_funpay(self):
        result = await self.preview()
        self.assertEqual(result["rows"][0]["price"], "70.00")
        self.assertIn("2", result["rows"][0]["texts"]["title"])
        self.assertNotIn("{game}", result["rows"][0]["texts"]["description_en"])
        self.client.request.assert_not_awaited()

    async def test_create_claim_is_durable_and_blocks_double_submit(self):
        batch = await self.preview()
        with patch.object(ws, "execute_batch", AsyncMock()) as execute:
            await ws.create_batch(batch["id"])
            import asyncio

            await asyncio.gather(*list(ws.tasks.values()))
            with self.assertRaises(HTTPException) as error:
                await ws.create_batch(batch["id"])
            self.assertEqual(error.exception.status_code, 409)
            execute.assert_awaited_once()

    async def test_batch_creates_inactive_offer_and_persists_binding(self):
        batch = await self.preview()
        # Remove initial mapping to leave only the generated binding.
        async with self.Session() as db:
            await db.delete(await db.get(PluginBinding, 1))
            await db.commit()
        fields = {
            "offer_id": "0",
            "node_id": "1",
            "price": "1",
            "amount": "1",
            "active": "on",
            "fields[summary][ru]": "old",
            "fields[summary][en]": "old",
            "fields[desc][ru]": "old",
        }
        controls = [
            {
                "name": k,
                "kind": "checkbox" if k == "active" else "text",
                "readonly": False,
                "options": [],
                "required": False,
            }
            for k in fields
        ]
        meta = {"node_id": 1, "controls": controls}

        async def sync_lots(_):
            async with self.Session() as db:
                db.add(
                    Lot(
                        id=2,
                        account_id=1,
                        funpay_lot_id="20",
                        node_id=1,
                        category_title="Steam",
                        title=batch["rows"][0]["texts"]["title"],
                        price=Decimal("70"),
                        is_active=False,
                    )
                )
                await db.commit()

        self.client.get_lots.return_value = [{"funpay_lot_id": "10"}]
        with (
            patch.object(ws, "load_form", AsyncMock(return_value=(meta, fields))),
            patch.object(ws, "save_form", AsyncMock()) as save,
            patch.object(ws.accounts, "sync_lots", sync_lots),
        ):
            await ws.execute_batch(batch["id"])
            self.assertEqual(save.call_args.args[1]["active"], "")
            self.assertEqual(save.call_args.args[1]["offer_id"], "0")
            self.assertEqual((await ws.batch_status(batch["id"]))["state"], "done")
            async with self.Session() as db:
                binding = await db.scalar(select(PluginBinding).where(PluginBinding.lot_id == 2))
                self.assertEqual(binding.service_id, "5")
            await ws.execute_batch(batch["id"])
            save.assert_awaited_once()

    async def test_markup_changes_only_opted_in_lots(self):
        with patch.object(kosell, "price", AsyncMock(return_value=self.quote)):
            self.assertEqual((await ws.markup_preview(ws.MarkupPreview(markup=50)))["rows"], [])
            await services.save_setting("kosell:binding:1", {"auto_price": True, "markup": 40})
            preview = await ws.markup_preview(ws.MarkupPreview(markup=50))
            self.assertEqual(preview["rows"][0]["after"], "75.00")
            await ws.apply_markup(preview["id"])
            self.assertEqual((await services.get_setting("kosell:binding:1", {}))["markup"], 50)
            self.client.update_lot.assert_not_awaited()

    async def test_stock_restore_only_plugin_owned_hides_and_manual_override(self):
        product = {"id": 5, "name": "Steam", "available_accounts": 0}
        with (
            patch.object(kosell, "products", AsyncMock(return_value=[product])),
            patch.object(kosell, "price", AsyncMock(return_value=self.quote)),
        ):
            await sync.sync()
            self.assertIn("1", await services.get_setting("kosell:hidden", {}))
            product["available_accounts"] = 1
            await sync.manual_override(1)
            await sync.sync()
            async with self.Session() as db:
                self.assertFalse((await db.get(Lot, 1)).is_active)
            await services.save_setting("kosell:hidden", {"1": {"reason": "stock"}})
            await sync.sync()
            async with self.Session() as db:
                self.assertTrue((await db.get(Lot, 1)).is_active)

    async def test_low_balance_alert_only_once_until_replenishment(self):
        config = await services.get_setting("kosell", {})
        await services.save_setting("kosell", {**config, "balance_threshold": 100})
        with patch.object(
            kosell, "api", AsyncMock(return_value={"balance_rub": 50, "balance_usd": 1})
        ):
            await sync.balance_check()
            await sync.balance_check()
            kosell.notify.assert_awaited_once()

    async def test_extension_timeout_is_never_replayed(self):
        await self.seed_rental()
        async with self.Session() as db:
            op = KosellOperation(
                rental_id=1,
                event_key="review:1",
                kind="review",
                hours=1,
                payload=packed({"key": "test-key", "currency": "RUB"}),
            )
            db.add(op)
            await db.commit()
            ident = op.id
        self.client.order_details.return_value["status"] = "closed"
        with patch.object(
            kosell,
            "api",
            AsyncMock(
                side_effect=[{"total_rub": 10, "total_usd": 0.1}, kosell.KoSellError("timeout")]
            ),
        ) as api:
            await ext.execute(ident)
            await ext.execute(ident)
            self.assertEqual(api.await_count, 2)
            async with self.Session() as db:
                self.assertEqual((await db.get(KosellOperation, ident)).state, "MANUAL_CHECK")

    async def test_extension_success_tracks_receipt_and_updates_expiry(self):
        await self.seed_rental()
        async with self.Session() as db:
            op = KosellOperation(
                rental_id=1,
                event_key="review:1",
                kind="review",
                hours=1,
                payload=packed({"key": "test-key", "currency": "RUB"}),
            )
            db.add(op)
            await db.commit()
            ident = op.id
        self.client.order_details.return_value["status"] = "closed"
        with patch.object(
            kosell,
            "api",
            AsyncMock(
                side_effect=[
                    {"total_rub": 10, "total_usd": 0.1},
                    {
                        "success": True,
                        "rental_uid": "uid-1",
                        "hours_added": 1,
                        "new_expires_at": (self.end + timedelta(hours=1)).isoformat(),
                        "price_paid": 10,
                        "currency": "RUB",
                    },
                ]
            ),
        ):
            await ext.execute(ident)
        async with self.Session() as db:
            op = await db.get(KosellOperation, ident)
            self.assertEqual(op.state, "DONE")
            self.assertEqual(unpacked(op.payload)["receipt"]["amount"], "10")
            self.assertGreater(kosell.aware((await db.get(KosellRental, 1)).end_time), self.end)

    async def test_manual_access_requires_live_paid_order(self):
        await self.seed_rental()
        self.client.order_details.return_value["status"] = "refunded"
        with patch.object(kosell, "api", AsyncMock()) as api:
            with self.assertRaises(HTTPException):
                await ws.rental_access(1)
            api.assert_not_awaited()

    async def test_time_command_checks_sender_before_provider_calls(self):
        await self.seed_rental()
        self.assertFalse(await ext.command(1, 1, {"text": "!time", "sender_id": 999}))
        await ext.command(1, 1, {"text": "!time", "sender_id": 2})
        self.assertIn("мин", kosell.accounts.send.call_args.args[1])

    async def test_paid_extension_intent_claims_order_once_without_new_rental(self):
        from app.models import Chat

        await self.seed_rental()
        async with self.Session() as db:
            rental = await db.get(KosellRental, 1)
            await ext.mark_intent(rental)
            order = Order(
                id=2,
                account_id=1,
                funpay_order_id="EXTEND",
                funpay_lot_id="10",
                description="Extend",
                sum=Decimal("100"),
                currency="RUB",
                buyer_id=2,
                buyer_name="Buyer",
                status="paid",
                created_at=utcnow(),
            )
            db.add(order)
            await db.commit()
            op = await ext.consume_intent(
                db, order, await db.get(Chat, 1), await db.get(PluginBinding, 1), 2
            )
            self.assertTrue(op)
            self.assertIsNone(
                await ext.consume_intent(
                    db, order, await db.get(Chat, 1), await db.get(PluginBinding, 1), 2
                )
            )
            self.assertEqual(len((await db.scalars(select(KosellRental))).all()), 1)

    async def test_review_bonus_is_claimed_once(self):
        await self.seed_rental()
        async with self.Session() as db:
            (await db.get(Order, 1)).status = "closed"
            await db.commit()
        config = await services.get_setting("kosell", {})
        await services.save_setting("kosell", {**config, "review_bonus": True})
        self.client.order_details.return_value.update(status="closed", review={"rating": 5})
        with patch.object(ext, "execute", AsyncMock()) as execute:
            await ext.review_bonus()
            await services.save_setting("kosell:review_cursor", 2147483647)
            await ext.review_bonus()
            execute.assert_awaited_once()

    async def test_ambiguous_creation_stays_blocked_until_reconciled(self):
        batch = await self.preview()
        row = batch["rows"][0]
        row["state"] = "manual_check"
        row["before_ids"] = ["10"]
        batch["state"] = "manual_check"
        await services.save_setting("kosell:studio:" + batch["id"], batch)
        with self.assertRaises(HTTPException):
            await ws.create_batch(batch["id"])
        with self.assertRaises(HTTPException):
            await ws.continue_batch(batch["id"])
        self.client.request.assert_not_awaited()

    async def test_failure_after_creation_ack_does_not_make_position_retryable(self):
        batch = await self.preview()
        fields = {
            "offer_id": "0",
            "node_id": "1",
            "price": "1",
            "amount": "1",
            "active": "on",
            "fields[summary][ru]": "old",
        }
        meta = {
            "node_id": 1,
            "controls": [
                {
                    "name": k,
                    "kind": "checkbox" if k == "active" else "text",
                    "readonly": False,
                    "options": [],
                    "required": False,
                }
                for k in fields
            ],
        }
        self.client.get_lots.return_value = [{"funpay_lot_id": "10"}]

        async def uncertain(row):
            row["state"] = "done"
            raise OSError("local write failed after remote ack")

        with (
            patch.object(ws, "load_form", AsyncMock(return_value=(meta, fields))),
            patch.object(ws, "save_form", AsyncMock()) as save,
            patch.object(ws.accounts, "sync_lots", AsyncMock()),
            patch.object(ws, "reconcile_row", uncertain),
        ):
            await ws.execute_batch(batch["id"])
            result = await ws.batch_status(batch["id"])
            self.assertEqual(result["rows"][0]["state"], "manual_check")
            with self.assertRaises(HTTPException):
                await ws.continue_batch(batch["id"])
            save.assert_awaited_once()

    async def test_finance_refunds_remove_revenue_and_do_not_invent_expenses(self):
        await self.seed_rental()
        stats = await ws.finance()
        self.assertEqual(stats["currencies"]["RUB"]["revenue"], "100.00")
        self.assertEqual(stats["unconfirmed_costs"], 1)
        async with self.Session() as db:
            (await db.get(Order, 1)).status = "refunded"
            await db.commit()
        self.assertEqual((await ws.finance())["currencies"]["RUB"]["revenue"], "0")

    async def test_guard_template_supports_login_and_rental_variables(self):
        from app.models import MessageTemplate

        await self.seed_rental()
        async with self.Session() as db:
            rental = await db.get(KosellRental, 1)
            payload = unpacked(rental.payload)
            payload["login"] = "steam_example"
            rental.payload = packed(payload)
            db.add(
                MessageTemplate(
                    plugin="kosell",
                    key="kosell_guard_success",
                    text="{login}: {code}, {duration_hours} ч, UID {rental_uid}",
                )
            )
            await db.commit()
        with patch.object(
            kosell, "api", AsyncMock(return_value={"success": True, "code": "ABCDE"})
        ):
            await kosell.handle_message(
                1, 1, {"text": "!код steam_example", "sender_id": 2, "id": 55}
            )
            await kosell.tick()
            self.assertIn("steam_example: ABCDE", kosell.accounts.send.call_args.args[1])
            self.assertIn("UID uid-1", kosell.accounts.send.call_args.args[1])
