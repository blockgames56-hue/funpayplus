"""Original KOSellRent contracts and host flows; all remote services are mocked."""

import ast
import asyncio
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from app import kosellrent as runtime
from app import kosellrent_api as api
from app import services
from app.kosellrent_schema import validate_settings
from app.kosellrent_source import SOURCE, SOURCE_SHA256, load_source
from app.models import Account, Base, Chat, Lot, Order, PluginBinding, Settings
from app.security import require_auth
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.module = load_source()

    def test_original_source_checksum_and_function_bodies(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        tree = ast.parse(SOURCE.read_bytes())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                self.assertEqual(
                    getattr(self.module, node.name).__code__.co_firstlineno, node.lineno
                )

    def test_rent_preserves_payload_headers_and_idempotency(self):
        client = self.module.KOSellAPI("test-key")
        client.s = MagicMock()
        response = MagicMock(status_code=201)
        response.json.return_value = {"rental_uid": "r-1"}
        client.s.request.return_value = response
        self.assertEqual(client.rent(7, 24, "USD", "fp-ORDER"), ({"rental_uid": "r-1"}, None))
        client.s.request.assert_called_once_with(
            "POST",
            "https://www.kosell.store/api/v1/rental/rent",
            json={"product_id": 7, "duration_hours": 24, "currency": "USD"},
            headers={"Idempotency-Key": "fp-ORDER"},
            timeout=25,
        )

    def test_supplier_methods_preserve_paths_and_error_contract(self):
        client = self.module.KOSellAPI("key")
        response = MagicMock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(client, "_req", return_value=response) as req:
            client.extend("uid", 3, "RUB", "extension-id")
            req.assert_called_with(
                "POST",
                "/rental/uid/extend",
                json={"hours": 3, "currency": "RUB"},
                headers={"Idempotency-Key": "extension-id"},
            )
            client.delivery_preferences()
            req.assert_called_with("GET", "/api/rental-delivery/preferences")
            client.delivery_detail("uid")
            req.assert_called_with("GET", "/api/rental-delivery/uid")
            client.rotate_password_status("uid")
            req.assert_called_with("GET", "/rental/uid/rotate-password/status")
            response.status_code = 400
            response.json.return_value = {"error": "no_stock"}
            self.assertEqual(client.rent(7, 3, "RUB"), (None, "no_stock"))

    def test_settings_validation_rejects_wrong_types_and_nonfinite_numbers(self):
        for values in (
            {"auto_refund": "false"},
            {"poll_sec": float("nan")},
            {"poll_sec": 2},
            {"enabled": True},
            {"currency": "EUR"},
            {"texts": {"unknown": "text"}},
            {"api_key": 123},
            {"product_markup_percent": {"5": float("inf")}},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_settings(values, self.module)

    def test_template_uses_original_placeholders(self):
        title = self.module._apply_placeholders("KOSGAME / KOSTIME", "Portal", 3)
        self.assertIn("Portal", title)
        self.assertNotIn("KOSTIME", title)

    def test_all_original_user_toggles_parameters_and_texts_have_controls(self):
        from app.kosellrent_schema import GROUPS

        controls = {key for _, fields in GROUPS.values() for key in fields} | {"enabled"}
        node = next(
            n
            for n in ast.parse(SOURCE.read_bytes()).body
            if isinstance(n, ast.FunctionDef) and n.name == "_full_toggles_kb"
        )
        sections = ast.literal_eval(
            next(
                n.value
                for n in node.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "sections" for t in n.targets)
            )
        )
        toggles = {key for _, items in sections for key, _ in items}
        self.assertTrue(toggles <= controls, toggles - controls)
        self.assertTrue(
            self.module.PARAM_DESC.keys() <= controls, self.module.PARAM_DESC.keys() - controls
        )
        self.assertTrue(self.module.DEFAULT_TEXTS.keys() <= self.module.TEXT_LABELS.keys())


class HostFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kosell-rent-tests-")
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix()
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.patches = [
            patch.object(module, "Session", self.Session) for module in (runtime, api, services)
        ]
        # Each IsolatedAsyncioTestCase owns a new loop; locks contended by the
        # queue regressions must not remain bound to the previous test's loop.
        self.patches += [
            patch.object(runtime, "lock", asyncio.Lock()),
            patch.object(runtime, "hosts_lock", asyncio.Lock()),
            patch.object(api, "batch_lock", asyncio.Lock()),
        ]
        self.patches += [
            patch.object(runtime.bus, "log", AsyncMock()),
            patch.object(services.telegram, "notify", AsyncMock()),
        ]
        for item in self.patches:
            item.start()
        runtime.instances.clear()
        runtime.stopping = False
        api.operation_task = None
        async with self.Session() as db:
            db.add_all(
                [
                    Account(id=i, title=f"Seller {i}", golden_key="encrypted", user_agent="offline")
                    for i in (1, 2)
                ]
            )
            db.add(
                Chat(
                    id=1,
                    account_id=1,
                    funpay_chat_id="123",
                    interlocutor_id=20,
                    interlocutor_name="Buyer",
                )
            )
            db.add(
                Lot(
                    id=1,
                    account_id=1,
                    funpay_lot_id="100",
                    node_id=89,
                    category_title="Steam",
                    title="Portal rent 3 часа",
                    price=100,
                )
            )
            db.add(
                Order(
                    id=1,
                    account_id=1,
                    funpay_order_id="ORDER1",
                    funpay_lot_id="100",
                    description="Portal rent 3 часа",
                    sum=100,
                    currency="RUB",
                    buyer_id=20,
                    buyer_name="Buyer",
                    status="paid",
                )
            )
            await db.commit()
        await api.save_config(api.Config(account_id=1, settings={"api_key": "private-test-key"}))
        self.host = runtime.instances[1]
        self.module = self.host.module
        self.supplier = MagicMock()
        self.supplier.products.return_value = [{"id": 7, "name": "Portal", "available_accounts": 5}]
        self.supplier.balance.return_value = {"balance_rub": 1000, "balance_usd": 10}
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.supplier.rent.return_value = (
            {"rental_uid": "uid-1", "steam_login": "renter", "expires_at": future},
            None,
        )
        self.supplier.credentials.return_value = {"steam_password": "private-password"}
        self.supplier.extend.return_value = ({"new_expires_at": future}, None)
        self.supplier.calculate_price.return_value = {"total_rub": 5}
        self.supplier.calculate_extend_price.return_value = {"total_rub": 4}
        self.module._get_api = lambda: self.supplier
        self.sent = MagicMock()
        self.module._send = self.sent
        self.original = SimpleNamespace(
            id="ORDER1",
            seller_id=10,
            status=SimpleNamespace(name="PAID"),
            buyer_username="Buyer",
            buyer_id=20,
            chat_id="123",
            amount=1,
            price=100,
            description="Portal rent 3 часа",
            html="",
            subcategory=SimpleNamespace(id=89),
        )
        sdk = SimpleNamespace(
            get_order=lambda ident: copy.copy(self.original),
            get_chat_by_name=lambda *a, **kw: SimpleNamespace(id="123"),
            refund=MagicMock(),
        )
        self.client = SimpleNamespace(
            api_account=sdk, user_id=10, call=AsyncMock(side_effect=lambda f, *a, **kw: f(*a, **kw))
        )
        self.client_patch = patch.object(
            services.accounts, "client", AsyncMock(return_value=self.client)
        )
        self.client_patch.start()
        await api.save_mapping(api.Mapping(lot_id=1, product_id=7, hours=3))
        await runtime.toggle(True)

    async def asyncTearDown(self):
        await api.stop_operation()
        await runtime.stop()
        runtime.instances.clear()
        self.client_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_delivery_runs_original_rental_and_never_repeats_purchase(self):
        self.assertTrue(await runtime.ensure_for_order(1))
        self.supplier.rent.assert_called_once_with(7, 3, "RUB", idempotency_key="fp-ORDER1")
        self.assertIn("private-password", self.sent.call_args_list[0].args[1])
        self.assertEqual(self.module.RENTALS["123"][0]["uid"], "uid-1")
        self.assertFalse(await runtime.ensure_for_order(1))
        self.assertEqual(self.supplier.rent.call_count, 1)
        async with self.Session() as db:
            self.assertEqual((await db.get(Order, 1)).delivery_state, runtime.ID)

    async def test_state_encrypted_and_restored_without_plain_api_key(self):
        await runtime.ensure_for_order(1)
        async with self.Session() as db:
            data = "\n".join((await db.scalars(select(Settings.value))).all())
        self.assertNotIn("private-test-key", data)
        self.assertNotIn("renter", data)
        new_host = runtime.Host(1)
        await new_host.hydrate()
        self.assertEqual(new_host.module.SETTINGS["api_key"], "private-test-key")
        self.assertEqual(new_host.module.RENTALS["123"][0]["uid"], "uid-1")
        self.assertIn("ORDER1", new_host.module.HANDLED)

    async def test_public_state_has_no_key_and_scopes_lots(self):
        state = await api.workspace()
        self.assertNotIn("private-test-key", json.dumps(state))
        self.assertTrue(state["has_api_key"])
        self.assertEqual(len(state["lots"]), 1)
        self.assertEqual(
            {g["id"] for g in state["groups"]}, {"delivery", "stock", "buyers", "creation"}
        )

    async def test_repeat_order_extends_original_rental(self):
        await runtime.ensure_for_order(1)
        async with self.Session() as db:
            db.add(
                Order(
                    id=2,
                    account_id=1,
                    funpay_order_id="ORDER2",
                    funpay_lot_id="100",
                    description="Portal rent 3 часа",
                    sum=100,
                    currency="RUB",
                    buyer_id=20,
                    buyer_name="Buyer",
                    status="paid",
                )
            )
            await db.commit()
        self.original.id = "ORDER2"
        await runtime.ensure_for_order(2)
        self.assertEqual(self.supplier.rent.call_count, 1)
        self.supplier.extend.assert_called_once_with(
            "uid-1", 3, "RUB", idempotency_key="fp-ext-uid-1-1-ORDER2"
        )

    async def test_multiple_quantity_waits_and_keeps_original_decision(self):
        self.original.amount = 2
        await runtime.ensure_for_order(1)
        self.supplier.rent.assert_not_called()
        self.assertIn("ORDER1", self.module.PENDING_QTY)
        self.assertTrue(
            await runtime.handle_message(1, 1, {"text": "нет", "sender_id": 20, "is_my": False})
        )
        self.supplier.rent.assert_called_once_with(7, 6, "RUB", idempotency_key="fp-ORDER1")

    async def test_disabled_and_wrong_account_never_purchase(self):
        await runtime.toggle(False)
        self.assertFalse(await runtime.ensure_for_order(1))
        self.assertFalse(await runtime.handle_message(1, 1, {"text": "!друг"}))
        await runtime.toggle(True)
        async with self.Session() as db:
            order = await db.get(Order, 1)
            order.account_id = 2
            await db.commit()
        self.assertFalse(await runtime.ensure_for_order(1))
        self.supplier.rent.assert_not_called()

    async def test_review_bonus_uses_original_idempotency_and_is_not_repeated(self):
        await runtime.ensure_for_order(1)
        self.module.SETTINGS["review_bonus_enabled"] = True
        payload = {
            "text": "Покупатель Buyer оставил отзыв: 5 звезд",
            "funpay_message_id": 20,
            "is_my": False,
        }
        self.assertTrue(await runtime.handle_message(1, 1, payload, system=True))
        await runtime.handle_message(1, 1, payload, system=True)
        self.supplier.extend.assert_called_once_with(
            "uid-1", 3, "RUB", idempotency_key="fp-ext-uid-1-bonus"
        )

    async def test_lot_conflict_and_foreign_account_rejected(self):
        async with self.Session() as db:
            binding = await db.scalar(select(PluginBinding))
            binding.kind = "steam-rent"
            await db.commit()
        with self.assertRaises(HTTPException) as error:
            await api.save_mapping(api.Mapping(lot_id=1, product_id=7, hours=3))
        self.assertEqual(error.exception.status_code, 409)
        async with self.Session() as db:
            lot = await db.get(Lot, 1)
            lot.account_id = 2
            await db.commit()
        with self.assertRaises(HTTPException):
            await api.save_mapping(api.Mapping(lot_id=1, product_id=7, hours=3))

    async def test_mapping_delete_keeps_running_rentals(self):
        await runtime.ensure_for_order(1)
        await api.remove_mapping(100)
        self.assertEqual(self.module.MAPPINGS, [])
        self.assertEqual(len(self.module.RENTALS["123"]), 1)
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(PluginBinding)))

    async def test_blank_key_preserves_saved_key_and_templates(self):
        await api.save_config(
            api.Config(
                account_id=1,
                settings={"api_key": "", "texts": {"text_delivery": "Account {login} {password}"}},
            )
        )
        self.assertEqual(self.module.SETTINGS["api_key"], "private-test-key")
        self.assertEqual(
            self.module.SETTINGS["texts"]["text_delivery"], "Account {login} {password}"
        )

    async def test_plan_required_and_running_operation_blocks_settings(self):
        with self.assertRaises(HTTPException):
            await api.operation("create")
        gate = asyncio.Event()
        api.operation_task = asyncio.create_task(gate.wait())
        with self.assertRaises(HTTPException) as error:
            await api.save_config(api.Config(account_id=1))
        self.assertEqual(error.exception.status_code, 409)
        gate.set()
        await api.operation_task

    async def test_api_authentication_required(self):
        app = FastAPI()
        app.include_router(api.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/plugins/kosell-rent")
            self.assertEqual(response.status_code, 401)
            app.dependency_overrides[require_auth] = lambda: True
            response = await client.get("/api/plugins/kosell-rent")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("private-test-key", response.text)

    async def test_source_friend_mode_supported_before_first_purchase(self):
        self.assertTrue(await runtime.handle_message(1, 1, {"text": "!друг", "sender_id": 20}))
        self.assertTrue(self.module._friend_active("123"))

    async def test_claimed_order_does_not_fall_through_after_supplier_error(self):
        self.supplier.rent.return_value = (None, "insufficient_balance")
        self.assertTrue(await runtime.ensure_for_order(1))
        self.assertTrue(self.sent.called)
        async with self.Session() as db:
            self.assertEqual((await db.get(Order, 1)).delivery_state, runtime.ID)

    async def test_key_change_blocked_for_active_rentals(self):
        await runtime.ensure_for_order(1)
        with self.assertRaises(HTTPException) as error:
            await api.save_config(api.Config(account_id=1, settings={"api_key": "other-key"}))
        self.assertEqual(error.exception.status_code, 409)

    async def test_account_switch_blocked_for_active_rentals(self):
        await runtime.ensure_for_order(1)
        await runtime.toggle(False)
        with self.assertRaises(HTTPException) as error:
            await api.save_config(api.Config(account_id=2, settings={"api_key": "other"}))
        self.assertEqual(error.exception.status_code, 409)

    async def test_queued_order_cannot_start_after_shutdown(self):
        async with runtime.lock:
            pending = asyncio.create_task(runtime.ensure_for_order(1))
            await asyncio.sleep(0)
            runtime.stopping = True
        self.assertFalse(await pending)
        self.supplier.rent.assert_not_called()

    async def test_host_creation_is_singleton_during_concurrent_load(self):
        first, second = await asyncio.gather(runtime.host_for(2), runtime.host_for(2))
        self.assertIs(first, second)

    async def test_refresh_does_not_report_stale_catalog_as_success(self):
        await api.refresh()
        self.supplier.products.return_value = None
        with self.assertRaises(HTTPException):
            await api.refresh()

    async def test_hiding_reports_failed_supplier_write(self):
        with patch.object(self.module, "_set_lot_active", return_value=False):
            with self.assertRaises(HTTPException):
                await api.lot_action(100, "hide")

    async def test_partial_text_update_preserves_other_message_templates(self):
        await api.save_config(
            api.Config(account_id=1, settings={"texts": {"text_delivery": "Custom"}})
        )
        self.assertEqual(
            self.module.SETTINGS["texts"]["text_extension"],
            self.module.DEFAULT_TEXTS["text_extension"],
        )

    async def test_autocreate_does_not_adopt_other_plugins_lot(self):
        async with self.Session() as db:
            binding = await db.scalar(select(PluginBinding))
            binding.kind = "steam-rent"
            await db.commit()
        result = await runtime.run_sync(
            self.module._append_mapping_for_lot,
            "100",
            {
                "product_id": 8,
                "hours": 3,
                "product_name": "Other",
                "subcategory_id": 89,
            },
        )
        self.assertFalse(result)
        self.assertEqual(len(self.module.MAPPINGS), 1)

    async def test_catalog_reads_encrypted_readiness_without_starting_engine(self):
        from app import plugin_catalog

        with patch.object(plugin_catalog, "Session", self.Session):
            result = await plugin_catalog.catalog()
        state = next(row for row in result if row["id"] == runtime.ID)
        self.assertTrue(state["enabled"])
        self.assertTrue(state["configured"])
        self.assertNotIn("private-test-key", json.dumps(result))

    async def test_auto_lot_registry_is_synchronized(self):
        async with self.Session() as db:
            binding = await db.scalar(select(PluginBinding))
            await db.delete(binding)
            await db.commit()
        await api.sync_registry(self.host)
        async with self.Session() as db:
            binding = await db.scalar(select(PluginBinding))
            self.assertEqual(
                (binding.kind, binding.service_id, binding.quantity), (runtime.ID, "7", 3)
            )

    async def test_deleted_seller_cannot_leave_credentials_for_reused_id(self):
        from app.account_workspace import remove_account_settings

        async with self.Session() as db:
            await remove_account_settings(db, 1)
            await db.commit()
        self.assertNotIn(1, runtime.instances)
        async with self.Session() as db:
            rows = (
                await db.scalars(
                    select(Settings.key).where(Settings.key.startswith("kosell-rent:state:1:"))
                )
            ).all()
            self.assertEqual(rows, [])
        self.assertEqual(
            await services.get_setting(runtime.ID), {"enabled": False, "account_id": None}
        )

    async def test_order_without_summary_lot_id_uses_original_offer_link(self):
        async with self.Session() as db:
            order = await db.get(Order, 1)
            order.funpay_lot_id = None
            await db.commit()
        self.original.html = '<a href="/lots/offer?id=100">Offer</a>'
        self.module.MAPPINGS[0]["title_keyword"] = ""
        self.assertTrue(await runtime.ensure_for_order(1))
        self.supplier.rent.assert_called_once()

    async def test_manual_mapping_keeps_cardinal_title_fallback_without_offer_id(self):
        self.assertEqual(self.module.MAPPINGS[0]["title_keyword"], "Portal rent 3 часа")
        async with self.Session() as db:
            order = await db.get(Order, 1)
            order.funpay_lot_id = None
            await db.commit()
        self.assertTrue(await runtime.ensure_for_order(1))
        self.supplier.rent.assert_called_once()

    async def test_per_game_plan_forwards_temporary_hours_without_changing_template(self):
        template = {"durations": [3, 24], "price_mode": "markup", "summary_ru": "KOSGAME KOSTIME"}
        self.module.SETTINGS["ac_template"] = copy.deepcopy(template)
        with patch.object(self.module, "_build_plan", return_value=[{"hours": 7}]) as build:
            result = await api.plan(api.PlanOptions(product_id=7, durations=[7, 48]))
        build.assert_called_once_with(1, product_id=7, durations=[7, 48])
        self.assertEqual(result, {"count": 1})
        self.assertEqual(self.module.SETTINGS["ac_template"], template)

    async def test_invalid_temporary_hours_are_rejected(self):
        with self.assertRaises(HTTPException):
            await api.plan(api.PlanOptions(durations=[0]))

    async def test_price_preview_uses_original_calculations_and_competitor_filter(self):
        items = [
            {
                "product_id": 7,
                "game_name": "Portal",
                "hours": 3,
                "subcategory_id": 89,
                "source": "misc",
            }
        ] * 7
        self.module._ac_plan[1] = {"items": items, "ts": time.time()}
        with (
            patch.object(self.module, "_kosell_cost_rub", return_value=5) as cost,
            patch.object(self.module, "_public_price_rub", return_value=15) as price,
            patch.object(
                self.module, "_fp_lowest_prices_for", return_value=[17, 20]
            ) as competitors,
        ):
            result = await api.preview_prices(api.PricePage(offset=0, limit=1))
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(result["total"], 7)
        self.assertEqual(result["items"][0]["cost_rub"], 5)
        self.assertEqual(result["items"][0]["public_rub"], 15)
        self.assertEqual(result["items"][0]["competitor_prices"], [17, 20])
        self.assertEqual(cost.call_count, 1)
        self.assertEqual(price.call_count, 1)
        competitors.assert_called_once_with(89, 3, "Portal", 5)
        self.supplier.rent.assert_not_called()

    async def test_prices_require_a_current_plan(self):
        with self.assertRaises(HTTPException):
            await api.preview_prices(api.PricePage())
        self.module._ac_plan[1] = {"items": [{"hours": 3}], "ts": time.time() - 1000}
        with self.assertRaises(HTTPException):
            await api.preview_prices(api.PricePage())

    async def test_map_refresh_invalidates_all_related_caches_and_plan(self):
        caches = [
            self.module._misc_cache,
            self.module._pub_lots_cache,
            self.module._commission_cache,
            self.module._node_form_cache,
            self.module._price_cache,
        ]
        for cache in caches:
            cache["old"] = "stale"
        self.module._ac_plan[1] = {"items": [1]}
        with patch.object(self.module, "_fetch_map", return_value={"games": [1, 2]}) as fetch:
            result = await api.refresh_map()
        fetch.assert_called_once_with(force=True)
        self.assertEqual(result["games"], 2)
        self.assertEqual(result["products"], 1)
        self.assertTrue(all(not cache for cache in caches))
        self.assertEqual(self.module._ac_plan, {})

    async def test_diagnostics_uses_original_report_without_html_markup(self):
        with patch.object(
            self.module,
            "_ac_diagnostics_text",
            return_value="<b>Диагностика</b>\nНет свободных лотов",
        ) as report:
            result = await api.diagnose_map()
        report.assert_called_once_with()
        self.assertEqual(result, {"text": "Диагностика\nНет свободных лотов"})

    async def test_clear_all_links_preserves_rentals_and_funpay_lots(self):
        await runtime.ensure_for_order(1)
        self.module.HIDDEN["100"] = "no_stock"
        result = await api.clear_mappings()
        self.assertEqual(result, {"removed": 1})
        self.assertEqual(self.module.MAPPINGS, [])
        self.assertEqual(self.module.HIDDEN, {})
        self.assertEqual(len(self.module.RENTALS["123"]), 1)
        async with self.Session() as db:
            self.assertIsNotNone(await db.get(Lot, 1))
            self.assertIsNone(await db.scalar(select(PluginBinding)))

    async def test_debug_toggle_is_persisted_and_type_checked(self):
        await api.save_config(api.Config(account_id=1, settings={"ac_debug": True}))
        self.assertTrue(self.module.SETTINGS["ac_debug"])
        state = await api.workspace()
        fields = [field for group in state["groups"] for field in group["fields"]]
        self.assertEqual(next(f for f in fields if f["key"] == "ac_debug")["type"], "boolean")
        with self.assertRaises(HTTPException):
            await api.save_config(api.Config(account_id=1, settings={"ac_debug": "yes"}))

    async def test_per_game_reprice_reuses_original_worker_argument(self):
        with (
            patch.object(self.module, "_reprice_thread_entry") as worker,
            patch.object(services.accounts, "sync_lots", AsyncMock()),
        ):
            await api.perform(self.host, "reprice", product_id=7)
        worker.assert_called_once_with(1, product_id=7)

    async def test_admin_notifications_use_supported_telegram_event_filters(self):
        with patch.object(services.telegram, "notify", AsyncMock()) as notify:
            await runtime.run_sync(self.host.admin_message, 0, "[KOSellRent] ✅ Заказ создан")
            self.assertEqual(notify.await_args.args[0], "orders")
            await runtime.run_sync(
                self.host.admin_message, 0, "[KOSellRent] ⚠️ Не удалось выдать аккаунт"
            )
            self.assertEqual(notify.await_args.args[0], "errors")
            await runtime.run_sync(
                self.host.admin_message, 0, "[KOSellRent] Создание лотов завершено"
            )
            self.assertEqual(notify.await_args.args[0], "lots")

    async def test_long_lot_batch_does_not_block_paid_order_delivery(self):
        started, release = threading.Event(), threading.Event()

        def batch(_user):
            started.set()
            release.wait(10)

        with (
            patch.object(self.module, "_autocreate_run", side_effect=batch),
            patch.object(services.accounts, "sync_lots", AsyncMock()),
        ):
            task = asyncio.create_task(api.perform(self.host, "create"))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                self.assertTrue(await asyncio.wait_for(runtime.ensure_for_order(1), 2))
                self.supplier.rent.assert_called_once()
                self.assertTrue(
                    await asyncio.wait_for(
                        runtime.handle_message(1, 1, {"text": "!друг", "sender_id": 20}), 2
                    )
                )
                self.assertTrue(self.module._friend_active("123"))
                self.assertFalse(task.done())
            finally:
                release.set()
                await task

    async def test_queued_settings_recheck_operation_before_mutating_configuration(self):
        gate = asyncio.Event()
        async with runtime.lock:
            pending = asyncio.create_task(
                api.save_config(api.Config(account_id=1, settings={"notify_sales": True}))
            )
            await asyncio.sleep(0)
            api.operation_task = asyncio.create_task(gate.wait())
        try:
            with self.assertRaises(HTTPException) as error:
                await pending
            self.assertEqual(error.exception.status_code, 409)
            self.assertFalse(self.module.SETTINGS["notify_sales"])
        finally:
            gate.set()
            await api.operation_task

    async def test_edit_mapping_preserves_source_metadata_and_rent_code(self):
        self.module.MAPPINGS[0].update(auto_created=True, placement="steam", rent_code="unchanged")
        response = await api.edit_mapping(
            100, api.MappingEdit(product_id=7, hours=24, currency="USD")
        )
        self.assertTrue(response["ok"])
        mapping = self.module.MAPPINGS[0]
        self.assertEqual((mapping["hours"], mapping["currency"]), (24, "USD"))
        self.assertEqual(mapping["rent_code"], "unchanged")
        self.assertTrue(mapping["auto_created"])
        self.assertEqual(mapping["placement"], "steam")

    async def test_bulk_unlink_is_scoped_and_preserves_rentals_and_funpay_lots(self):
        self.module.MAPPINGS.append({"lot_id": 200, "product_id": 7, "hours": 3})
        self.module.HIDDEN.update({"100": {}, "200": {}})
        self.module.RENTALS["123"] = [{"uid": "keep-rental"}]
        response = await api.delete_selected(api.MappingSelection(lot_ids=[100, 100, 999]))
        self.assertEqual(response["removed"], 1)
        self.assertEqual([m["lot_id"] for m in self.module.MAPPINGS], [200])
        self.assertEqual(set(self.module.HIDDEN), {"200"})
        self.assertEqual(self.module.RENTALS["123"][0]["uid"], "keep-rental")
        async with self.Session() as db:
            self.assertIsNotNone(await db.get(Lot, 1))
        again = await api.delete_selected(api.MappingSelection(lot_ids=[100]))
        self.assertEqual(again["removed"], 0)

    async def test_bulk_unlink_rolls_back_source_state_when_database_commit_fails(self):
        before = copy.deepcopy(self.module.MAPPINGS)
        with patch(
            "sqlalchemy.ext.asyncio.AsyncSession.commit",
            AsyncMock(side_effect=RuntimeError("database unavailable")),
        ):
            with self.assertRaises(RuntimeError):
                await api.delete_selected(api.MappingSelection(lot_ids=[100]))
        self.assertEqual(self.module.MAPPINGS, before)

    async def test_mapping_edit_rejects_unknown_mapping(self):
        with self.assertRaises(HTTPException) as error:
            await api.edit_mapping(999, api.MappingEdit(product_id=7, hours=24, currency="USD"))
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
