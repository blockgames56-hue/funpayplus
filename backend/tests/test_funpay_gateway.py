import asyncio
import json
import threading
import unittest
from datetime import datetime
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from app.funpay import FunPayError
from app.funpay_gateway import FunPayGateway, SealedAccount
from FunPayAPI import enums
from FunPayAPI.common.exceptions import RaiseError


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_executor_retries_only_transient_form_get(self):
        from FunPayAPI.common.exceptions import RequestFailedError
        from test_offer_editor import FORM

        failed = SimpleNamespace(
            status_code=503,
            request=SimpleNamespace(url="https://funpay.com/lots/offerEdit", headers={}, body=""),
        )
        self.gateway.api_account.method = Mock(
            side_effect=[
                RequestFailedError(failed),
                SimpleNamespace(json=lambda: {"html": FORM.format(offer=123)}),
            ]
        )
        with patch("app.funpay_gateway.asyncio.sleep", new_callable=AsyncMock) as sleep:
            html = await self.gateway.lot_form_html("123")
        self.assertIn('name="offer_id"', html)
        self.assertEqual(self.gateway.api_account.method.call_count, 2)
        sleep.assert_awaited_once_with(1.2)

    async def test_legacy_requests_share_the_sdk_seal_and_session(self):
        from app.funpay import FunPayClient

        self.gateway.api_account.golden_seal = "synthetic-seal"
        self.gateway.api_account.phpsessid = "synthetic-session"
        with patch.object(FunPayClient, "request", new_callable=AsyncMock) as request:
            await self.gateway.request("POST", "lots/calc", data={"price": "100"})
        self.assertEqual(self.gateway.http.cookies.get("golden_seal"), "synthetic-seal")
        self.assertEqual(self.gateway.http.cookies.get("PHPSESSID"), "synthetic-session")
        request.assert_awaited_once()

    def test_sdk_redirect_is_not_followed_or_treated_as_form(self):
        from app.funpay import UnauthorizedException

        response = SimpleNamespace(
            status_code=302, cookies={}, headers={"location": "/account/login"}
        )
        with patch("app.funpay_gateway.requests.get", return_value=response) as get:
            with self.assertRaises(UnauthorizedException):
                self.gateway.api_account.method("get", "lots/offerEdit?offer=123", {}, {})
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    async def test_html_editor_supports_edit_hide_activate_and_price(self):
        import requests
        from app.lot_editor import load_form
        from test_offer_editor import FORM

        self.gateway.csrf = "old-token"
        state = {"price": "100", "active": "on"}

        def remote(method, path, headers, data, **kwargs):
            if method == "post":
                state.update(data)
                return SimpleNamespace(json=lambda: {"error": False})
            html = FORM.format(offer=123).replace(
                'name="price" value="100"', f'name="price" value="{state["price"]}"'
            )
            if not state["active"]:
                html = html.replace(
                    'name="active" type="checkbox" checked', 'name="active" type="checkbox"'
                )
            response = requests.Response()
            response.status_code = 200
            response._content = html.encode()
            response.encoding = "utf-8"
            return response

        self.gateway.api_account.method = Mock(side_effect=remote)
        with patch.object(
            type(self.gateway.api_account),
            "is_initiated",
            new_callable=PropertyMock,
            return_value=True,
        ):
            metadata, fields = await load_form(self.gateway, offer_id="123")
            self.assertTrue(metadata["controls"])
            self.assertEqual(fields["fields[summary][ru]"], "Русский текст")
            self.assertEqual(self.gateway.csrf, "fresh-csrf")
            await self.gateway.update_lot("123", active=False, price=Decimal("125.50"))
            self.assertEqual(state["active"], "")
            self.assertEqual(state["price"], "125.50")
            await self.gateway.update_lot("123", active=True)
            self.assertEqual(state["active"], "on")

    async def test_delete_uses_native_session_and_requires_acknowledgement(self):
        from app.lot_editor import save_form

        self.gateway.csrf = "fresh-csrf"
        fields = {"offer_id": "123", "deleted": "1"}
        for result in ({"done": True}, {"error": False}, {}, {"error": "denied"}):
            self.gateway.api_account.method = Mock(
                return_value=SimpleNamespace(json=lambda: result)
            )
            if result in ({"done": True}, {"error": False}):
                await save_form(self.gateway, fields)
            else:
                with self.assertRaises(FunPayError):
                    await save_form(self.gateway, fields)
            self.gateway.api_account.method.assert_called_once()
            args = self.gateway.api_account.method.call_args.args
            self.assertEqual(args[:2], ("post", "lots/offerSave"))
            self.assertEqual(args[3]["csrf_token"], "fresh-csrf")

    def test_session_seal_is_carried_without_leaking_to_foreign_host(self):
        account = SealedAccount("offline-key")
        response = SimpleNamespace(status_code=200, cookies={"golden_seal": "offline-seal"})
        with patch("app.funpay_gateway.requests.get", return_value=response) as get:
            account.method("get", "https://funpay.com", {}, {})
            account.phpsessid = "offline-php"
            account.method("get", "chat/", {}, {})
        self.assertEqual(account.golden_seal, "offline-seal")
        self.assertIn("golden_seal=offline-seal", get.call_args.kwargs["headers"]["cookie"])
        self.assertIn("PHPSESSID=offline-php", get.call_args.kwargs["headers"]["cookie"])
        with self.assertRaises(FunPayError):
            account.method("get", "https://example.com/", {}, {})

    async def test_lot_form_html_retries_transient_failures(self):
        from FunPayAPI.common.exceptions import RequestFailedError

        gateway = FunPayGateway.__new__(FunPayGateway)
        gateway.api_account = SimpleNamespace(method=None)
        gateway.api_lock = asyncio.Lock()
        sleeps = []
        with (
            patch.object(FunPayGateway, "call", new_callable=AsyncMock) as call,
            patch(
                "app.funpay_gateway.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=lambda value: sleeps.append(value),
            ),
        ):
            call.side_effect = RequestFailedError(
                SimpleNamespace(
                    status_code=503,
                    text="",
                    request=SimpleNamespace(url="https://funpay.com/x", headers={}, body=""),
                )
            )
            with self.assertRaisesRegex(FunPayError, "отклонил запрос"):
                await FunPayGateway.lot_form_html(gateway, 123)
            self.assertEqual(call.await_count, 4)
            self.assertEqual(sleeps, [1.2, 3.0, 6.0])
            call.side_effect = None
            form = '<form><input name="fields[summary][ru]"><input name="price"></form>'
            call.return_value = SimpleNamespace(json=lambda: {"html": form})
            html = await FunPayGateway.lot_form_html(gateway, 123)
            self.assertEqual(html, form)

    async def test_native_save_preserves_disabled_and_unknown_fields(self):
        fields = {
            "offer_id": "123",
            "node_id": "17",
            "price": "100.20",
            "amount": "2",
            "active": "",
            "deactivate_after_sale": "on",
            "fields[server]": "abc",
            "fields[payment_msg][en]": "Message",
            "secrets": "synthetic-key",
            "csrf_token": "synthetic-csrf",
        }
        captured = []
        self.gateway.api_account.save_lot = Mock(
            side_effect=lambda payload: captured.append(dict(payload.renew_fields().fields))
        )
        readback = patch("app.lot_editor.load_form", AsyncMock(return_value=({}, dict(fields))))
        readback.start()
        self.addCleanup(readback.stop)
        await self.gateway.save_lot_fields(fields)
        self.assertEqual(captured, [fields])

    async def test_native_save_mismatch_is_unknown_without_retry(self):
        self.gateway.api_account.save_lot = Mock()
        readback = patch("app.lot_editor.load_form", AsyncMock(return_value=({}, {"price": "99"})))
        readback.start()
        self.addCleanup(readback.stop)
        with self.assertRaisesRegex(FunPayError, "UNKNOWN"):
            await self.gateway.save_lot_fields({"offer_id": "123", "price": "100"})
        self.gateway.api_account.save_lot.assert_called_once()

    async def test_real_sdk_save_and_readback_keep_unchecked_active(self):
        fields = {
            "offer_id": "123",
            "node_id": "17",
            "price": "100.20",
            "amount": "2",
            "active": "",
        }
        html = (
            '<form><input name="offer_id" value="123"><input name="node_id" value="17">'
            '<input name="price" value="100.20"><input name="amount" value="2">'
            '<input name="fields[summary][ru]" value="Test"><input name="active" type="checkbox"></form>'
        )
        import requests

        self.gateway.csrf = "synthetic-csrf"
        response = requests.Response()
        response.status_code = 200
        response._content = html.encode()
        self.gateway.api_account.method = Mock(
            side_effect=[
                SimpleNamespace(json=lambda: {"error": False}),
                response,
            ]
        )
        with patch.object(
            type(self.gateway.api_account),
            "is_initiated",
            new_callable=PropertyMock,
            return_value=True,
        ):
            await self.gateway.save_lot_fields(fields)
        calls = self.gateway.api_account.method.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[:2], ("post", "lots/offerSave"))
        self.assertEqual(calls[0].args[3]["active"], "")
        self.assertEqual(calls[0].args[3]["price"], "100.20")
        self.assertEqual(calls[1].args[:2], ("get", "lots/offerEdit?offer=123"))

    async def test_native_form_validates_offer_identity(self):
        from app.lot_editor import load_form

        self.gateway.api_account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {"html": '<form><input name="offer_id" value="999"></form>'}
            )
        )
        with self.assertRaises(FunPayError):
            await load_form(self.gateway, offer_id="123")

    async def test_history_accepts_current_markup_without_sdk_parser_crash(self):
        html = '<div class="chat-msg-item"><a href="/users/2/">Buyer</a><div class="chat-msg-text">Hello</div></div>'
        response = SimpleNamespace(
            json=lambda: {"chat": {"messages": [{"id": 17, "author": 2, "html": html}]}}
        )
        self.gateway.api_account.method = Mock(return_value=response)
        result = await self.gateway.runner([{"type": "chat_node", "id": "users-7-2"}])
        parsed = result["objects"][0]["data"]["normalized_messages"]
        self.assertEqual(parsed[0]["funpay_message_id"], 17)
        self.assertEqual(parsed[0]["text"], "Hello")
        self.gateway.api_account.method.assert_called_once()
        # Refresh must fetch the newest page, rather than an arbitrary old
        # page of a long conversation.
        self.assertIn("last_message=-1", self.gateway.api_account.method.call_args.args[1])

    def test_runner_history_accepts_current_markup(self):
        account = self.gateway.api_account
        account.id = 7
        account.username = "Seller"
        account.csrf_token = "offline-csrf"
        html = (
            '<div class="chat-msg-item"><a href="/users/2/">Buyer</a>'
            '<div class="chat-msg-text">Hello</div></div>'
        )
        account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {
                    "objects": [
                        {"id": 42, "data": {"messages": [{"id": 17, "author": 2, "html": html}]}}
                    ]
                }
            )
        )
        messages = account.get_chats_histories({42: "Buyer"})[42]
        self.assertEqual(len(messages), 1)
        self.assertEqual((messages[0].id, messages[0].chat_id, messages[0].text), (17, 42, "Hello"))
        self.assertEqual(messages[0].author_id, 2)
        self.assertFalse(messages[0].by_bot)
        self.assertEqual(account.method.call_args.args[:2], ("post", "runner/"))

    async def asyncSetUp(self):
        self.gateway = FunPayGateway("offline-not-a-real-key")
        self.gateway.user_id = 7
        self.gateway.refreshed_at = monotonic()

    async def asyncTearDown(self):
        await self.gateway.close()

    async def test_poll_marks_initial_baseline_and_confirmed_runner_message(self):
        message = SimpleNamespace(
            id=10,
            chat_id=42,
            chat_name="Buyer",
            author_id=2,
            author="Buyer",
            text="!help",
            image_link=None,
            html="",
        )
        initial = SimpleNamespace(
            type=enums.EventTypes.INITIAL_CHAT,
            chat=SimpleNamespace(id=42, name="Buyer"),
        )
        runner = Mock()
        runner.parse_updates.return_value = [
            initial,
            SimpleNamespace(type=enums.EventTypes.INITIAL_ORDER),
            SimpleNamespace(type=enums.EventTypes.NEW_MESSAGE, message=message),
        ]
        self.gateway.api_account.get_chats_histories = Mock(return_value={42: [message]})
        self.gateway.api_runner = runner
        result = await self.gateway.poll([])
        chat = result["objects"][0]
        self.assertEqual(chat["data"]["normalized_messages"][0]["funpay_message_id"], 10)
        self.assertEqual(chat["data"]["confirmed_new_ids"], [10])
        self.assertFalse(chat["data"]["initial"])
        self.assertFalse(chat["data"]["reconcile"])
        # Runner already fetched this message; a second history request adds latency.
        self.gateway.api_account.get_chats_histories.assert_not_called()
        runner.get_updates.assert_called_once()
        runner.parse_updates.assert_called_once()

    async def test_poll_rotates_known_cursors_and_reconciles_history(self):
        runner = Mock()
        runner.parse_updates.return_value = []
        message = SimpleNamespace(
            id=12,
            chat_id=42,
            chat_name=None,
            author_id=2,
            author="Buyer",
            text="hello",
            image_link=None,
            html="",
        )
        self.gateway.api_account.get_chats_histories = Mock(return_value={42: [message]})
        self.gateway.api_runner = runner
        result = await self.gateway.poll(["42"], {"42": 10})
        self.gateway.api_account.get_chats_histories.assert_called_once_with({42: None}, {42: 10})
        chat = result["objects"][0]
        self.assertTrue(chat["data"]["reconcile"])
        self.assertFalse(chat["data"]["initial"])
        self.assertEqual(chat["data"]["confirmed_new_ids"], [])

    async def test_profile_shares_session_with_remaining_lot_adapter(self):
        account = self.gateway.api_account
        account.id = 7
        account.phpsessid = "offline-session"
        account.html = """<body data-app-data='{"userId":7,"csrf-token":"offline-csrf"}'>
        <a class="user-link" data-id="7"><span class="user-link-name">Seller</span></a></body>"""
        account.get = Mock(return_value=account)
        with patch("app.funpay_gateway.Runner") as runner:
            result = await self.gateway.sync_profile()
            runner.assert_called_once_with(
                account, disable_message_requests=False, disabled_order_requests=False
            )
        self.assertEqual(result["funpay_id"], 7)
        self.assertEqual(self.gateway.http.cookies.get("PHPSESSID"), "offline-session")
        self.assertEqual(self.gateway.csrf, "offline-csrf")

    async def test_image_send_uses_native_image_parameter(self):
        html = (
            '<div class="chat-msg-item"><a href="/users/7/">Seller</a>'
            '<a class="chat-img-link" href="/image/test">Image</a></div>'
        )
        self.gateway.api_account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {
                    "response": {"ok": True},
                    "objects": [{"data": {"messages": [{"id": 20, "author": 7, "html": html}]}}],
                }
            )
        )
        result = await self.gateway.send_message("42", "[img:123]")
        call = self.gateway.api_account.method.call_args
        self.assertEqual(call.args[:2], ("post", "runner/"))
        self.assertEqual(json.loads(call.args[3]["request"])["data"]["image_id"], 123)
        self.assertEqual(result["message_id"], 20)

    async def test_text_send_accepts_current_markup_and_confirms_exact_content(self):
        html = (
            '<div class="chat-msg-item"><a href="/users/7/">Seller</a>'
            '<div class="chat-msg-text">\u2064hello</div></div>'
        )
        self.gateway.api_account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {
                    "response": {"ok": True},
                    "objects": [{"data": {"messages": [{"id": 21, "author": 7, "html": html}]}}],
                }
            )
        )
        result = await self.gateway.send_message("42", "hello")
        call = self.gateway.api_account.method.call_args
        self.assertEqual(json.loads(call.args[3]["request"])["data"]["content"], "\u2064hello")
        self.assertEqual(result["message_id"], 21)
        self.assertEqual(result["objects"][0]["data"]["normalized_messages"][0]["text"], "hello")

    async def test_send_requires_remote_message_id(self):
        self.gateway.api_account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {
                    "response": {"ok": True},
                    "objects": [
                        {
                            "data": {
                                "messages": [
                                    {
                                        "author": 7,
                                        "html": '<div class="chat-msg-text">\u2064hello</div>',
                                    }
                                ]
                            }
                        }
                    ],
                }
            )
        )
        with self.assertRaisesRegex(FunPayError, "UNKNOWN"):
            await self.gateway.send_message("42", "hello")
        self.gateway.api_account.method.assert_called_once()

    async def test_send_rejects_mismatched_remote_text_without_retry(self):
        html = (
            '<div class="chat-msg-item"><a href="/users/7/">Seller</a>'
            '<div class="chat-msg-text">\u2064different</div></div>'
        )
        self.gateway.api_account.method = Mock(
            return_value=SimpleNamespace(
                json=lambda: {
                    "response": {"ok": True},
                    "objects": [{"data": {"messages": [{"id": 22, "author": 7, "html": html}]}}],
                }
            )
        )
        with self.assertRaisesRegex(FunPayError, "UNKNOWN"):
            await self.gateway.send_message("42", "hello")
        self.gateway.api_account.method.assert_called_once()

    async def test_sync_requests_are_serial_and_off_event_loop(self):
        loop_thread = threading.get_ident()
        active = 0
        highest = 0

        def work():
            nonlocal active, highest
            active += 1
            highest = max(highest, active)
            thread = threading.get_ident()
            active -= 1
            return thread

        ids = await asyncio.gather(*(self.gateway.call(work) for _ in range(5)))
        self.assertEqual(highest, 1)
        self.assertTrue(all(ident != loop_thread for ident in ids))
        self.assertEqual(len(set(ids)), 1)

    def order(self, **changes):
        values = dict(
            id="ABC123",
            description="Product",
            price=12.35,
            amount=2,
            buyer_id=2,
            buyer_username="Buyer",
            seller_id=7,
            status=enums.OrderStatuses.CLOSED,
            date=datetime(2026, 11, 19, 12),
            html='<div class="tc-price">12.35 ₽</div>',
            review=SimpleNamespace(stars=4, text="Good", reply=None),
        )
        return SimpleNamespace(**(values | changes))

    async def test_native_lots_map_ids_stock_and_hidden_state(self):
        category = SimpleNamespace(
            id=17,
            name="Items",
            type=enums.SubCategoryTypes.COMMON,
            category=SimpleNamespace(id=4, name="Game"),
        )
        lot = SimpleNamespace(
            id=123,
            subcategory=category,
            description="Item",
            price=12.35,
            html='<a class="tc-item warning"><div class="tc-amount">1 234</div></a>',
        )
        profile = Mock()
        profile.get_lots.return_value = [lot]
        self.gateway.api_account.get_user = Mock(return_value=profile)
        rows = await self.gateway.get_lots()
        self.gateway.api_account.get_user.assert_called_once_with(7)
        self.assertEqual(rows[0]["amount"], 1234)
        self.assertEqual(rows[0]["game_id"], 4)
        self.assertEqual(rows[0]["node_id"], 17)
        self.assertEqual(rows[0]["price"], Decimal("12.35"))
        self.assertFalse(rows[0]["is_active"])
        lot.html = '<a class="tc-item"></a>'
        self.assertIsNone((await self.gateway.get_lots())[0]["amount"])
        category.type = enums.SubCategoryTypes.CURRENCY
        self.assertEqual(await self.gateway.get_lots(), [])

    async def test_orders_preserve_money_currency_date_and_quantity(self):
        self.gateway.api_account.get_sells = Mock(return_value=(None, [self.order()]))
        self.gateway.api_account.get_order = Mock(return_value=self.order())
        rows = await self.gateway.get_orders()
        self.assertEqual(rows[0]["sum"], Decimal("12.35"))
        self.assertEqual(rows[0]["currency"], "RUB")
        self.assertEqual(rows[0]["status"], "closed")
        self.assertIsNotNone(rows[0]["created_at"].tzinfo)
        self.assertEqual((await self.gateway.order_details("ABC123"))["quantity"], 2)

    async def test_orders_reject_unknown_currency(self):
        self.gateway.api_account.get_sells = Mock(return_value=(None, [self.order(html="")]))
        with self.assertRaisesRegex(FunPayError, "валюта"):
            await self.gateway.get_orders()

    async def test_orders_stop_repeated_cursor_and_deduplicate(self):
        self.gateway.api_account.get_sells = Mock(return_value=("cursor", [self.order()]))
        rows = await self.gateway.get_orders()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.gateway.api_account.get_sells.call_count, 2)

    async def test_orders_respect_page_bound(self):
        self.gateway.api_account.get_sells = Mock(return_value=("cursor", [self.order()]))
        await self.gateway.get_orders(max_pages=1)
        self.gateway.api_account.get_sells.assert_called_once_with(start_from=None)

    async def test_review_guards_prevent_writes(self):
        self.gateway.api_account.send_review = Mock()
        for order in (
            self.order(seller_id=8),
            self.order(status=enums.OrderStatuses.PAID),
            self.order(review=None),
            self.order(review=SimpleNamespace(stars=5, text="Fine", reply="Already replied")),
        ):
            self.gateway.api_account.get_order = Mock(return_value=order)
            with self.assertRaises(FunPayError):
                await self.gateway.reply_review("ABC123", "Thanks")
        self.gateway.api_account.send_review.assert_not_called()

    async def test_review_reply_preserves_rating_and_verifies_remote(self):
        confirmed = self.order(review=SimpleNamespace(stars=4, text="Good", reply="Thanks"))
        self.gateway.api_account.get_order = Mock(side_effect=[self.order(), confirmed])
        self.gateway.api_account.send_review = Mock(return_value="<div>review</div>")
        await self.gateway.reply_review("ABC123", "Thanks")
        self.gateway.api_account.send_review.assert_called_once_with("ABC123", "Thanks", rating=4)

    async def test_unconfirmed_review_is_unknown_without_resend(self):
        self.gateway.api_account.get_order = Mock(return_value=self.order())
        self.gateway.api_account.send_review = Mock(return_value="<div>review</div>")
        with self.assertRaisesRegex(FunPayError, "UNKNOWN"):
            await self.gateway.reply_review("ABC123", "Thanks")
        self.assertEqual(self.gateway.api_account.send_review.call_count, 1)

    def raise_category(self):
        category = SimpleNamespace(id=4, name="Game")
        self.gateway.api_account.get_subcategory = Mock(
            return_value=SimpleNamespace(category=category)
        )
        self.gateway.api_account.raise_lots = Mock(return_value=True)
        return category

    async def test_raise_targets_only_selected_subcategory(self):
        self.raise_category()
        result = await self.gateway.raise_lots(17, 4)
        self.assertTrue(result["raised"])
        self.gateway.api_account.raise_lots.assert_called_once_with(4, subcategories=[17])

    async def test_raise_dry_run_does_not_mutate_remote(self):
        self.raise_category()
        result = await self.gateway.raise_lots(17, 4, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["raised"])
        self.gateway.api_account.raise_lots.assert_not_called()

    async def test_raise_rejects_mismatched_game(self):
        self.raise_category()
        with self.assertRaisesRegex(FunPayError, "не совпадает"):
            await self.gateway.raise_lots(17, 99)
        self.gateway.api_account.raise_lots.assert_not_called()

    async def test_raise_honors_full_server_cooldown_without_retry(self):
        category = self.raise_category()
        response = SimpleNamespace(
            status_code=429,
            request=SimpleNamespace(url="https://funpay.com/lots/raise", headers={}, body=None),
        )
        self.gateway.api_account.raise_lots.side_effect = RaiseError(
            response, category, "wait", 90000
        )
        result = await self.gateway.raise_lots(17, 4)
        self.assertEqual(result["cooldown"], 90000)
        self.assertFalse(result["raised"])
        self.assertEqual(self.gateway.api_account.raise_lots.call_count, 1)
