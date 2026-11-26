"""Unified error contract: {code, message, details?, correlation_id} + legacy `detail`.

Real app instance, isolated temporary storage, no FunPay network.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

temporary = tempfile.TemporaryDirectory(prefix="funpay-plus-contract-")
os.environ["STORAGE_DIR"] = temporary.name
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + (Path(temporary.name) / "test.db").as_posix()
os.environ["WORKERS_ENABLED"] = "false"
os.environ["API_TOKEN"] = "sample-test-access-token-1234567890"
os.environ["SECRET_KEY"] = "sample-test-encryption-key-1234567890"

import httpx  # noqa: E402
from app.db import engine  # noqa: E402
from app.error_contract import ApiError  # noqa: E402
from app.funpay import ChallengeException, ResponseFormatError, UnauthorizedException  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


class ErrorContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        )

    async def asyncTearDown(self):
        await self.http.aclose()
        await engine.dispose()

    async def contract(self, response: httpx.Response) -> dict:
        payload = response.json()
        self.assertEqual(response.headers.get("x-correlation-id"), payload["correlation_id"])
        self.assertTrue(payload["correlation_id"])
        self.assertIn("detail", payload)  # legacy alias for old frontend calls
        self.assertNotIn("golden", response.text.lower())
        return payload

    async def test_http_error_carries_code_and_correlation(self):
        response = await self.http.get("/api/chats/999999/messages")
        self.assertEqual(response.status_code, 404)
        payload = await self.contract(response)
        self.assertEqual(payload["code"], "not_found")
        self.assertEqual(payload["message"], "Запись не найдена")
        self.assertEqual(payload["detail"], payload["message"])

    async def test_unauthorized_uses_rate_limited_code(self):
        response = await self.http.get("/api/health", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status_code, 401)
        payload = await self.contract(response)
        self.assertEqual(payload["code"], "unauthorized")

    async def test_validation_error_keeps_legacy_detail_list(self):
        response = await self.http.post("/api/chats/1/messages", json={"text": ""})
        self.assertEqual(response.status_code, 422)
        payload = await self.contract(response)
        self.assertEqual(payload["code"], "validation_failed")
        self.assertIsInstance(payload["details"], list)
        self.assertIsInstance(payload["detail"], list)

    async def test_funpay_errors_map_to_safe_codes(self):
        cases = {
            UnauthorizedException("FunPayAPI: сессия не авторизована"): "funpay_auth_expired",
            ChallengeException("FunPay требует проверку в браузере"): "funpay_challenge_required",
            ResponseFormatError("FunPay вернул HTML вместо JSON"): "remote_unconfirmed",
        }
        for error, code in cases.items():
            with self.subTest(code=code):
                with patch("app.main.get_setting", new=AsyncMock(side_effect=error)):
                    response = await self.http.get("/api/dashboard")
                    self.assertEqual(response.status_code, 502)
                    payload = await self.contract(response)
                    self.assertEqual(payload["code"], code)

    async def test_typed_api_error_passes_through(self):
        code, message = "cooldown_active", "Ключ источника на cooldown"
        async def raising(*args, **kwargs):
            raise ApiError(409, code, message)
        with patch("app.main.get_setting", new=AsyncMock(side_effect=raising)):
            response = await self.http.get("/api/dashboard")
            self.assertEqual(response.status_code, 409)
            payload = await self.contract(response)
            self.assertEqual((payload["code"], payload["message"]), (code, message))

    async def test_unhandled_error_is_safe_internal(self):
        async def raising(*args, **kwargs):
            raise RuntimeError("secret internal detail must not leak")
        # ServerErrorMiddleware re-raises after responding; the ASGI transport
        # must not surface that re-raise to the test client.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        ) as client:
            with patch("app.main.get_setting", new=AsyncMock(side_effect=raising)):
                response = await client.get("/api/dashboard")
            self.assertEqual(response.status_code, 500)
            payload = await self.contract(response)
            self.assertEqual(payload["code"], "internal")
            self.assertEqual(payload["message"], "Внутренняя ошибка. Повторите позже.")
            self.assertNotIn("secret internal detail", response.text)

    async def test_success_responses_carry_correlation_header(self):
        response = await self.http.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers.get("x-correlation-id"))

    async def test_diagnostics_exposes_responder_stop_state(self):
        response = await self.http.get("/api/automation/status")
        self.assertEqual(response.status_code, 200)
        responder = response.json()["responder"]
        self.assertFalse(responder["enabled"])
        self.assertIn("commands", responder)
        self.assertIn("keywords", responder)

    async def test_correlation_id_is_unique_per_request(self):
        first = (await self.http.get("/api/health", headers={"Authorization": "Bearer wrong"}))
        second = (await self.http.get("/api/health", headers={"Authorization": "Bearer wrong"}))
        self.assertNotEqual(
            first.headers.get("x-correlation-id"), second.headers.get("x-correlation-id")
        )

    async def test_legacy_frontend_shape_still_parses(self):
        # Old frontend builds messages from `detail`; contract keeps it working.
        response = await self.http.post("/api/chats/1/messages", json={"text": ""})
        legacy = response.json()["detail"]
        text = (
            " ".join(f"{item['loc'][1:]}: {item['msg']}" for item in legacy)
            if isinstance(legacy, list)
            else legacy
        )
        self.assertTrue(text)
