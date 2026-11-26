"""Telegram control uses isolated storage and mocked Bot API/FunPay transports."""

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlencode

temporary = tempfile.TemporaryDirectory(prefix="fp-telegram-tests-", ignore_cleanup_errors=True)
os.environ.update(
    STORAGE_DIR=temporary.name,
    DATABASE_URL="sqlite+aiosqlite:///" + (Path(temporary.name) / "test.db").as_posix(),
    WORKERS_ENABLED="false",
    API_TOKEN="telegram-test-access-token-1234567890",
    SECRET_KEY="telegram-test-encryption-key-1234567890",
)

import httpx  # noqa: E402
from app import telegram_control as tc  # noqa: E402
from app.db import Session, engine  # noqa: E402
from app.funpay import FunPayError  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Base, Chat, Lot  # noqa: E402
from app.schemas import TelegramSettings  # noqa: E402
from app.security import vault  # noqa: E402
from app.services import (  # noqa: E402
    TelegramService,
    accounts,
    get_setting,
    save_setting,
    telegram,
)

TOKEN = "123456:sample_bot_token"


def signed(user=42, **extra):
    data = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user, "first_name": "Owner"}),
        **extra,
    }
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with engine.begin() as db:
            await db.run_sync(Base.metadata.drop_all)
            await db.run_sync(Base.metadata.create_all)
        tc.devices.clear()
        self.bot = tc.TelegramControl()
        self.swap = patch.object(tc, "control", self.bot)
        self.swap.start()

        async def bot_api(token, method, payload):
            if method == "getMe":
                return {"id": 123456, "username": "SampleFunPayBot"}
            if method == "getUpdates":
                return []
            if method == "sendMessage":
                return {"message_id": 100}
            return True

        self.remote = AsyncMock(side_effect=bot_api)
        self.remote_patch = patch.object(telegram, "request", self.remote)
        self.remote_patch.start()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]},
        )
        await save_setting(
            "telegram",
            {
                **TelegramSettings().model_dump(),
                "token": vault.encrypt(TOKEN),
                "control_enabled": True,
            },
        )
        self.owner = {
            "id": 42,
            "name": "Owner",
            "bot": tc.fingerprint(TOKEN),
            "generation": "generation-1",
        }
        await save_setting("telegram_owner", self.owner)
        await save_setting(
            "telegram_bot", {"bot": tc.fingerprint(TOKEN), "username": "SampleFunPayBot"}
        )
        async with Session() as db:
            db.add(
                Account(
                    id=1,
                    title="Store",
                    username="Store",
                    golden_key=vault.encrypt("fake"),
                    funpay_id=100,
                    user_agent="Isolated test",
                    is_active=False,
                )
            )
            await db.flush()
            db.add(
                Lot(
                    id=1,
                    account_id=1,
                    funpay_lot_id="123",
                    node_id=1,
                    category_title="Game",
                    title="Test lot",
                    price=100,
                )
            )
            db.add(
                Chat(
                    id=1,
                    account_id=1,
                    funpay_chat_id="456",
                    interlocutor_id=200,
                    interlocutor_name="Buyer",
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.bot.stop()
        await self.http.aclose()
        self.swap.stop()
        self.remote_patch.stop()
        await engine.dispose()

    async def settings(self, **changes):
        cfg = await get_setting("telegram")
        cfg.update(changes)
        await save_setting("telegram", cfg)
        return cfg

    def update(self, text="/menu", actor=42, kind="private", update_id=1):
        return {
            "update_id": update_id,
            "message": {
                "from": {"id": actor, "first_name": "Owner"},
                "chat": {"id": actor, "type": kind},
                "text": text,
            },
        }

    async def test_init_data_valid_and_rejects_tampering(self):
        self.assertEqual(tc.validate_init_data(signed(), TOKEN), 42)
        for value in [
            signed().replace("Owner", "Other"),
            signed(auth_date="1"),
            signed(auth_date=str(int(time.time()) + 100)),
            signed(user=True),
            signed() + "&auth_date=1",
            "broken",
            "x" * 17000,
        ]:
            with self.assertRaises(tc.HTTPException):
                tc.validate_init_data(value, TOKEN)

    async def test_mini_session_uses_shared_api_and_hides_token(self):
        result = await self.http.post("/api/telegram/session", json={"init_data": signed()})
        self.assertEqual(result.status_code, 200, result.text)
        token = result.json()["session_token"]
        self.assertTrue(token.startswith("tg1."))
        self.assertNotIn(TOKEN, result.text)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": "Bearer " + token},
        ) as mini:
            response = await mini.get("/api/accounts")
            self.assertEqual(response.json()[0]["username"], "Store")
            response = await mini.patch("/api/settings/telegram", json={"enabled": True})
            self.assertEqual(response.status_code, 200)
        self.assertTrue((await self.http.get("/api/settings/telegram")).json()["enabled"])

    async def test_foreign_user_bad_origin_and_unsigned_mini_rejected(self):
        for data in [signed(user=99), "user=%7B%22id%22%3A42%7D"]:
            result = await self.http.post("/api/telegram/session", json={"init_data": data})
            self.assertIn(result.status_code, [401, 403])
        result = await self.http.post(
            "/api/telegram/session",
            json={"init_data": signed()},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(result.status_code, 403)

    async def test_session_revoked_by_disable_owner_change_and_token_rotation(self):
        token = tc.sign_session(self.owner)
        self.assertTrue(await tc.authorized_session(token))
        await self.settings(control_enabled=False)
        self.assertFalse(await tc.authorized_session(token))
        await self.settings(control_enabled=True, token=vault.encrypt("999:other"))
        self.assertFalse(await tc.authorized_session(token))
        await self.settings(token=vault.encrypt(TOKEN))
        await save_setting("telegram_owner", {**self.owner, "generation": "changed"})
        self.assertFalse(await tc.authorized_session(token))
        self.assertFalse(await tc.authorized_session(token + "invalid"))

    async def test_pairing_one_use_expiry_and_automatic_recipient(self):
        result = await self.http.post("/api/telegram/pair")
        self.assertEqual(result.status_code, 200, result.text)
        nonce = result.json()["url"].split("pair_")[1]
        cfg = await get_setting("telegram")
        await self.bot.dispatch(self.update("/start pair_" + nonce, actor=77), cfg, TOKEN)
        owner = await get_setting("telegram_owner")
        self.assertEqual(owner["id"], 77)
        self.assertEqual((await get_setting("telegram"))["chat_id"], "77")
        await self.bot.dispatch(self.update("/start pair_" + nonce, actor=88), cfg, TOKEN)
        self.assertEqual((await get_setting("telegram_owner"))["id"], 77)
        self.assertNotIn(nonce, json.dumps(await get_setting("telegram_pair")))

    async def test_expired_or_foreign_bot_pair_cannot_bind(self):
        nonce = "expired"
        await save_setting(
            "telegram_pair",
            {
                "hash": hashlib.sha256(nonce.encode()).hexdigest(),
                "expires": time.time() - 1,
                "bot": tc.fingerprint(TOKEN),
            },
        )
        await self.bot.dispatch(
            self.update("/start pair_" + nonce, actor=77), await get_setting("telegram"), TOKEN
        )
        self.assertEqual((await get_setting("telegram_owner"))["id"], 42)

    async def test_foreign_users_and_group_cannot_manage(self):
        for update in [self.update(actor=99), self.update(kind="group")]:
            await self.bot.dispatch(update, await get_setting("telegram"), TOKEN)
        self.remote.assert_not_awaited()

    async def test_all_read_menus_render_and_account_button_uses_real_state(self):
        cfg = await get_setting("telegram")
        for name in [
            "menu",
            "status",
            "accounts:0",
            "account:1",
            "lots:0",
            "lot:1",
            "chats:0",
            "chat:1",
            "plugins:0",
            "plugin:steam-rent",
            "settings",
            "logs",
        ]:
            await self.bot.command(TOKEN, 42, name, cfg, self.owner)
        texts = [
            c.args[2].get("text", "")
            for c in self.remote.await_args_list
            if c.args[1] == "sendMessage"
        ]
        self.assertTrue(any("Test lot" in t for t in texts))
        self.assertTrue(any("Store" in t for t in texts))

    async def test_account_action_confirmed_once_and_not_on_first_click(self):
        cfg = await get_setting("telegram")
        with patch.object(tc, "internal_api", AsyncMock(return_value={"ok": True})) as api:
            await self.bot.command(TOKEN, 42, "account-state:1:1", cfg, self.owner)
            api.assert_not_awaited()
            nonce = next(iter(self.bot.actions))
            await self.bot.command(TOKEN, 42, "apply:" + nonce, cfg, self.owner)
            await self.bot.command(TOKEN, 42, "apply:" + nonce, cfg, self.owner)
            api.assert_awaited_once_with("/accounts/1", "PATCH", {"is_active": True})

    async def test_confirmation_expired_or_owner_replaced_does_not_apply(self):
        cfg = await get_setting("telegram")
        await self.bot.confirm(
            TOKEN, 42, self.owner, "Test", "/lots/bulk", "POST", {"ids": [1], "action": "delete"}
        )
        nonce = next(iter(self.bot.actions))
        self.bot.actions[nonce]["expires"] = 0
        with patch.object(tc, "internal_api", AsyncMock()) as api:
            await self.bot.command(TOKEN, 42, "apply:" + nonce, cfg, self.owner)
            api.assert_not_awaited()

    async def test_poll_offset_survives_new_worker_and_deduplicates_updates(self):
        self.remote.side_effect = None
        self.remote.return_value = [self.update(update_id=7), self.update(update_id=7)]
        with patch.object(self.bot, "dispatch", AsyncMock()) as dispatch:
            await self.bot.poll_once()
            dispatch.assert_awaited_once()
        self.assertEqual(await get_setting("telegram_offset:" + tc.fingerprint(TOKEN)), 8)
        fresh = tc.TelegramControl()
        with patch.object(fresh, "dispatch", AsyncMock()) as dispatch:
            await fresh.poll_once()
            dispatch.assert_not_awaited()

    async def test_disabled_bot_does_not_poll(self):
        await self.settings(control_enabled=False, enabled=False)
        self.assertFalse(await self.bot.poll_once())
        self.remote.assert_not_awaited()

    async def test_worker_starts_once_and_cancels_idle_poll(self):
        await self.settings(control_enabled=False, enabled=False)
        await self.bot.start()
        first = self.bot.task
        await self.bot.start()
        self.assertIs(first, self.bot.task)
        await asyncio.sleep(0)
        await self.bot.stop()
        self.assertTrue(first.cancelled())
        self.assertIsNone(self.bot.task)

    async def test_reply_prompt_sends_to_shared_chat_with_idempotency(self):
        cfg = await get_setting("telegram")
        await self.bot.command(TOKEN, 42, "reply:1", cfg, self.owner)
        update = self.update("Hello buyer", update_id=50)
        update["message"]["reply_to_message"] = {"message_id": 100}
        with patch.object(accounts, "send", AsyncMock(return_value={"sent": True})) as send:
            await self.bot.dispatch(update, cfg, TOKEN)
            send.assert_awaited_once_with(
                1, "Hello buyer", idempotency_key="tg-" + tc.fingerprint(TOKEN) + "-50"
            )

    async def test_partial_settings_preserve_recipient_events_and_secret(self):
        await self.settings(chat_id="42", events=["chats"])
        result = await self.http.patch("/api/settings/telegram", json={"enabled": True})
        self.assertEqual(result.status_code, 200, result.text)
        saved = await get_setting("telegram")
        self.assertEqual(saved["chat_id"], "42")
        self.assertEqual(saved["events"], ["chats"])
        self.assertEqual(vault.decrypt(saved["token"]), TOKEN)
        self.assertEqual(result.json()["token"], "")
        self.assertNotIn(TOKEN, result.text)

    async def test_https_url_normalizes_and_invalid_urls_rejected(self):
        result = await self.http.patch(
            "/api/settings/telegram", json={"mini_app_url": "https://panel.example.com"}
        )
        self.assertEqual(result.json()["mini_app_url"], "https://panel.example.com/telegram")
        for url in [
            "http://panel.example.com",
            "https://a:b@example.com",
            "https://example.com/?token=x",
            "https://example.com/other",
        ]:
            result = await self.http.patch("/api/settings/telegram", json={"mini_app_url": url})
            self.assertEqual(result.status_code, 422)

    async def test_public_bootstrap_locked_and_device_approval_has_no_manual_key(self):
        await self.settings(mini_app_url="https://panel.example.com/telegram")
        transport = httpx.ASGITransport(app=app, client=("203.0.113.8", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as visitor:
            self.assertEqual((await visitor.post("/api/auth/connect")).status_code, 403)
            result = await visitor.post("/api/telegram/device")
            self.assertEqual(result.status_code, 200, result.text)
            device = result.json()
            body = {k: device[k] for k in ["device", "secret"]}
            self.assertTrue(
                (await visitor.post("/api/telegram/device/poll", json=body)).json()["pending"]
            )
            self.assertEqual(
                (
                    await visitor.post(
                        "/api/telegram/device/poll", json={**body, "secret": "wrong"}
                    )
                ).status_code,
                401,
            )
            await self.bot.command(
                TOKEN, 42, "device:" + device["device"], await get_setting("telegram"), self.owner
            )
            result = await visitor.post("/api/telegram/device/poll", json=body)
            token = result.json()["session_token"]
            self.assertTrue(await tc.authorized_session(token))
            self.assertEqual(
                (await visitor.post("/api/telegram/device/poll", json=body)).status_code, 401
            )
            self.assertEqual(
                (
                    await visitor.get("/api/accounts", headers={"Authorization": "Bearer " + token})
                ).status_code,
                200,
            )

    async def test_device_limits_and_revocation(self):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 123))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as visitor:
            first = (await visitor.post("/api/telegram/device")).json()
            for _ in range(4):
                self.assertEqual((await visitor.post("/api/telegram/device")).status_code, 200)
            self.assertEqual((await visitor.post("/api/telegram/device")).status_code, 429)
            await self.http.post("/api/telegram/disconnect")
            self.assertEqual(
                (
                    await visitor.post(
                        "/api/telegram/device/poll",
                        json={k: first[k] for k in ["device", "secret"]},
                    )
                ).status_code,
                401,
            )

    async def test_long_poll_does_not_block_outgoing_messages(self):
        service = TelegramService()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def post(url, **kwargs):
            if url.endswith("/getUpdates"):
                entered.set()
                await release.wait()
            return Mock(json=lambda: {"ok": True, "result": []})

        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = post
        with patch("app.services.httpx.AsyncClient", return_value=client):
            poll = asyncio.create_task(service.request(TOKEN, "getUpdates", {"timeout": 25}))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(
                service.request(TOKEN, "sendMessage", {"chat_id": 42, "text": "Hello"}), 1
            )
            release.set()
            await poll

    async def test_network_error_does_not_leak_bot_token(self):
        service = TelegramService()
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = httpx.ConnectError(
            "https://api.telegram.org/bot" + TOKEN + "/getUpdates"
        )
        with patch("app.services.httpx.AsyncClient", return_value=client):
            with self.assertRaises(FunPayError) as error:
                await service.request(TOKEN, "getUpdates", {})
        self.assertNotIn(TOKEN, str(error.exception))

    async def test_notification_reply_mapping_is_scoped_to_its_bot(self):
        from app.models import TelegramLink
        from app.services import telegram_link_scope

        await self.settings(enabled=True, chat_id="42", events=["chats"])
        await telegram.notify("chats", "Buyer message", chat_id=1)
        async with Session() as db:
            from sqlalchemy import select

            link = await db.scalar(select(TelegramLink))
            self.assertEqual(link.telegram_chat_id, telegram_link_scope(TOKEN, 42))
        update = self.update("Reply", update_id=70)
        update["message"]["reply_to_message"] = {"message_id": 100}
        with patch.object(accounts, "send", AsyncMock(return_value={})) as send:
            await self.bot.dispatch(update, await get_setting("telegram"), TOKEN)
            send.assert_awaited_once()

    async def test_replacing_token_cannot_reply_to_previous_bots_buyer(self):
        from app.models import TelegramLink

        async with Session() as db:
            db.add(TelegramLink(telegram_chat_id="42", telegram_message_id=101, chat_id=1))
            await db.commit()
        await self.settings(chat_id="42")
        response = await self.http.patch(
            "/api/settings/telegram", json={"token": "777:new_bot", "control_enabled": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        update = self.update("Reply", update_id=71)
        update["message"]["reply_to_message"] = {"message_id": 101}
        with patch.object(accounts, "send", AsyncMock()) as send:
            await self.bot.dispatch(update, await get_setting("telegram"), "777:new_bot")
            send.assert_not_awaited()

    async def test_disconnecting_does_not_reopen_public_keyless_access(self):
        response = await self.http.patch(
            "/api/settings/telegram", json={"mini_app_url": "https://panel.example.com"}
        )
        self.assertEqual(response.status_code, 200)
        await self.http.post("/api/telegram/disconnect")
        await self.http.patch("/api/settings/telegram", json={"mini_app_url": ""})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 123)),
            base_url="http://testserver",
        ) as visitor:
            self.assertEqual((await visitor.post("/api/auth/connect")).status_code, 403)

    async def test_telegram_session_cannot_be_upgraded_to_unrevokable_session(self):
        await self.settings(mini_app_url="https://panel.example.com/telegram")
        token = tc.sign_session(self.owner)
        response = await self.http.post(
            "/api/auth/connect", headers={"Authorization": "Bearer " + token}
        )
        self.assertEqual(response.json()["session_token"], token)
        self.assertNotIn("set-cookie", response.headers)

    async def test_websocket_ticket_preserves_telegram_session_scope(self):
        from app import main

        token = tc.sign_session(self.owner)
        response = await self.http.post(
            "/api/ws-ticket", headers={"Authorization": "Bearer " + token}
        )
        ticket = response.json()["ticket"]
        self.assertEqual(main.socket_ticket_sessions.pop(ticket), token)
        main.socket_tickets.pop(ticket)
        await self.http.post("/api/telegram/disconnect")
        self.assertFalse(await tc.authorized_session(token))

    async def test_owner_can_replace_bot_remotely_without_locking_current_device(self):
        token = tc.sign_session(self.owner)
        result = await self.http.patch(
            "/api/settings/telegram",
            json={"token": "888:new_bot"},
            headers={"Authorization": "Bearer " + token},
        )
        self.assertEqual(result.status_code, 200, result.text)
        fresh = result.json()["session_token"]
        self.assertFalse(await tc.authorized_session(token))
        self.assertTrue(await tc.authorized_session(fresh))
        owner = await get_setting("telegram_owner")
        self.assertEqual(owner["id"], 42)
        self.assertEqual(owner["bot"], tc.fingerprint("888:new_bot"))
        result = await self.http.post(
            "/api/telegram/pair", headers={"Authorization": "Bearer " + fresh}
        )
        self.assertEqual(result.status_code, 200, result.text)

    async def test_invalid_replacement_token_keeps_current_owner_and_token(self):
        self.remote.side_effect = FunPayError("invalid bot")
        result = await self.http.patch("/api/settings/telegram", json={"token": "888:invalid"})
        self.assertGreaterEqual(result.status_code, 400)
        self.assertEqual(vault.decrypt((await get_setting("telegram"))["token"]), TOKEN)
        self.assertTrue(await tc.authorized_session(tc.sign_session(self.owner)))

    async def test_refund_callback_keeps_its_own_acknowledgement_handler(self):
        from app import reply_requests

        callback = {
            "id": "cb-refund",
            "from": {"id": 42},
            "message": {"chat": {"id": 42, "type": "private"}},
            "data": "reply-refund:1:reject",
        }
        with patch.object(reply_requests, "handle_callback", AsyncMock()) as handler:
            await self.bot.dispatch(
                {"callback_query": callback}, await get_setting("telegram"), TOKEN
            )
            handler.assert_awaited_once()
        self.remote.assert_not_awaited()
