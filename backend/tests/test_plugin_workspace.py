"""Real ASGI/SQLite checks for isolated plugin settings and attributed logs."""

import gc
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import (
    events,
    fulfillment,
    integrated_api,
    integrated_config,
    main,
    message_templates,
    plugin_catalog,
    plugin_workspace,
    rental_api,
    rental_verification,
    services,
    template_api,
)
from app.config import config
from app.models import Account, Activity, AppRouteSupplier, Base, FulfillmentTask, Lot, Order
from app.security import vault
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="plugin-workspace-")
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix()
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = []
        for module in (
            events,
            message_templates,
            rental_api,
            rental_verification,
            template_api,
            fulfillment,
            integrated_api,
            integrated_config,
            main,
            plugin_catalog,
            plugin_workspace,
            services,
        ):
            item = patch.object(module, "Session", self.session)
            item.start()
            self.patches.append(item)

        async def get_db():
            async with self.session() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = get_db
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {config.api_token}"},
        )
        async with self.session() as db:
            account = Account(
                title="Seller",
                golden_key=vault.encrypt("test-cookie"),
                username="Seller",
                funpay_id=1,
                user_agent="test",
            )
            db.add(account)
            await db.flush()
            lot = Lot(
                account_id=account.id,
                funpay_lot_id="123",
                node_id=81,
                title="Product",
                category_title="Game",
                price=Decimal("10"),
            )
            order = Order(
                account_id=account.id,
                funpay_order_id="TEST1",
                buyer_id=42,
                buyer_name="Buyer",
                description="Gift",
                status="paid",
                sum=Decimal("10"),
                currency="RUB",
            )
            db.add_all([lot, order])
            await db.commit()
            self.account_id, self.lot_id, self.order_id = account.id, lot.id, order.id

    async def asyncTearDown(self):
        await self.http.aclose()
        main.app.dependency_overrides.pop(main.get_db, None)
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        gc.collect()
        self.temp.cleanup()

    async def test_log_source_pagination_filters_and_auth(self):
        async with self.session() as db:
            db.add_all(
                [
                    Activity(channel="orders", text=f"SMM order {index}", source_plugin="auto-smm")
                    for index in range(103)
                ]
            )
            db.add(
                Activity(
                    channel="orders",
                    text="literal %_ error",
                    level="error",
                    source_plugin="auto-smm",
                )
            )
            db.add(Activity(channel="orders", text="OTHER-PLUGIN-ONLY", source_plugin="lzt"))
            await db.commit()
        first = (await self.http.get("/api/plugins/auto-smm/logs")).json()
        self.assertEqual(len(first["items"]), 100)
        self.assertNotIn("OTHER-PLUGIN-ONLY", str(first))
        second = (
            await self.http.get(f"/api/plugins/auto-smm/logs?before={first['next_cursor']}")
        ).json()
        self.assertEqual(len(second["items"]), 4)
        self.assertFalse(
            {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
        )
        filtered = (
            await self.http.get("/api/plugins/auto-smm/logs", params={"level": "error", "q": "%_"})
        ).json()
        self.assertEqual(len(filtered["items"]), 1)
        self.assertEqual((await self.http.get("/api/plugins/missing/logs")).status_code, 404)
        self.assertEqual(
            (
                await self.http.get("/api/plugins/lzt/logs", headers={"Authorization": ""})
            ).status_code,
            401,
        )

    async def test_mutation_journal_never_records_credentials(self):
        response = await self.http.put(
            "/api/plugins/integrated/steam-points/settings", json={"api_key": "SENSITIVE-TEST-KEY"}
        )
        self.assertEqual(response.status_code, 200)
        logs = (await self.http.get("/api/plugins/steam-points/logs")).json()
        self.assertEqual(len(logs["items"]), 1)
        self.assertNotIn("SENSITIVE-TEST-KEY", str(logs))
        self.assertEqual((await self.http.get("/api/plugins/auto-smm/logs")).json()["items"], [])
        with patch.object(
            integrated_api,
            "points_request",
            AsyncMock(side_effect=httpx.ReadTimeout("do not expose key")),
        ):
            self.assertEqual(
                (await self.http.post("/api/plugins/integrated/steam-points/test")).status_code, 422
            )
        rows = (await self.http.get("/api/plugins/steam-points/logs?level=warning")).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("do not expose", str(rows))

    async def test_native_settings_do_not_overwrite_another_plugin(self):
        await services.save_setting(
            "plugins", {"responder": True, "first_message": "KEEP", "thanks_text": "Before"}
        )
        response = await self.http.put(
            "/api/plugins/reviews/settings",
            json={
                "responder": False,
                "first_message": "STALE",
                "thanks_text": "After",
                "review_replies": {"5": "After"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = await services.get_setting("plugins", {})
        self.assertTrue(saved["responder"])
        self.assertEqual(saved["first_message"], "KEEP")
        self.assertEqual(saved["thanks_text"], "After")

    async def test_discord_settings_preserve_steam_ciphertext_and_only_log_discord(self):
        encrypted = vault.encrypt("steam-key")
        await services.save_setting(
            fulfillment.SETTINGS_KEY,
            {
                "discord_boost": {"lot_phrase": "Boost"},
                "steam_gift": {"enabled": True, "lot_phrase": "Gift", "api_key": encrypted},
            },
        )
        response = await self.http.put(
            "/api/plugins/fulfillment/discord_boost/settings",
            json={"lot_phrase": "Discord", "boost_count": 4, "success_message": "Done"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = await services.get_setting(fulfillment.SETTINGS_KEY, {})
        self.assertEqual(saved["steam_gift"]["api_key"], encrypted)
        self.assertEqual(vault.decrypt(saved["steam_gift"]["api_key"]), "steam-key")
        self.assertTrue(saved["steam_gift"]["enabled"])
        self.assertEqual((await self.http.get("/api/plugins/steam-gift/logs")).json()["items"], [])
        self.assertEqual(
            len((await self.http.get("/api/plugins/discord-boost/logs")).json()["items"]), 1
        )

    async def test_supplier_edit_pause_and_mapping_edit(self):
        response = await self.http.post(
            "/api/app-route/suppliers",
            json={
                "name": "Before",
                "endpoint_url": "https://supplier.example/api",
                "token": "secret-key",
            },
        )
        ident = response.json()["id"]
        response = await self.http.put(
            f"/api/app-route/suppliers/{ident}",
            json={
                "name": "After",
                "endpoint_url": "https://supplier.example/api",
                "token": "",
                "is_active": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("token", response.json())
        async with self.session() as db:
            supplier = await db.get(AppRouteSupplier, ident)
            self.assertFalse(supplier.is_active)
            self.assertEqual(vault.decrypt(supplier.token), "secret-key")
        body = {
            "account_id": self.account_id,
            "funpay_item_id": "123",
            "supplier_id": ident,
            "supplier_item_id": "game1",
            "quantity": 1,
        }
        mapping = (await self.http.post("/api/app-route/mappings", json=body)).json()
        response = await self.http.put(
            f"/api/app-route/mappings/{mapping['id']}", json={**body, "quantity": 3}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["quantity"], 3)

    async def test_lzt_connection_can_be_saved_before_search_configuration(self):
        with (
            patch.object(main.lzt_market.worker, "start", AsyncMock()),
            patch.object(main.lzt_market.worker, "stop", AsyncMock()),
        ):
            response = await self.http.put(
                "/api/plugins/lzt/connection", json={"token": "fake-lzt-key"}
            )
        self.assertEqual(response.status_code, 200, response.text)
        saved = await services.get_setting(main.lzt_market.SETTINGS_KEY, {})
        self.assertEqual(vault.decrypt(saved["token"]), "fake-lzt-key")
        self.assertFalse(saved.get("enabled"))
        self.assertNotIn("fake-lzt-key", str((await self.http.get("/api/plugins/lzt/logs")).json()))

    async def test_robux_cookie_can_be_saved_before_pass_ids(self):
        response = await self.http.put(
            "/api/auto-robux/connection", json={"cookie": "_|WARNING:-DO-NOT-SHARE-TEST"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["has_cookie"])
        self.assertNotIn("WARNING", response.text)

    async def test_uncertain_fulfillment_resolution_never_sends_again(self):
        async with self.session() as db:
            task = FulfillmentTask(
                order_id=self.order_id,
                kind="steam_gift",
                status="uncertain",
                target="76561198000000000",
            )
            db.add(task)
            await db.commit()
            ident = task.id
        with patch.object(services.accounts, "send", AsyncMock()) as send:
            response = await self.http.post(f"/api/plugins/fulfillment/{ident}/resolve")
            self.assertEqual(response.status_code, 200, response.text)
            send.assert_not_awaited()
        self.assertEqual(
            len((await self.http.get("/api/plugins/steam-gift/logs")).json()["items"]), 1
        )
        self.assertEqual(
            (await self.http.get("/api/plugins/discord-boost/logs")).json()["items"], []
        )

    async def test_log_event_is_published_once_when_channel_is_logs(self):
        bus = events.EventBus()
        with patch.object(bus, "publish", AsyncMock()) as publish:
            await bus.log("logs", "Saved", source_plugin="auto-smm")
            publish.assert_awaited_once()
            self.assertEqual(publish.call_args.args[1]["source_plugin"], "auto-smm")
