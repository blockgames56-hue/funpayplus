"""Real ASGI auth/CORS handshake used by the desktop VPS connection screen."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from app import email_codes, integrated_jobs, lzt_market, main
from app.automation_plugins import responder
from app.config import config
from sqlalchemy.ext.asyncio import create_async_engine


class VPSConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.engine_patch = patch.object(main, "engine", self.engine)
        self.engine_patch.start()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1234)),
            base_url="https://panel.example.com",
        )
        self.headers = {
            "Authorization": f"Bearer {config.api_token}",
            "Origin": "http://tauri.localhost",
        }

    async def asyncTearDown(self):
        await self.http.aclose()
        self.engine_patch.stop()
        await self.engine.dispose()

    async def test_desktop_preflight_health_and_session(self):
        response = await self.http.options(
            "/api/health",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://tauri.localhost")
        for origin in ("http://tauri.localhost", "tauri://localhost"):
            headers = {**self.headers, "Origin": origin}
            response = await self.http.get("/api/health", headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
            response = await self.http.get("/api/auth/session", headers=headers)
            self.assertEqual(response.status_code, 200)

    async def test_wrong_key_and_untrusted_origin_are_rejected(self):
        response = await self.http.get(
            "/api/health", headers={**self.headers, "Authorization": "Bearer wrong-key"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://tauri.localhost")
        response = await self.http.get(
            "/api/health", headers={**self.headers, "Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status_code, 403)

    async def test_pause_is_local_and_does_not_hide_offers(self):
        with (
            patch.object(config, "workers_enabled", True),
            patch.object(main.workers, "stop", AsyncMock()) as stop,
            patch.object(email_codes, "stop_mailbox", AsyncMock()),
            patch.object(lzt_market.worker, "stop", AsyncMock()),
            patch.object(integrated_jobs, "stop", AsyncMock()),
            patch.object(responder, "stop", AsyncMock()),
            patch.object(main.accounts, "client", AsyncMock()) as funpay,
        ):
            response = await self.http.post("/api/offline/pause-local", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(config.workers_enabled)
            stop.assert_awaited_once()
            funpay.assert_not_called()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("203.0.113.1", 1234)),
            base_url="https://panel.example.com",
        ) as remote:
            response = await remote.post("/api/offline/pause-local", headers=self.headers)
        self.assertEqual(response.status_code, 403)

    async def test_disabled_workers_do_not_start_mail_or_market(self):
        with (
            patch.object(config, "workers_enabled", False),
            patch.object(main.workers, "start", AsyncMock()) as workers_start,
            patch.object(main.workers, "stop", AsyncMock()),
            patch.object(email_codes, "start_saved_mailbox", AsyncMock()) as mail_start,
            patch.object(email_codes, "stop_mailbox", AsyncMock()),
            patch.object(lzt_market.worker, "start", AsyncMock()) as market_start,
            patch.object(lzt_market.worker, "stop", AsyncMock()),
        ):
            async with main.lifespan(main.app):
                workers_start.assert_not_awaited()
                mail_start.assert_not_awaited()
                market_start.assert_not_awaited()

    async def test_connect_without_key_sets_cookie_and_scoped_session(self):
        response = await self.http.post(
            "/api/auth/connect", headers={"Origin": "https://panel.example.com"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["session_token"]
        self.assertNotEqual(token, config.api_token)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual((await self.http.get("/api/auth/session")).status_code, 200)
        self.http.cookies.clear()
        result = await self.http.get(
            "/api/health",
            headers={"Origin": "http://tauri.localhost", "Authorization": f"Bearer {token}"},
        )
        self.assertEqual(result.status_code, 200, result.text)

    async def test_passwordless_connect_rejects_untrusted_web_origin(self):
        response = await self.http.post(
            "/api/auth/connect", headers={"Origin": "https://untrusted.example"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("set-cookie", response.headers)

    async def test_passwordless_session_can_request_websocket_ticket(self):
        response = await self.http.post(
            "/api/auth/connect", headers={"Origin": "tauri://localhost"}
        )
        token = response.json()["session_token"]
        self.http.cookies.clear()
        response = await self.http.post(
            "/api/ws-ticket",
            headers={"Origin": "tauri://localhost", "Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ticket"])

    async def test_signed_session_is_not_overridden_by_stale_cookie(self):
        from app.security import authorized, session_cookie

        self.assertTrue(authorized(session_cookie(), "1.invalid-cookie"))
        self.assertFalse(authorized("1.invalid", None))
        self.assertFalse(authorized("99999999999.invalid", None))

    async def test_malformed_sessions_are_rejected_without_server_errors(self):
        from app.security import authorized

        for token in ("².invalid", "9" * 5000 + ".invalid", ".invalid", "invalid."):
            self.assertFalse(authorized(token, None))
