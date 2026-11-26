"""Offline protocol tests. No credentials and no live FunPay operations."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
from app.copylots import editor_fields, editor_form, publish_copy
from app.funpay import (
    ChallengeException,
    FunPayClient,
    FunPayError,
    UnauthorizedException,
    app_data,
    avatar_url,
    currency,
    money,
    parse_lots,
    parse_messages,
    parse_profile,
    parse_runner_users,
    soup_of,
)
from app.lot_editor import describe_form, merge_fields
from app.services import command_reply

PROFILE = """<html><body data-app-data='{"userId":42,"csrf-token":"csrf-sample"}'>
<div class="user-link-name">SampleStore</div>
<span class="badge-balance">1 234,56 ₽</span><span class="badge-balance">12.50 $</span>
<span class="badge-trade">3</span><span class="badge-orders">1</span></body></html>"""


class ParserTests(unittest.TestCase):
    def test_runner_users_supply_avatar_urls(self):
        result = {"users": {"42": {"avatar": "/files/buyer.png"}, "43": {"avatar": "https://evil.example/a.png"}}}
        self.assertEqual(parse_runner_users(result), {42: "https://funpay.com/files/buyer.png"})

    def test_avatar_prefers_photo_over_unrelated_page_image(self):
        html = '<div><img src="/badge.png"><div class="avatar-photo" style="background-image:url(/files/real.png)"></div></div>'
        self.assertEqual(avatar_url(soup_of(html)), "https://funpay.com/files/real.png")

    def test_lot_editor_preserves_category_fields_and_validates_choices(self):
        html = """<form><input name="offer_id" value="0"><input name="node_id" value="123">
        <input name="fields[summary][ru]" value="Пример"><input name="price" value="100">
        <select name="fields[server]"><option value="1" selected>Первый</option><option value="2">Второй</option></select></form>"""
        metadata, fields = describe_form(html)
        changed = merge_fields(metadata, fields, {"fields[server]": "2", "price": "120"})
        self.assertEqual(changed["fields[summary][ru]"], "Пример")
        self.assertEqual(changed["fields[server]"], "2")
        with self.assertRaises(FunPayError):
            merge_fields(metadata, fields, {"fields[server]": "unexpected"})
        with self.assertRaises(FunPayError):
            merge_fields(metadata, fields, {"csrf_token": "attacker"})

    def test_command_matches_exact_or_contains_in_order(self):
        commands = [
            {"trigger": "/sample", "reply": "Первый", "match": "exact", "is_active": False},
            {"trigger": "/sample", "reply": "Второй", "match": "exact"},
            {"trigger": "доставка", "reply": "Третий", "match": "contains"},
        ]
        self.assertEqual(command_reply(commands, "  /SAMPLE  "), "Второй")
        self.assertEqual(command_reply(commands, "Когда доставка?"), "Третий")
        self.assertEqual(command_reply(commands, "/sample сейчас"), "")

    def test_avatar_from_funpay_and_rejects_external_image(self):
        self.assertEqual(
            avatar_url(
                soup_of(
                    '<div class="avatar-photo" style="background-image:url(/files/avatar.png)"></div>'
                )
            ),
            "https://funpay.com/files/avatar.png",
        )
        self.assertIsNone(avatar_url(soup_of('<img src="https://other.example/avatar.png">')))

    def test_editor_keeps_selected_options(self):
        form = editor_form(
            soup_of(
                '<form><input name="offer_id" value="0"><input name="fields[summary][ru]" value="Sample"><input name="price" value="10"><select name="fields[server]"><option value="1">A</option><option value="2" selected>B</option></select></form>'
            )
        )
        fields = editor_fields(form)
        self.assertEqual(fields["fields[server]"], "2")
        self.assertEqual(fields["offer_id"], "0")

    def test_editor_ignores_unchecked_and_file_inputs(self):
        form = editor_form(
            soup_of(
                '<form><input name="fields[summary][ru]"><input name="price"><input name="file" type="file"><input type="checkbox" name="active"><input type="checkbox" name="agree" checked value="on"></form>'
            )
        )
        fields = editor_fields(form)
        self.assertNotIn("active", fields)
        self.assertNotIn("file", fields)
        self.assertEqual(fields["agree"], "on")

    def test_editor_rejects_unrecognized_form(self):
        with self.assertRaises(FunPayError):
            editor_form(soup_of('<form action="/account/login"><input name="password"></form>'))

    def test_profile_data_attributes(self):
        profile = parse_profile(PROFILE)
        self.assertEqual(profile["funpay_id"], 42)
        self.assertEqual(profile["csrf"], "csrf-sample")
        self.assertEqual(profile["username"], "SampleStore")
        self.assertEqual(profile["balance_rub"], Decimal("1234.56"))
        self.assertEqual(profile["balance_usd"], Decimal("12.50"))
        self.assertEqual(profile["balance_eur"], 0)
        self.assertEqual(profile["active_sales"], 3)

    def test_meta_csrf_fallback(self):
        profile = parse_profile(
            '<meta name="csrf-token" content="sample"><a class="user-link" href="/users/17/"><span class="user-link-name">Seller</span></a>'
        )
        self.assertEqual(profile["funpay_id"], 17)
        self.assertEqual(profile["csrf"], "sample")

    def test_logged_out_is_not_accepted(self):
        with self.assertRaises(UnauthorizedException):
            parse_profile('<body><a href="/account/login">Login</a></body>')

    def test_missing_csrf_fails_closed(self):
        with self.assertRaises(FunPayError):
            parse_profile(
                '<a class="user-link" data-id="5"><span class="user-link-name">Sample</span></a>'
            )

    def test_invalid_app_data(self):
        with self.assertRaises(FunPayError):
            app_data(soup_of("<body data-app-data='invalid'>"))

    def test_currencies_never_merged(self):
        self.assertEqual(currency("125 EUR"), "EUR")
        self.assertEqual(currency("125 $"), "USD")
        self.assertEqual(currency("125 ₽"), "RUB")

    def test_decimal_no_float_drift(self):
        self.assertEqual(money("1\u00a0234,50 ₽") + money("0,10 ₽"), Decimal("1234.60"))

    def test_message_extracts_author_and_plain_text(self):
        data = {
            "node": {"id": 9},
            "messages": [
                {
                    "id": 7,
                    "date": 1700000000,
                    "html": '<div class="chat-msg-item"><a class="chat-msg-author-link" href="/users/42/">Sample</a><div class="chat-msg-text">A &amp; B<br>hello</div></div>',
                }
            ],
        }
        result = parse_messages(data, 42)
        self.assertEqual(result[0]["sender_id"], 42)
        self.assertTrue(result[0]["is_my"])
        self.assertEqual(result[0]["text"], "A & B\nhello")
        self.assertEqual(result[0]["node_id"], "9")

    def test_message_extracts_sender_avatar(self):
        data = {
            "node": {"id": "users-1-42"},
            "messages": [
                {
                    "id": 8,
                    "html": '<div class="chat-msg-item"><div class="avatar-photo" style="background-image:url(/files/person.jpg)"></div><a class="chat-msg-author-link" href="/users/42/">Buyer</a></div>',
                }
            ],
        }
        self.assertEqual(
            parse_messages(data, 1)[0]["sender_avatar"], "https://funpay.com/files/person.jpg"
        )

    def test_image_message(self):
        data = {
            "node": {"id": "users-1-42"},
            "messages": [
                {
                    "id": 1,
                    "html": '<a class="chat-img-link" href="https://funpay.com/image.png"><img></a>',
                }
            ],
        }
        result = parse_messages(data, 42)
        self.assertTrue(result[0]["has_image"])
        self.assertEqual(result[0]["image_url"], "https://funpay.com/image.png")

    def test_invalid_message_is_ignored(self):
        self.assertEqual(
            parse_messages({"node": {"id": 1}, "messages": [{"id": "broken", "html": "x"}]}, 42),
            [],
        )

    def test_lot_category_and_server_price(self):
        html = '<div><div class="offer-list-title-container"><h3><a href="/lots/81/">Sample Game</a></h3></div><a class="tc-item" href="/lots/offer?id=52"><div class="tc-desc-text">Sample seed</div><div class="tc-price" data-s="150.25">190.00 ₽</div><div class="tc-amount">2</div></a></div>'
        rows = parse_lots(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node_id"], 81)
        self.assertEqual(rows[0]["category_title"], "Sample Game")
        self.assertEqual(rows[0]["price"], Decimal("150.25"))

    def test_lot_without_category_is_not_assigned_randomly(self):
        html = '<a class="tc-item" href="/lots/offer?id=5"><div class="tc-desc-text">Sample</div><div class="tc-price">10 ₽</div></a>'
        self.assertEqual(parse_lots(html), [])


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.client = FunPayClient("sample-test-key-123456")
        await self.client.http.aclose()

    async def asyncTearDown(self):
        await self.client.close()

    def mock(self, handler):
        def capture(request):
            self.requests.append(request)
            return handler(request)

        self.client.http = httpx.AsyncClient(transport=httpx.MockTransport(capture), timeout=15)

    async def test_login_redirect(self):
        self.mock(lambda _: httpx.Response(302, headers={"Location": "/account/login"}))
        with self.assertRaises(UnauthorizedException):
            await self.client.sync_profile()

    async def test_challenge(self):
        self.mock(lambda _: httpx.Response(403, text="Cloudflare"))
        with self.assertRaises(ChallengeException):
            await self.client.sync_profile()
        self.assertEqual(len(self.requests), 1)

    async def test_non_funpay_url_is_rejected(self):
        self.mock(lambda _: httpx.Response(200))
        with self.assertRaises(FunPayError):
            await self.client.request("GET", "https://evil.example/")
        self.assertEqual(len(self.requests), 0)

    async def test_runner_browser_encoding_and_tags(self):
        self.client.csrf = "csrf-sample"
        self.mock(
            lambda _: httpx.Response(
                200,
                json={"objects": [{"type": "chat_bookmarks", "id": 42, "tag": "next"}]},
            )
        )
        await self.client.runner([{"type": "chat_bookmarks", "id": 42}])
        request = self.requests[0]
        payload = parse_qs(request.content.decode())
        self.assertIn("application/x-www-form-urlencoded", request.headers["content-type"])
        self.assertEqual(json.loads(payload["objects"][0])[0]["id"], 42)
        self.assertEqual(payload["csrf_token"][0], "csrf-sample")
        self.assertEqual(self.client.tags["chat_bookmarks:42"], "next")

    async def test_safe_requests_retry(self):
        self.mock(lambda _: httpx.Response(503 if len(self.requests) < 3 else 200, text=PROFILE))
        with patch("app.funpay.asyncio.sleep", new=AsyncMock()):
            result = await self.client.sync_profile()
        self.assertEqual(result["funpay_id"], 42)
        self.assertEqual(len(self.requests), 4)
        self.assertTrue(str(self.requests[-1].url).endswith("/users/42/"))

    async def test_rate_limit_honors_retry_after_without_replaying_writes(self):
        self.mock(lambda _: httpx.Response(429, headers={"Retry-After": "9"}))
        with self.assertRaises(FunPayError):
            await self.client.request("POST", "runner/")
        self.assertEqual(len(self.requests), 1)
        from time import monotonic

        self.assertGreater(self.client.next_request_at - monotonic(), 8)

    async def test_profile_avatar_loaded_from_own_user_page(self):
        self.mock(
            lambda request: httpx.Response(
                200,
                text=(
                    '<div class="avatar-photo" style="background-image:url(/files/sample.jpg)"></div>'
                    if str(request.url).endswith("/users/42/")
                    else PROFILE
                ),
            )
        )
        profile = await self.client.sync_profile()
        self.assertEqual(profile["avatar_url"], "https://funpay.com/files/sample.jpg")

    async def test_contact_without_data_user_uses_profile_link(self):
        self.mock(lambda _: httpx.Response(200, text='''<div class="contact-item" data-id="users-7-42"><a href="/users/42/"><span class="contact-item-name">Buyer</span></a><div class="avatar-photo" style="background-image:url(/files/buyer.png)"></div></div>'''))
        chats = await self.client.get_chats()
        self.assertEqual(chats[0]["interlocutor_id"], 42)
        self.assertEqual(chats[0]["interlocutor_avatar"], "https://funpay.com/files/buyer.png")

    async def test_ambiguous_message_is_never_blindly_retried(self):
        self.client.csrf = "sample"
        self.mock(lambda _: httpx.Response(503))
        with self.assertRaises(FunPayError):
            await self.client.send_message("9", "Sample")
        self.assertEqual(len(self.requests), 1)

    async def test_missing_confirmation_fails(self):
        self.client.csrf = "sample"
        self.mock(lambda _: httpx.Response(200, json={"objects": []}))
        with self.assertRaises(FunPayError):
            await self.client.send_message("9", "Sample")

    async def test_empty_confirmation_fails(self):
        self.client.csrf = "sample"
        self.mock(lambda _: httpx.Response(200, json={"objects": [], "response": {}}))
        with self.assertRaises(FunPayError):
            await self.client.send_message("9", "Sample")

    async def test_confirmed_message_payload(self):
        self.client.csrf = "sample"
        self.mock(lambda _: httpx.Response(200, json={"objects": [], "response": {"error": None}}))
        await self.client.send_message("9", "Sample")
        payload = parse_qs(self.requests[0].content.decode())
        action = json.loads(payload["request"][0])
        self.assertEqual(action["action"], "chat_message")
        self.assertEqual(action["data"]["content"], "Sample")

    async def test_raise_cooldown(self):
        self.client.csrf = "sample"
        self.mock(
            lambda _: httpx.Response(
                200, json={"error": 1, "msg": "Поднять через 1 час 12 минут 3 секунды"}
            )
        )
        result = await self.client.raise_lots(1, 2)
        self.assertFalse(result["raised"])
        self.assertEqual(result["cooldown"], 4323)

    async def test_invalid_json(self):
        self.client.csrf = "sample"
        self.mock(lambda _: httpx.Response(200, text="<html>broken</html>"))
        with self.assertRaises(FunPayError):
            await self.client.runner([])

    async def test_poll_correct_subscription_types(self):
        self.client.csrf = "sample"
        self.client.user_id = 42
        self.mock(lambda _: httpx.Response(200, json={"objects": []}))
        await self.client.poll(["7"])
        payload = parse_qs(self.requests[0].content.decode())
        kinds = [obj["type"] for obj in json.loads(payload["objects"][0])]
        self.assertEqual(kinds, ["chat_bookmarks", "orders_counters", "chat_node"])

    async def test_order_details_checks_seller(self):
        self.client.user_id = 42
        self.mock(
            lambda _: httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "data": {"SAMPLE": {"seller": {"user_id": 9}, "status": "paid"}},
                },
            )
        )
        with self.assertRaises(FunPayError):
            await self.client.order_details("SAMPLE")

    async def test_runner_cursor_prevents_history_replay(self):
        self.client.csrf = "sample"
        self.client.user_id = 42
        self.mock(
            lambda _: httpx.Response(
                200,
                json={
                    "objects": [
                        {
                            "type": "chat_node",
                            "id": 7,
                            "tag": "sample-next",
                            "data": {
                                "node": {"id": 7, "name": "users-8-42"},
                                "messages": [{"id": 100}],
                            },
                        }
                    ]
                },
            )
        )
        await self.client.runner([])
        await self.client.poll(["7"])
        payload = parse_qs(self.requests[-1].content.decode())
        chat = next(
            item for item in json.loads(payload["objects"][0]) if item["type"] == "chat_node"
        )
        self.assertEqual(chat["tag"], "sample-next")
        self.assertEqual(chat["data"]["last_message"], 100)
        self.assertEqual(self.client.last_messages["users-8-42"], 100)

    async def test_order_details_returns_confirmed_status(self):
        self.client.user_id = 42
        self.mock(
            lambda _: httpx.Response(
                200,
                json={
                    "status": "SUCCESS",
                    "data": {
                        "SAMPLE": {
                            "seller": {"user_id": 42},
                            "status": "paid",
                            "buyer": {"user_id": 9},
                        }
                    },
                },
            )
        )
        result = await self.client.order_details("SAMPLE")
        self.assertEqual(result["status"], "paid")

    async def test_copy_rejects_hidden_field_injection(self):
        from unittest.mock import patch

        preview = {"node_id": 2, "controls": []}
        with patch(
            "app.copylots.copy_form", new=AsyncMock(return_value=(preview, {"offer_id": "0"}))
        ):
            with self.assertRaises(FunPayError):
                await publish_copy(
                    self.client, 1, "Sample", "", Decimal("10"), 1, {"csrf_token": "injected"}
                )

    async def test_copy_rejects_unknown_select_value(self):
        preview = {
            "node_id": 2,
            "controls": [
                {
                    "name": "fields[server]",
                    "label": "Server",
                    "options": [{"value": "1", "label": "A"}],
                    "required": True,
                }
            ],
        }
        with patch(
            "app.copylots.copy_form", new=AsyncMock(return_value=(preview, {"offer_id": "0"}))
        ):
            with self.assertRaises(FunPayError):
                await publish_copy(
                    self.client, 1, "Sample", "", Decimal("10"), 1, {"fields[server]": "999"}
                )

    async def test_review_reply_does_not_invent_reciprocal_rating(self):
        self.client.csrf = "sample"
        self.client.user_id = 42
        self.mock(lambda _: httpx.Response(200, json={"content": "Review saved"}))
        await self.client.reply_review("SAMPLE", "Thank you")
        payload = parse_qs(self.requests[0].content.decode(), keep_blank_values=True)
        self.assertEqual(payload["rating"], [""])
        self.assertEqual(payload["orderId"], ["SAMPLE"])


if __name__ == "__main__":
    unittest.main()
