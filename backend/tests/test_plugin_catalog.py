"""Catalog switches persist state and control the real plugin lifecycle."""

import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import app_route, auto_dump, plugin_catalog
from app.models import Base
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="plugin-catalog-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix())
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.saved = {}
        async def get_setting(key, default=None):
            return copy.deepcopy(self.saved.get(key, default))
        async def save_setting(key, value):
            self.saved[key] = copy.deepcopy(value)
        async def get_settings(defaults):
            return {key: copy.deepcopy(self.saved.get(key, value)) for key, value in defaults.items()}
        self.get_setting = get_setting
        self.patches = [
            patch.object(plugin_catalog, "Session", async_sessionmaker(self.engine)),
            patch.object(plugin_catalog, "get_setting", get_setting),
            patch.object(plugin_catalog, "get_settings", get_settings),
            patch.object(plugin_catalog, "save_setting", save_setting),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.temp.cleanup()

    async def test_email_switch_stops_and_restarts_saved_mailbox(self):
        self.saved["imap_mailbox_default"] = {"host": "mail.example", "password": "secret"}
        with (patch.object(plugin_catalog.email_codes, "stop_mailbox", AsyncMock()) as stop,
              patch.object(plugin_catalog.email_codes, "start_saved_mailbox", AsyncMock()) as start):
            self.assertFalse((await plugin_catalog.toggle("email-code", False))["enabled"])
            start.assert_not_awaited()
            self.assertTrue((await plugin_catalog.toggle("email-code", True))["enabled"])
            self.assertEqual(stop.await_count, 2)
            start.assert_awaited_once()
        self.assertNotIn("secret", str(await plugin_catalog.catalog()))

    async def test_catalog_only_exposes_requested_plugins(self):
        self.assertEqual(
            {row["id"] for row in await plugin_catalog.catalog()},
            plugin_catalog.CATALOG_IDS,
        )
        # Каждый плагин каталога можно выключить и включить обратно.
        for ident in {"kosell", "lzt", "auto-dump"}:
            row = next(
                item for item in await plugin_catalog.catalog() if item["id"] == ident
            )
            if row["available"] and row["configured"]:
                self.assertFalse((await plugin_catalog.toggle(ident, False))["enabled"])

    async def test_unconfigured_plugin_cannot_start(self):
        with self.assertRaises(ValueError):
            await plugin_catalog.toggle("lzt", True)
        with self.assertRaises(ValueError):
            await plugin_catalog.toggle("auto-robux", True)

    async def test_lzt_switch_controls_worker_and_keeps_token(self):
        self.saved[plugin_catalog.lzt_market.SETTINGS_KEY] = {"token": "encrypted", "filter_url": "https://lzt.market/steam/", "enabled": True}
        with (patch.object(plugin_catalog.lzt_market.worker, "stop", AsyncMock()) as stop,
              patch.object(plugin_catalog.lzt_market.worker, "start", AsyncMock()) as start):
            await plugin_catalog.toggle("lzt", False)
            start.assert_not_awaited()
            await plugin_catalog.toggle("lzt", True)
            start.assert_awaited_once()
            self.assertEqual(stop.await_count, 2)
        self.assertEqual(self.saved[plugin_catalog.lzt_market.SETTINGS_KEY]["token"], "encrypted")

    async def test_offline_guard_plugin_requires_mafile_accounts(self):
        # Без добавленных аккаунтов плагин нельзя включить.
        with self.assertRaises(ValueError):
            await plugin_catalog.toggle("offline", True)
        # Добавим аккаунт с maFile и убедимся, что плагин готов к работе.
        from app import plugin_workspace

        plugin_workspace.Session = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        add_guard_account, guard_accounts = (
            plugin_workspace.add_guard_account,
            plugin_workspace.guard_accounts,
        )

        mafile = {
            "shared_secret": base64.b64encode(b"1234567890123456").decode(),
            "identity_secret": base64.b64encode(b"6543210987654321").decode(),
            "device_id": "android:00000000-1111-2222-3333-444444444444",
            "Session": {"SteamID": "76561198000000001"},
        }
        await add_guard_account(
            {
                "label": "CS2 main",
                "login": "user",
                "password": "pw",
                "mafile": json.dumps(mafile),
            }
        )
        listing = await guard_accounts()
        self.assertEqual(listing[0]["label"], "CS2 main")
        self.assertEqual(listing[0]["steamid"], "76561198000000001")
        row = next(item for item in await plugin_catalog.catalog() if item["id"] == "offline")
        self.assertTrue(row["configured"])
        self.assertTrue((await plugin_catalog.toggle("offline", True))["enabled"])
        self.assertFalse((await plugin_catalog.toggle("offline", False))["enabled"])

    async def test_offline_guard_code_and_credentials(self):
        from app import plugin_workspace
        from app.steam_security import guard_code

        plugin_workspace.Session = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        add, accounts, code, credentials, delete_account = (
            plugin_workspace.add_guard_account,
            plugin_workspace.guard_accounts,
            plugin_workspace.guard_account_code,
            plugin_workspace.guard_account_credentials,
            plugin_workspace.delete_guard_account,
        )
        shared = base64.b64encode(b"1234567890123456").decode()
        mafile = {
            "shared_secret": shared,
            "identity_secret": base64.b64encode(b"6543210987654321").decode(),
            "device_id": "android:test",
            "Session": {"SteamID": "76561198000000001"},
        }
        row = await add(
            {"label": "alt", "login": "user", "password": "pw", "mafile": json.dumps(mafile)}
        )
        ident = row["id"]
        # Дубликат отклоняется.
        with self.assertRaises(plugin_workspace.HTTPException) as duplicate:
            await add(
                {"label": "alt2", "login": "user", "password": "", "mafile": json.dumps(mafile)}
            )
        self.assertEqual(duplicate.exception.status_code, 409)
        # Код совпадает с локальным расчётом и живёт меньше 30 секунд.
        result = await code(ident)
        self.assertEqual(result["code"], guard_code(shared))
        self.assertTrue(0 < result["seconds"] <= 30)
        # Данные расшифровываются только для владельца.
        data = await credentials(ident)
        self.assertEqual(data["login"], "user")
        self.assertEqual(data["password"], "pw")
        # Некорректный maFile отклоняется.
        with self.assertRaises(plugin_workspace.HTTPException) as invalid:
            await add(
                {"label": "x", "login": "u", "password": "", "mafile": "not-json"}
            )
        self.assertEqual(invalid.exception.status_code, 422)
        self.assertTrue((await delete_account(ident))["ok"])
        self.assertEqual(await accounts(), [])

    async def test_disabled_modules_do_not_dispatch_work(self):
        self.saved["plugin_controls"] = {"app-route": False, "auto-dump": False}
        with (patch.object(app_route, "get_setting", self.get_setting),
              patch.object(app_route, "Session") as route_db,
              patch.object(auto_dump, "get_setting", self.get_setting),
              patch.object(auto_dump, "Session") as dump_db):
            self.assertFalse(await app_route.deliver(1))
            await auto_dump.scan_all()
            route_db.assert_not_called()
            dump_db.assert_not_called()
