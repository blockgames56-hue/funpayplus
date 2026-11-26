"""No live purchases: durable adapter flows use mocked provider and FunPay APIs."""

import asyncio
import base64
import gc
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import (
    events,
    funpay,
    integrated_api,
    integrated_config,
    integrated_jobs,
    main,
    message_templates,
    plugin_catalog,
    rental_api,
    rental_verification,
    services,
    steam_rentals,
    support_tickets,
    template_api,
)
from app.auto_dump import parse_competitor_prices
from app.integrated_config import unpacked
from app.integrated_schemas import BindingCreate, RentalAccountCreate, RentalStateChange
from app.models import (
    Account,
    Base,
    Chat,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalLease,
    SupportRequest,
)
from app.plugin_providers import parse_target, trace_complete
from app.security import vault
from app.steam_security import guard_code
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ValidationTests(unittest.TestCase):
    def test_targets_require_exact_domains(self):
        self.assertEqual(
            parse_target(
                "steam-points", "https://steamcommunity.com/profiles/76561198000000000", {}
            ),
            "https://steamcommunity.com/profiles/76561198000000000",
        )
        self.assertIsNone(
            parse_target("steam-points", "https://steamcommunity.com.evil.example/id/buyer", {})
        )
        self.assertIsNone(
            parse_target(
                "auto-smm", "https://evil.example/?t.me/buyer", {"allowed_domains": ["t.me"]}
            )
        )
        self.assertEqual(parse_target("auto-stars", "@buyername", {}), "buyername")
        self.assertIsNone(parse_target("auto-stars", "otheruser extra text", {}))

    def test_trace_must_include_all_internal_transactions(self):
        self.assertFalse(trace_complete({"emulated": True, "transaction": {"success": True}}))
        self.assertFalse(
            trace_complete(
                {
                    "transaction": {"success": True, "out_msgs": [{"msg_type": "int_msg"}]},
                    "children": [],
                }
            )
        )
        self.assertTrue(
            trace_complete(
                {
                    "transaction": {"success": True, "out_msgs": [{"msg_type": "int_msg"}]},
                    "children": [{"transaction": {"success": True, "out_msgs": []}}],
                }
            )
        )
        self.assertFalse(trace_complete({"transaction": {"success": False}}))

    def test_dump_filters_and_guard(self):
        html = '<a class="tc-item" data-user="2" href="/lots/offer?id=3"><div class="media-user-name">Seller</div><div class="tc-desc-text">Steam account EU</div><div class="rating-stars rating-5"></div><div class="tc-price">100 ₽</div></a>'
        self.assertEqual(
            parse_competitor_prices(
                html, set(), "1", {"keywords": "steam|стим\neu", "min_rating": 4}
            ),
            [Decimal("100.00")],
        )
        self.assertEqual(
            parse_competitor_prices(html, set(), "1", {"excluded_sellers": ["SELLER"]}), []
        )
        self.assertEqual(parse_competitor_prices(html, set(), "1", {"keywords": "US"}), [])
        secret = base64.b64encode(b"12345678901234567890").decode()
        self.assertEqual(guard_code(secret, 0), guard_code(secret, 29))
        self.assertNotEqual(guard_code(secret, 0), guard_code(secret, 30))
        self.assertRegex(guard_code(secret, 0), r"^[23456789BCDFGHJKMNPQRTVWXY]{5}$")


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_smm_cannot_receive_refill_commands(self):
        async with self.session() as db:
            db.add(PluginJob(order_id=self.order_id, kind="auto-smm", state="done", remote_id="123",
                             payload='{}'))
            await db.commit()
        with patch.object(integrated_jobs, "smm_request", AsyncMock()) as request:
            handled = await integrated_jobs.handle_message(self.chat_id, self.account_id,
                {"sender_id": 42, "text": "рефилл 123", "is_my": False})
        self.assertFalse(handled)
        request.assert_not_awaited()

    async def test_disabled_plugin_does_not_accept_waiting_target(self):
        async with self.session() as db:
            job = PluginJob(order_id=self.order_id, kind="auto-stars", state="waiting_target")
            db.add(job)
            await db.commit()
            ident = job.id
        with patch.object(integrated_jobs, "update_target", AsyncMock()) as update:
            handled = await integrated_jobs.handle_message(self.chat_id, self.account_id,
                {"sender_id": 42, "text": "@buyername", "is_my": False})
        self.assertFalse(handled)
        update.assert_not_awaited()
        async with self.session() as db:
            self.assertEqual((await db.get(PluginJob, ident)).state, "waiting_target")

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="integrated-plugins-")
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + (Path(self.temp.name) / "test.db").as_posix()
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = []
        for module in (
            services,
            events,
            message_templates,
            rental_api,
            rental_verification,
            template_api,
            main,
            integrated_api,
            integrated_config,
            integrated_jobs,
            plugin_catalog,
            steam_rentals,
            support_tickets,
        ):
            item = patch.object(module, "Session", self.session)
            item.start()
            self.patches.append(item)
        self.client = AsyncMock()
        self.client.order_details.return_value = {
            "status": "paid",
            "buyer": {"user_id": 42},
            "offer_id": 123,
            "quantity": 2,
        }
        self.send = AsyncMock()
        for item in (
            patch.object(services.accounts, "client", AsyncMock(return_value=self.client)),
            patch.object(services.accounts, "send", self.send),
            patch.object(services.telegram, "notify", AsyncMock()),
            patch.object(integrated_jobs, "start_job"),
        ):
            item.start()
            self.patches.append(item)
        async with self.session() as db:
            account = Account(
                title="Seller",
                username="Seller",
                golden_key=vault.encrypt("dummy-session"),
                funpay_id=1,
                user_agent="test-user-agent",
            )
            db.add(account)
            await db.flush()
            lot = Lot(
                account_id=account.id,
                funpay_lot_id="123",
                node_id=81,
                category_title="Game",
                title="Digital product",
                price=Decimal("10"),
            )
            db.add(lot)
            order = Order(
                account_id=account.id,
                funpay_order_id="ORDER1",
                description="Digital product",
                sum=Decimal("20"),
                currency="RUB",
                buyer_id=42,
                buyer_name="Buyer",
                status="paid",
            )
            db.add(order)
            chat = Chat(
                account_id=account.id,
                funpay_chat_id="users-1-42",
                interlocutor_id=42,
                interlocutor_name="Buyer",
                current_order_id="ORDER1",
            )
            db.add(chat)
            await db.commit()
            self.account_id, self.lot_id, self.order_id, self.chat_id = (
                account.id,
                lot.id,
                order.id,
                chat.id,
            )

    async def asyncTearDown(self):
        await integrated_jobs.stop()
        await steam_rentals.stop()
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        # sqlite exception tracebacks can retain failed statement cursors until
        # collection; Windows cannot unlink the file while such a cursor lives.
        gc.collect()
        self.temp.cleanup()

    async def setup_smm(self):
        await integrated_config.save_config(
            "auto-smm",
            {
                "providers": [
                    {
                        "id": "test",
                        "name": "Test Provider",
                        "url": "https://provider.example/api/v2",
                        "api_key": "DO-NOT-LEAK-KEY",
                    }
                ]
            },
        )
        await integrated_api.add_binding(
            "auto-smm",
            BindingCreate(lot_id=self.lot_id, quantity=100, provider_id="test", service_id="15"),
        )
        await plugin_catalog.toggle("auto-smm", True)
        self.assertTrue(await integrated_jobs.ensure_for_order(self.order_id))
        async with self.session() as db:
            return (
                await db.scalar(select(PluginJob).where(PluginJob.order_id == self.order_id))
            ).id

    async def test_settings_encrypt_and_catalog_uses_real_state(self):
        job_id = await self.setup_smm()
        data = await integrated_api.status("auto-smm")
        self.assertNotIn("DO-NOT-LEAK-KEY", str(data))
        self.assertTrue(data["settings"]["providers"][0]["has_api_key"])
        async with self.session() as db:
            job = await db.get(PluginJob, job_id)
            self.assertEqual(job.quantity, 200)
            self.assertNotIn("DO-NOT-LEAK-KEY", job.payload)
            self.assertEqual(job.state, "waiting_target")
        state = next(row for row in await plugin_catalog.catalog() if row["id"] == "auto-smm")
        self.assertTrue(state["enabled"])
        await plugin_catalog.toggle("auto-smm", False)
        self.assertFalse((await integrated_config.stored_config("auto-smm"))["enabled"])

    async def test_smm_dispatch_persists_before_post_and_completes_once(self):
        ident = await self.setup_smm()
        await integrated_jobs.update_target(ident, "https://t.me/buyername")
        await integrated_jobs.confirm_target(ident)

        async def provider_call(provider, action, **fields):
            if action == "add":
                async with self.session() as db:
                    self.assertEqual((await db.get(PluginJob, ident)).state, "submitting")
                self.assertEqual(fields["quantity"], 200)
                return {"order": 12345}
            return {"status": "Completed", "remains": "0"}

        with patch.object(
            integrated_jobs, "smm_request", AsyncMock(side_effect=provider_call)
        ) as provider:
            await integrated_jobs.process_job(ident)
            await integrated_jobs.process_job(ident)
            await integrated_jobs.process_job(ident)
            self.assertEqual([call.args[1] for call in provider.await_args_list], ["add", "status"])
        async with self.session() as db:
            self.assertEqual((await db.get(PluginJob, ident)).state, "completed")
            self.assertTrue((await db.get(Order, self.order_id)).auto_delivered)
        self.assertEqual(self.send.await_count, 2)

    async def test_timeout_never_repeats_purchase(self):
        ident = await self.setup_smm()
        await integrated_jobs.update_target(ident, "https://t.me/buyername")
        await integrated_jobs.confirm_target(ident)
        with patch.object(
            integrated_jobs, "smm_request", AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
        ) as provider:
            await integrated_jobs.process_job(ident)
            await integrated_jobs.process_job(ident)
            provider.assert_awaited_once()
        async with self.session() as db:
            self.assertEqual((await db.get(PluginJob, ident)).state, "uncertain")
        with self.assertRaises(main.HTTPException):
            await integrated_api.job_action("auto-smm", ident, "retry")

    async def test_partial_is_attention_not_completed(self):
        ident = await self.setup_smm()
        await integrated_jobs.update_target(ident, "https://t.me/buyername")
        await integrated_jobs.confirm_target(ident)
        with patch.object(
            integrated_jobs,
            "smm_request",
            AsyncMock(side_effect=[{"order": 10}, {"status": "Partial", "remains": 20}]),
        ):
            await integrated_jobs.process_job(ident)
            await integrated_jobs.process_job(ident)
        async with self.session() as db:
            self.assertEqual((await db.get(PluginJob, ident)).state, "attention")
            self.assertFalse((await db.get(Order, self.order_id)).auto_delivered)

    async def test_failed_message_does_not_resend_on_provider_check(self):
        ident = await self.setup_smm()
        await integrated_jobs.update_target(ident, "https://t.me/buyername")
        await integrated_jobs.confirm_target(ident)
        with patch.object(
            integrated_jobs,
            "smm_request",
            AsyncMock(side_effect=[{"order": 10}, {"status": "Completed", "remains": 0}]),
        ):
            await integrated_jobs.process_job(ident)
            self.send.side_effect = main.FunPayError("unknown send result")
            await integrated_jobs.process_job(ident)
        with self.assertRaises(main.HTTPException):
            await integrated_api.job_action("auto-smm", ident, "check")
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            self.assertTrue(unpacked(job.payload)["delivery_attempted"])
            self.assertEqual(job.state, "uncertain")

    async def test_recipient_cannot_change_after_submission(self):
        ident = await self.setup_smm()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            job.remote_id, job.state = "10", "error"
            await db.commit()
        with self.assertRaises(ValueError):
            await integrated_jobs.update_target(ident, "https://t.me/somebodyelse")

    async def test_restart_recovers_states_without_repeat(self):
        ident = await self.setup_smm()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            job.state = "submitting"
            await db.commit()
        await integrated_jobs.recover()
        async with self.session() as db:
            self.assertEqual((await db.get(PluginJob, ident)).state, "uncertain")

    async def test_unconfigured_enable_rejected(self):
        with self.assertRaises(ValueError):
            await plugin_catalog.toggle("steam-points", True)

    async def test_bound_provider_cannot_be_removed_and_duplicate_binding_is_409(self):
        await self.setup_smm()
        with self.assertRaises(ValueError):
            await integrated_config.save_config("auto-smm", {"providers": []})
        with self.assertRaises(main.HTTPException) as error:
            await integrated_api.add_binding(
                "auto-smm",
                BindingCreate(
                    lot_id=self.lot_id, quantity=100, provider_id="test", service_id="15"
                ),
            )
        self.assertEqual(error.exception.status_code, 409)

    async def test_binding_edit_and_pause_apply_to_future_orders(self):
        ident = await self.setup_smm()
        async with self.session() as db:
            binding = await db.scalar(select(PluginBinding))
            binding_id = binding.id
        await integrated_api.edit_binding(
            "auto-smm",
            binding_id,
            BindingCreate(
                lot_id=self.lot_id,
                quantity=250,
                provider_id="test",
                service_id="20",
                is_active=False,
            ),
        )
        async with self.session() as db:
            binding = await db.get(PluginBinding, binding_id)
            self.assertFalse(binding.is_active)
            self.assertEqual(binding.quantity, 250)
            snapshot = unpacked((await db.get(PluginJob, ident)).payload)
            self.assertEqual(snapshot["service_id"], "15")

    async def test_resolve_lot_accepts_pasted_offer_reference(self):
        # ID распознаётся из разных форматов вставки.
        for query in ("123", "offer=123&location=offer",
                      "https://funpay.com/lots/offer?id=123"):
            data = await integrated_api.resolve_lot("steam-rent", {"query": query})
            self.assertEqual(data["lot_id"], self.lot_id)
            self.assertEqual(data["funpay_lot_id"], "123")
        # Неизвестный лот: sync_lots не нашёл совпадений — понятная ошибка,
        # при этом синхронизация с FunPay действительно запускается.
        sync = AsyncMock(return_value=0)
        with patch.object(services.accounts, "sync_lots", sync), patch(
            "app.lot_editor.load_form",
            AsyncMock(side_effect=funpay.FunPayError("лот недоступен")),
        ):
            with self.assertRaises(integrated_api.HTTPException) as missing:
                await integrated_api.resolve_lot("auto-smm", {"query": "99999999"})
            self.assertEqual(missing.exception.status_code, 404)
            self.assertIn("лот недоступен", missing.exception.detail)
            sync.assert_awaited()

    async def test_manual_resolution_does_not_purchase_or_send_again(self):
        ident = await self.setup_smm()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            job.state = "uncertain"
            await db.commit()
        self.send.reset_mock()
        with patch.object(integrated_api, "smm_request", AsyncMock()) as provider:
            await integrated_api.job_action("auto-smm", ident, "resolve")
            provider.assert_not_awaited()
        self.send.assert_not_awaited()
        async with self.session() as db:
            self.assertTrue((await db.get(Order, self.order_id)).auto_delivered)
            self.assertTrue(unpacked((await db.get(PluginJob, ident)).payload)["manually_resolved"])

    async def test_stars_waits_for_previous_wallet_transaction(self):
        settings = {
            "fragment_cookie": "fake-cookie",
            "fragment_hash": "fake-hash",
            "ton_api_key": "fake-key",
            "mnemonic": " ".join(["test"] * 24),
            "wallet_address": "fake-wallet",
        }
        await integrated_config.save_config("auto-stars", settings)
        await integrated_api.add_binding(
            "auto-stars", BindingCreate(lot_id=self.lot_id, quantity=50)
        )
        await plugin_catalog.toggle("auto-stars", True)
        await integrated_jobs.ensure_for_order(self.order_id)
        async with self.session() as db:
            job = await db.scalar(select(PluginJob).where(PluginJob.order_id == self.order_id))
            job.state, job.target = "ready", "buyername"
            other = Order(
                account_id=self.account_id,
                funpay_order_id="ORDER2",
                description="Stars",
                sum=Decimal("10"),
                currency="RUB",
                buyer_id=42,
                buyer_name="Buyer",
                status="paid",
            )
            db.add(other)
            await db.flush()
            db.add(
                PluginJob(
                    order_id=other.id,
                    kind="auto-stars",
                    quantity=50,
                    state="submitted",
                    remote_id="a" * 64,
                    payload=job.payload,
                )
            )
            await db.commit()
            ident = job.id
        with patch.object(integrated_jobs, "prepare_stars", AsyncMock()) as prepare:
            await integrated_jobs.process_job(ident)
            prepare.assert_not_awaited()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            self.assertEqual(job.state, "ready")
            self.assertIsNotNone(job.next_check_at)

    async def setup_rental(self):
        binding = await integrated_api.add_binding(
            "steam-rent", BindingCreate(lot_id=self.lot_id, quantity=1)
        )
        secret = base64.b64encode(b"12345678901234567890").decode()
        data = RentalAccountCreate(
            binding_id=binding["id"],
            label="Seller account",
            login="seller-account",
            password="original-password",
            shared_secret=secret,
            identity_secret=secret,
            device_id="android:device",
            steamid="76561198000000000",
        )
        inventory = await integrated_api.add_inventory(data)
        await plugin_catalog.toggle("steam-rent", True)
        await integrated_jobs.ensure_for_order(self.order_id)
        async with self.session() as db:
            job = await db.scalar(select(PluginJob).where(PluginJob.order_id == self.order_id))
            return inventory["id"], job.id

    async def test_rental_issue_and_failed_rotation_quarantines_account(self):
        account_id, job_id = await self.setup_rental()
        await integrated_jobs.process_job(job_id)
        snapshot = await integrated_api.status("steam-rent")
        self.assertEqual(len(snapshot["leases"]), 1)
        self.assertEqual(snapshot["leases"][0]["buyer"], "Buyer")
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease).where(RentalLease.job_id == job_id))
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "leased")
            ident = lease.id
        with patch.object(
            steam_rentals, "rotate_password", AsyncMock(side_effect=ValueError("not confirmed"))
        ):
            await steam_rentals.rotate(ident, force=True)
        async with self.session() as db:
            row = await db.get(RentalAccount, account_id)
            self.assertEqual(row.state, "quarantine")
            self.assertEqual(unpacked(row.credentials)["password"], "original-password")
            self.assertTrue(row.pending_password)
        with self.assertRaises(main.HTTPException):
            await integrated_api.inventory_state(account_id, RentalStateChange(state="free"))

    async def test_successful_rotation_stores_verified_password(self):
        account_id, job_id = await self.setup_rental()
        await integrated_jobs.process_job(job_id)
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease).where(RentalLease.job_id == job_id))
            ident = lease.id
        with patch.object(steam_rentals, "rotate_password", AsyncMock()):
            await steam_rentals.rotate(ident, force=True)
        async with self.session() as db:
            row = await db.get(RentalAccount, account_id)
            self.assertEqual(row.state, "free")
            self.assertNotEqual(unpacked(row.credentials)["password"], "original-password")
            self.assertIsNone(row.pending_password)

    async def test_same_steam_identity_cannot_be_added_twice(self):
        account_id, _ = await self.setup_rental()
        values = await integrated_api.inventory_credentials(account_id)
        with self.assertRaises(main.HTTPException) as error:
            await integrated_api.add_inventory(RentalAccountCreate(**values))
        self.assertEqual(error.exception.status_code, 409)

    async def test_rent_stock_does_not_enable_manually_disabled_lot(self):
        await self.setup_rental()
        await integrated_config.save_config("steam-rent", {"auto_hide_empty": True})
        async with self.session() as db:
            lot = await db.get(Lot, self.lot_id)
            lot.is_active = False
            await db.commit()
        self.client.update_lot.reset_mock()
        await steam_rentals.update_stock_visibility()
        self.client.update_lot.assert_not_awaited()
        await services.save_setting("integrated:hidden:steam-rent", [self.lot_id])
        await steam_rentals.update_stock_visibility()
        self.client.update_lot.assert_awaited_once_with("123", active=True)

    async def test_guard_only_for_active_buyer(self):
        _, job_id = await self.setup_rental()
        await integrated_jobs.process_job(job_id)
        own = {"sender_id": 42, "text": "!код", "is_my": False}
        other = {"sender_id": 43, "text": "!код", "is_my": False}
        with patch(
            "app.integrated_config.pool.request",
            AsyncMock(
                return_value=httpx.Response(
                    200,
                    json={"response": {"server_time": 123456}},
                    request=httpx.Request("POST", "https://api.steampowered.com"),
                )
            ),
        ):
            self.assertFalse(
                await steam_rentals.handle_command(self.chat_id, self.account_id, other)
            )
            self.assertTrue(await steam_rentals.handle_command(self.chat_id, self.account_id, own))
        self.assertIn("Steam Guard", self.send.call_args.args[1])
        self.assertIn("J7CW3", self.send.call_args.args[1])

    async def test_support_200_login_page_not_success_and_no_blind_retry(self):
        await integrated_config.save_config("support-tickets", {"account_id": self.account_id})
        form = '<form action="/tickets/create/1"><input name="ticket[_token]" value="csrf"></form>'
        original = httpx.AsyncClient
        posts = []

        def transport(request):
            if request.url.host == "funpay.com":
                return httpx.Response(
                    302, headers={"location": "https://support.funpay.com/tickets/new/1"}
                )
            if request.method == "GET":
                return httpx.Response(200, text=form)
            posts.append(request)
            return httpx.Response(200, text="login required")

        with patch.object(
            support_tickets.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: original(
                transport=httpx.MockTransport(transport), **kwargs
            ),
        ):
            with self.assertRaises(ValueError):
                await support_tickets.send([self.order_id])
            with self.assertRaises(ValueError):
                await support_tickets.send([self.order_id])
        self.assertEqual(len(posts), 1)
        async with self.session() as db:
            self.assertEqual((await db.scalar(select(SupportRequest))).state, "uncertain")

    async def test_support_success_requires_ticket_redirect(self):
        await integrated_config.save_config(
            "support-tickets", {"account_id": self.account_id, "phpsessid": "fake-session"}
        )
        form = '<form action="/tickets/create/1"><input name="ticket[_token]" value="csrf"></form>'
        original = httpx.AsyncClient
        with patch.object(
            support_tickets.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: original(
                transport=httpx.MockTransport(
                    lambda request: (
                        httpx.Response(200, text=form)
                        if request.method == "GET"
                        else httpx.Response(302, headers={"location": "/tickets/999"})
                    )
                ),
                **kwargs,
            ),
        ):
            ident = await support_tickets.send([self.order_id])
        async with self.session() as db:
            row = await db.get(SupportRequest, ident)
            self.assertEqual(row.remote_id, "999")
            self.assertEqual(row.state, "sent")

    async def test_authenticated_http_status(self):
        from app.config import config

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://test"
        ) as http:
            unauthenticated = await http.get("/api/plugins/integrated/auto-smm")
            self.assertEqual(unauthenticated.status_code, 401)
            response = await http.get(
                "/api/plugins/integrated/auto-smm",
                headers={"Authorization": f"Bearer {config.api_token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["bindings"], [])

    async def test_extension_survives_restart_without_adding_hours_twice(self):
        inventory_id, base_job_id = await self.setup_rental()
        await integrated_jobs.process_job(base_job_id)
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease).where(RentalLease.job_id == base_job_id))
            expires = steam_rentals.aware(lease.expires_at)
            base_job = await db.get(PluginJob, base_job_id)
            payload = unpacked(base_job.payload)
            payload.update(
                is_extension=True,
                service_id=str((await db.get(RentalAccount, inventory_id)).binding_id),
            )
            payload.pop("delivery_attempted", None)
            order = Order(
                account_id=self.account_id,
                funpay_order_id="EXT1",
                description="Extension",
                buyer_id=42,
                buyer_name="Buyer",
                status="paid",
                sum=Decimal("10"),
                currency="RUB",
                delivery_state="integrated_pending",
            )
            db.add(order)
            await db.flush()
            job = PluginJob(
                order_id=order.id,
                kind="steam-rent",
                state="ready",
                quantity=2,
                payload=integrated_config.packed(payload),
            )
            db.add(job)
            await db.commit()
            ident, lease_id = job.id, lease.id
        with patch.object(
            integrated_jobs, "finish_job", AsyncMock(side_effect=asyncio.CancelledError)
        ):
            with self.assertRaises(asyncio.CancelledError):
                await integrated_jobs.process_job(ident)
        await integrated_jobs.recover()
        self.send.reset_mock()
        await integrated_jobs.process_job(ident)
        async with self.session() as db:
            self.assertEqual(
                steam_rentals.aware((await db.get(RentalLease, lease_id)).expires_at),
                expires + timedelta(hours=2),
            )
            job = await db.get(PluginJob, ident)
            self.assertEqual(job.state, "uncertain")
            job.state = "error"  # Even an incorrect externally restored state must not reissue.
            await db.commit()
        with self.assertRaises(main.HTTPException):
            await integrated_api.job_action("steam-rent", ident, "retry")
        self.send.assert_not_awaited()

    async def test_queued_rotation_rechecks_extended_expiry(self):
        inventory_id, job_id = await self.setup_rental()
        await integrated_jobs.process_job(job_id)
        async with self.session() as db:
            lease_id = await db.scalar(select(RentalLease.id).where(RentalLease.job_id == job_id))
        with patch.object(steam_rentals, "rotate_password", AsyncMock()) as rotate:
            await steam_rentals.rotate(lease_id)
            rotate.assert_not_awaited()
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, inventory_id)).state, "leased")

    async def test_rental_custom_success_message_is_used(self):
        _, job_id = await self.setup_rental()
        async with self.session() as db:
            job = await db.get(PluginJob, job_id)
            payload = unpacked(job.payload)
            payload["config"]["success_message"] = "Спасибо, {buyer}! Ваш заказ {order_id}."
            job.payload = integrated_config.packed(payload)
            await db.commit()
        await integrated_jobs.process_job(job_id)
        text = self.send.call_args.args[1]
        self.assertIn("original-password", text)
        self.assertIn("Спасибо, Buyer! Ваш заказ ORDER1.", text)
