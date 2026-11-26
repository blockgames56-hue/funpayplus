"""Offline regression tests for templates, manual rentals and PC-club checks."""

import io
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
import test_integrated_plugins as fixtures
from app import (
    integrated_config,
    integrated_jobs,
    message_templates,
    rental_api,
    rental_verification,
    steam_rentals,
    template_api,
)
from app.integrated_config import packed, unpacked
from app.integrated_schemas import RentConfig
from app.models import (
    Activity,
    BlacklistEntry,
    Order,
    PluginJob,
    RentalAccount,
    RentalAccountBinding,
    RentalLease,
    utcnow,
)
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select


class FormatterTests(unittest.TestCase):
    def test_values_are_not_evaluated_or_recursively_substituted(self):
        self.assertEqual(
            message_templates.MessageFormatter.format(
                "{buyer}: {key}", {"buyer": "Alice", "key": "{password}"}
            ),
            "Alice: {password}",
        )
        for text in ("{buyer.__class__}", "{buyer!r}", "{buyer[0]}", "{buyer:>10}", "{unknown}"):
            with self.assertRaises(ValueError):
                message_templates.MessageFormatter.validate(text, ("buyer",))
        with self.assertRaises(ValueError):
            message_templates.MessageFormatter.format("{password}", {})

    def test_all_required_event_keys_and_source_defaults_exist(self):
        keys = {key for _, key in message_templates.CATALOG}
        self.assertTrue(
            {
                "roblox_start",
                "roblox_success",
                "roblox_no_money",
                "roblox_invalid_link",
                "roblox_api_fail",
                "gift_friend_request",
                "gift_success",
                "gift_region_error",
                "gift_timeout",
                "discord_start",
                "discord_success",
                "discord_invalid_invite",
                "discord_no_tokens",
                "rent_credentials",
                "rent_warning_10m",
                "rent_finished",
                "rent_no_accounts",
                "route_success",
                "route_fail",
            }
            <= keys
        )
        self.assertIn(
            "ОПЛАТА ПОЛУЧЕНА", message_templates.CATALOG["steam-rent", "pc_request"].default
        )
        for row in message_templates.CATALOG.values():
            message_templates.MessageFormatter.validate(row.default, row.variables)

    def test_photo_configuration_rejects_invalid_location_and_timezone(self):
        for values in (
            {"photo_clubs": [{"name": "X", "lat": 200, "lon": 1}]},
            {"photo_timezone": "Invalid/Zone"},
            {"photo_notify_chat_ids": ["not-a-chat"]},
            {"photo_ai_base_url": "http://localhost"},
        ):
            with self.assertRaises(ValueError):
                RentConfig(**values)


class RentalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.IntegrationTests.asyncSetUp
    asyncTearDown = fixtures.IntegrationTests.asyncTearDown
    setup_rental = fixtures.IntegrationTests.setup_rental

    async def test_account_can_be_added_before_creating_lot_bindings(self):
        result = await rental_api.connect(
            rental_api.Connection(label="Unbound", login="draft-login", password="dummy")
        )
        state = await rental_api.workspace()
        row = next(r for r in state["inventory"] if r["id"] == result["id"])
        self.assertEqual(row["binding_ids"], [])
        self.assertEqual(row["state"], "draft")

    async def test_blacklist_added_after_payment_blocks_issuance(self):
        ident, job_id = await self.setup_rental()
        async with self.session() as db:
            db.add(BlacklistEntry(user_id=42, username="Buyer"))
            await db.commit()
        await integrated_jobs.process_job(job_id)
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, ident)).state, "free")
            self.assertEqual((await db.get(PluginJob, job_id)).state, "attention")
            self.assertIsNone(await db.scalar(select(RentalLease.id)))

    async def test_override_reset_and_legacy_messages(self):
        await integrated_config.save_config(
            "steam-points", {"success_message": "Старый текст: {quantity}"}
        )
        rows = await template_api.listing("steam-points")
        self.assertEqual(
            next(r for r in rows if r["key"] == "delivery_success")["text"],
            "Старый текст: {quantity}",
        )
        await template_api.update(
            "steam-points", "delivery_success", template_api.TemplateInput(text="Новый: {quantity}")
        )
        self.assertEqual(
            await message_templates.render("steam-points", "delivery_success", {"quantity": 12}),
            "Новый: 12",
        )
        await template_api.reset("steam-points", "delivery_success")
        self.assertNotIn(
            "Старый текст",
            await message_templates.render("steam-points", "delivery_success", {"quantity": 12}),
        )
        with self.assertRaises(HTTPException):
            await template_api.update(
                "steam-points", "delivery_success", template_api.TemplateInput(text="{password}")
            )

    async def test_custom_rent_credentials_reach_actual_delivery(self):
        _, job_id = await self.setup_rental()
        await template_api.update(
            "steam-rent",
            "rent_credentials",
            template_api.TemplateInput(text="Доступ {login} / {password}, до {expires}"),
        )
        await integrated_jobs.process_job(job_id)
        self.assertIn("Доступ seller-account / original-password", self.send.call_args.args[1])
        async with self.session() as db:
            logs = (await db.scalars(select(Activity))).all()
            self.assertFalse(any("original-password" in row.text for row in logs))

    async def test_manual_issue_is_idempotent_and_does_not_fake_an_order(self):
        account_id, _ = await self.setup_rental()
        request = rental_api.ManualLease(
            account_id=account_id, chat_id=self.chat_id, minutes=60, request_key="manual-test-00001"
        )
        first = await rental_api.manual_lease(request)
        count = self.send.await_count
        second = await rental_api.manual_lease(request)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.send.await_count, count)
        async with self.session() as db:
            lease = await db.get(RentalLease, first["id"])
            self.assertIsNone(lease.job_id)
            self.assertEqual(len((await db.scalars(select(Order))).all()), 1)
        state = await rental_api.workspace()
        self.assertTrue(state["leases"][0]["manual"])
        with patch.object(steam_rentals, "rotate_password", AsyncMock()):
            await steam_rentals.rotate(first["id"], force=True)
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "free")

    async def test_manual_extend_is_idempotent_and_stale_rotation_cannot_close_it(self):
        _, job_id = await self.setup_rental()
        await integrated_jobs.process_job(job_id)
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease))
            ident, old = lease.id, steam_rentals.aware(lease.expires_at)
        payload = rental_api.Extend(minutes=35, request_key="extend-test-00001")
        await rental_api.extend(ident, payload)
        await rental_api.extend(ident, payload)
        with patch.object(steam_rentals, "rotate_password", AsyncMock()) as rotation:
            await steam_rentals.rotate(ident)
            rotation.assert_not_awaited()
        async with self.session() as db:
            self.assertEqual(
                steam_rentals.aware((await db.get(RentalLease, ident)).expires_at),
                old + timedelta(minutes=35),
            )

    async def test_shared_inventory_is_visible_to_secondary_binding(self):
        account_id, job_id = await self.setup_rental()
        async with self.session() as db:
            account = await db.get(RentalAccount, account_id)
            job = await db.get(PluginJob, job_id)
            # Put primary outside the requested allocation; use its secondary link.
            binding = account.binding_id
            account.binding_id = 999
            db.add(RentalAccountBinding(account_id=account_id, binding_id=binding))
            await db.commit()
            order = await db.get(Order, job.order_id)
            await steam_rentals.issue(db, job, order, unpacked(job.payload))
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "leased")

    async def photo_job(self):
        account_id, ident = await self.setup_rental()
        config = (await integrated_config.plain_config("steam-rent")) | {
            "require_photo_review": True
        }
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            payload = unpacked(job.payload)
            job.state = "waiting_review"
            await rental_verification.begin(job, payload, config, db)
            await db.commit()
        return account_id, ident

    async def test_photo_timeout_releases_reserved_account_once(self):
        account_id, ident = await self.photo_job()
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "reserved")
            job = await db.get(PluginJob, ident)
            payload = unpacked(job.payload)
            payload["photo_expires"] = (utcnow() - timedelta(seconds=1)).isoformat()
            job.payload = packed(payload)
            await db.commit()
        await rental_verification.tick()
        count = self.send.await_count
        await rental_verification.tick()
        self.assertEqual(self.send.await_count, count)
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "free")
            self.assertEqual((await db.get(PluginJob, ident)).state, "attention")

    async def test_rejections_retry_then_stop_and_release_hold(self):
        account_id, ident = await self.photo_job()
        for attempt in range(3):
            async with self.session() as db:
                job = await db.get(PluginJob, ident)
                payload = unpacked(job.payload)
                payload["photo_state"] = "review"
                job.payload = packed(payload)
                await db.commit()
            await rental_verification.decide(ident, "reject", "Не видно рабочее место")
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            self.assertEqual(job.state, "attention")
            self.assertEqual(unpacked(job.payload)["photo_rejects"], 3)
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "free")

    async def test_approve_allocates_the_reserved_account_exactly_once(self):
        account_id, ident = await self.photo_job()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            payload = unpacked(job.payload)
            payload["photo_state"] = "review"
            job.payload = packed(payload)
            await db.commit()
        await rental_verification.decide(ident, "approve")
        with self.assertRaises(ValueError):
            await rental_verification.decide(ident, "approve")
        await integrated_jobs.process_job(ident)
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, account_id)).state, "leased")
            self.assertEqual(len((await db.scalars(select(RentalLease))).all()), 1)

    async def test_restart_during_photo_analysis_requires_owner_review(self):
        _, ident = await self.photo_job()
        async with self.session() as db:
            job = await db.get(PluginJob, ident)
            payload = unpacked(job.payload)
            payload["photo_state"] = "processing"
            job.payload = packed(payload)
            await db.commit()
        await rental_verification.tick()
        async with self.session() as db:
            self.assertEqual(
                unpacked((await db.get(PluginJob, ident)).payload)["photo_state"], "review"
            )

    async def test_missing_exif_or_no_checks_never_auto_approves(self):
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, "PNG")
        config = RentConfig(photo_exif_time=True, photo_exif_geo=True).model_dump()
        report, raw = await rental_verification.check_image(buf.getvalue(), config, utcnow())
        self.assertEqual(report["decision"], "review")
        self.assertTrue(all(c["passed"] is None for c in report["checks"]))
        self.assertEqual(Image.open(io.BytesIO(raw)).format, "JPEG")
        report, _ = await rental_verification.check_image(
            buf.getvalue(), RentConfig().model_dump(), utcnow()
        )
        self.assertEqual(report["decision"], "review")

    async def test_photo_download_rejects_untrusted_hosts(self):
        for url in (
            "https://evil.example/photo.jpg",
            "https://sfunpay.com.evil.example/photo.jpg",
            "http://sfunpay.com/photo.jpg",
            "https://user:password@sfunpay.com/photo.jpg",
        ):
            with self.assertRaises(ValueError):
                await rental_verification.download(url)

    async def test_ai_network_error_goes_to_manual_review(self):
        buf = io.BytesIO()
        Image.new("RGB", (100, 100)).save(buf, "PNG")
        with patch.object(
            rental_verification.pool,
            "request",
            AsyncMock(side_effect=httpx.ConnectError("offline")),
        ):
            report, _ = await rental_verification.check_image(
                buf.getvalue(),
                RentConfig(
                    photo_ai=True, photo_ai_api_key="dummy", photo_ai_model="model"
                ).model_dump(),
                utcnow(),
            )
        self.assertEqual(report["decision"], "review")
        self.assertIsNone(report["checks"][0]["passed"])

    async def test_guard_parenthesis_and_extension_url(self):
        _, ident = await self.setup_rental()
        await integrated_jobs.process_job(ident)
        result = httpx.Response(
            200,
            json={"response": {"server_time": 123456}},
            request=httpx.Request("POST", "https://api.steampowered.com"),
        )
        with patch.object(integrated_config.pool, "request", AsyncMock(return_value=result)):
            self.assertTrue(
                await steam_rentals.handle_command(
                    self.chat_id, self.account_id, {"sender_id": 42, "text": "!код(seller-account)"}
                )
            )
        self.assertIn("J7CW3", self.send.call_args.args[1])

    async def test_connection_without_mafile_remains_draft(self):
        await self.setup_rental()
        async with self.session() as db:
            binding = (await db.scalar(select(RentalAccount))).binding_id
        data = rental_api.Connection(
            label="Draft", binding_ids=[binding], login="another", password="secret"
        )
        result = await rental_api.connect(data)
        self.assertEqual(result["state"], "draft")
        with self.assertRaises(HTTPException):
            await rental_api.manual_lease(
                rental_api.ManualLease(
                    account_id=result["id"],
                    chat_id=self.chat_id,
                    minutes=60,
                    request_key="draft-issue-000001",
                )
            )

    async def test_five_star_bonus_is_awarded_once_and_warning_flags_reset(self):
        _, ident = await self.setup_rental()
        await integrated_jobs.process_job(ident)
        async with self.session() as db:
            order = await db.get(Order, self.order_id)
            order.status = "closed"
            lease = await db.scalar(select(RentalLease))
            lease.warned_at = lease.warned_30_at = utcnow()
            before = steam_rentals.aware(lease.expires_at)
            await db.commit()
        self.client.order_details.return_value = {"review": {"rating": 5}}
        config = RentConfig(review_bonus_minutes=60).model_dump()
        await steam_rentals.review_bonuses(config)
        await steam_rentals.review_bonuses(config)
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease))
            self.assertEqual(steam_rentals.aware(lease.expires_at), before + timedelta(hours=1))
            self.assertIsNone(lease.warned_at)
            self.assertIsNone(lease.warned_30_at)

    async def test_review_event_awards_immediately_for_stale_paid_order_only_once(self):
        _, ident = await self.setup_rental()
        await integrated_jobs.process_job(ident)
        from app.services import save_setting

        await save_setting(
            "integrated:steam-rent", RentConfig(enabled=True, review_bonus_minutes=30).model_dump()
        )
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease))
            before = steam_rentals.aware(lease.expires_at)
        self.client.order_details.return_value = {
            "status": "closed",
            "buyer": {"user_id": 42},
            "review": {"rating": 5},
        }
        self.client.order_details.reset_mock()
        await steam_rentals.on_review_message(self.chat_id, self.account_id)
        await steam_rentals.on_review_message(self.chat_id, self.account_id)
        async with self.session() as db:
            lease = await db.scalar(select(RentalLease))
            self.assertEqual(steam_rentals.aware(lease.expires_at), before + timedelta(minutes=30))
            self.assertEqual((await db.get(Order, self.order_id)).status, "closed")
        self.client.order_details.assert_awaited_once()

    async def test_restart_quarantines_an_owner_password_tool_in_progress(self):
        ident, _ = await self.setup_rental()
        async with self.session() as db:
            row = await db.get(RentalAccount, ident)
            row.state = "rotating"
            await db.commit()
        await steam_rentals.recover()
        async with self.session() as db:
            self.assertEqual((await db.get(RentalAccount, ident)).state, "quarantine")

    async def test_automatic_checks_require_a_strict_boolean_result(self):
        buf = io.BytesIO()
        Image.new("RGB", (60, 60)).save(buf, "PNG")
        config = RentConfig(
            photo_ai=True, photo_ai_api_key="dummy", photo_ai_model="model"
        ).model_dump()
        for value, expected in ((True, "approved"), ("true", "review"), (False, "review")):
            result = httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": __import__("json").dumps(
                                            {"passed": value, "reason": "Обстановка клуба"}
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                },
                request=httpx.Request("POST", "https://generativelanguage.googleapis.com"),
            )
            with patch.object(
                rental_verification.pool, "request", AsyncMock(return_value=result)
            ) as http:
                report, _ = await rental_verification.check_image(buf.getvalue(), config, utcnow())
                self.assertEqual(report["decision"], expected)
                self.assertEqual(
                    http.call_args.kwargs["json"]["contents"][0]["parts"][-1]["inline_data"][
                        "mime_type"
                    ],
                    "image/jpeg",
                )
