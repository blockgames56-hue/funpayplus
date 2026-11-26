"""FunPayAPI adapter for the migrated profile/chat/Runner path.

Legacy lot editing stays in the existing gateway until its contract is migrated.
One serialized executor owns each synchronous Account and Runner instance.
"""

import asyncio
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from time import monotonic
from urllib.parse import quote, urlparse

import requests
from FunPayAPI import Account, Runner, enums, types
from FunPayAPI.common.exceptions import RaiseError, RequestFailedError, UnauthorizedError
from FunPayAPI.types import LotFields

from .chat_dates import UNKNOWN_DATE, message_date, order_date
from .funpay import (
    USER_AGENT,
    FunPayClient,
    FunPayError,
    ResponseFormatError,
    UnauthorizedException,
    parse_messages,
    parse_profile,
    soup_of,
)

# The library's DEBUG output includes raw updates and private chat contents.
logging.getLogger("FunPayAPI").setLevel(logging.WARNING)


def normalize_message(message, seller_id):
    if not message.id or not message.chat_id:
        raise FunPayError("FunPayAPI вернул сообщение без ID")
    return {
        "node_id": str(message.chat_id),
        "funpay_message_id": int(message.id),
        "sender_id": int(message.author_id or 0),
        "sender_name": message.author or "",
        "text": message.text or "",
        "is_my": message.author_id == seller_id,
        "has_image": bool(message.image_link),
        "image_url": message.image_link,
        # v1.1.0 Message has no documented timestamp. Never invent one.
        "created_at": message_date({}, soup_of(getattr(message, "html", ""))) or UNKNOWN_DATE,
    }


def message_objects(messages, seller_id):
    return (
        [
            {
                "type": "chat_node",
                "data": {
                    "normalized_messages": [
                        normalize_message(message, seller_id) for message in messages
                    ]
                },
            }
        ]
        if messages
        else []
    )


class ExactLotFields(LotFields):
    """Keep validated form values, including unchecked and category-specific fields."""

    def renew_fields(self):
        return self


class GatewayHTTPError(FunPayError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"FunPayAPI вернул HTTP {status_code}")


class SealedAccount(Account):
    """FunPay now requires the homepage's golden_seal alongside golden_key."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.golden_seal = None

    def method(
        self,
        request_method,
        api_method,
        headers,
        payload,
        exclude_phpsessid=False,
        raise_not_200=False,
    ):
        link = (
            api_method
            if api_method.startswith(("https://", "http://"))
            else "https://funpay.com/" + api_method
        )
        parsed = urlparse(link)
        if parsed.scheme != "https" or parsed.hostname != "funpay.com":
            raise FunPayError("Запрещён внешний адрес FunPayAPI")
        safe_headers = dict(headers)
        cookies = [f"golden_key={self.golden_key}"]
        if self.golden_seal:
            cookies.append(f"golden_seal={self.golden_seal}")
        if self.phpsessid and not exclude_phpsessid:
            cookies.append(f"PHPSESSID={self.phpsessid}")
        safe_headers["cookie"] = "; ".join(cookies)
        if self.user_agent:
            safe_headers["user-agent"] = self.user_agent
        response = getattr(requests, request_method)(
            link,
            headers=safe_headers,
            data=payload,
            timeout=self.requests_timeout,
            proxies=self.proxy or {},
            allow_redirects=False,
        )
        if response.cookies.get("golden_seal"):
            self.golden_seal = response.cookies["golden_seal"]
        if response.status_code == 403:
            raise UnauthorizedError(response)
        if response.status_code in {301, 302, 303, 307, 308}:
            if "login" in response.headers.get("location", "").lower():
                raise UnauthorizedException("Сессия FunPay истекла")
            raise ResponseFormatError("FunPay вернул неожиданный редирект")
        if response.status_code != 200 and raise_not_200:
            raise RequestFailedError(response)
        return response

    def get_chats_histories(self, chats_data, last_messages=None):
        """Keep Runner's batching and durable per-chat message cursors."""
        last_messages = last_messages or {}
        objects = [
            {
                "type": "chat_node",
                "id": node,
                "tag": "00000000",
                "data": {"node": node, "last_message": last_messages.get(node, -1), "content": ""},
            }
            for node in chats_data
        ]
        response = self.method(
            "post",
            "runner/",
            {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            {"objects": json.dumps(objects), "request": False, "csrf_token": self.csrf_token},
            raise_not_200=True,
        )
        result = {}
        for obj in response.json()["objects"]:
            node = obj.get("id")
            rows = (obj.get("data") or {}).get("messages") or []
            parsed = parse_messages({"node": {"id": node}, "messages": rows}, self.id)
            messages = []
            html_by_id = {
                int(raw["id"]): raw["html"] for raw in rows if str(raw.get("id", "")).isdigit()
            }
            for item in parsed:
                # parse_messages already validates IDs and extracts author/text/image.
                message = types.Message(
                    item["funpay_message_id"],
                    item["text"].removeprefix(self.bot_character)
                    if not item["has_image"]
                    else None,
                    node,
                    chats_data.get(node),
                    item["sender_name"],
                    item["sender_id"],
                    html_by_id[item["funpay_message_id"]],
                    item["image_url"],
                    determine_msg_type=False,
                )
                message.by_bot = bool(item["text"].startswith(self.bot_character))
                message.type = (
                    types.MessageTypes.NON_SYSTEM
                    if item["sender_id"]
                    else message.get_message_type()
                )
                messages.append(message)
            result[node] = messages
        return result


class FunPayGateway(FunPayClient):
    native_runner = True
    native_lot_forms = True

    def __init__(self, golden_key, user_agent=USER_AGENT, proxy=None, transport="httpx"):
        super().__init__(golden_key, user_agent, proxy, transport)
        self.api_account = SealedAccount(
            golden_key,
            user_agent,
            requests_timeout=15,
            proxy={"http": proxy, "https": proxy} if proxy else None,
        )
        self.api_runner = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="funpay-api")
        self.api_lock = asyncio.Lock()
        self.refreshed_at = 0.0
        self.order_quantities = {}

    async def call(self, function, *args, **kwargs):
        async with self.api_lock:
            future = asyncio.get_running_loop().run_in_executor(
                self.executor, lambda: function(*args, **kwargs)
            )
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # A cancelled asyncio task cannot cancel an active HTTP request.
                # Drain it before releasing ownership or closing the gateway.
                try:
                    await asyncio.shield(future)
                except Exception:
                    logging.getLogger(__name__).warning("FunPay request ended during shutdown")
                raise
            except UnauthorizedError:
                raise UnauthorizedException("FunPayAPI: сессия не авторизована") from None
            except RequestFailedError as error:
                if error.status_code >= 400:
                    raise GatewayHTTPError(error.status_code) from None
                raise FunPayError("FunPayAPI отклонил операцию; проверьте её результат") from None
            except FunPayError:
                raise
            except Exception as error:
                raise FunPayError(
                    f"FunPayAPI: {type(error).__name__}; проверьте результат операции"
                ) from None

    async def sync_profile(self):
        await self.call(self.api_account.get, update_phpsessid=True)
        self.refreshed_at = monotonic()
        profile = parse_profile(self.api_account.html)
        self.csrf = profile.pop("csrf")
        self.user_id = self.api_account.id
        if self.api_account.phpsessid:
            # Transitional lot operations must use the same session as the
            # CSRF token obtained from Account.get(), not a second PHP session.
            self.http.cookies.set("PHPSESSID", self.api_account.phpsessid, domain="funpay.com")
        if self.api_account.golden_seal:
            self.http.cookies.set("golden_seal", self.api_account.golden_seal, domain="funpay.com")
        if self.api_runner is None:
            self.api_runner = Runner(
                self.api_account, disable_message_requests=False, disabled_order_requests=False
            )
        return profile

    async def request(self, method, path, *, safe=False, **kwargs):
        # New offers, payment quotes and legacy adapters share the SDK's session.
        for name, value in (
            ("PHPSESSID", self.api_account.phpsessid),
            ("golden_seal", self.api_account.golden_seal),
        ):
            if value:
                self.http.cookies.set(name, value, domain="funpay.com")
        return await super().request(method, path, safe=safe, **kwargs)

    async def poll(self, chat_ids, cursors=None):
        if monotonic() - self.refreshed_at > 2400:
            await self.sync_profile()

        def cycle():
            return self.api_runner.parse_updates(self.api_runner.get_updates())

        events = await self.call(cycle)
        initial_ids = set()
        chat_names = {}
        confirmed_ids: dict[str, set[int]] = {}
        confirmed_rows = {}
        for event in events:
            if event.type == enums.EventTypes.INITIAL_CHAT:
                initial_ids.add(str(event.chat.id))
                chat_names[event.chat.id] = event.chat.name
            elif event.type == enums.EventTypes.LAST_CHAT_MESSAGE_CHANGED:
                chat_names[event.chat.id] = event.chat.name
            elif event.type == enums.EventTypes.NEW_MESSAGE:
                node = str(event.message.chat_id)
                ident = int(event.message.id)
                confirmed_ids.setdefault(node, set()).add(ident)
                confirmed_rows.setdefault(node, []).append(
                    normalize_message(event.message, self.user_id)
                )
                chat_names.setdefault(event.message.chat_id, event.message.chat_name)

        cursor_values = {str(key): value for key, value in (cursors or {}).items()}
        names = dict(chat_names)
        # The SDK already fetched NEW_MESSAGE histories while parsing Runner.
        # Deliver these messages now; rotate idle histories on the next cycle.
        reconcile_ids = [] if confirmed_rows else list(chat_ids)[:10]
        for raw_id in reconcile_ids:
            node = int(raw_id) if str(raw_id).isdigit() else str(raw_id)
            names.setdefault(node, None)
        # Runner changes are processed in bounded batches. Independently rotate
        # through ten known chats per cycle so a lost Runner update is reconciled.
        initial_nodes = {str(node) for node in initial_ids}
        reconciliation_nodes = {
            str(node) for node in reconcile_ids if str(node) not in initial_nodes
        }
        for node in reconcile_ids:
            key = int(node) if str(node).isdigit() else str(node)
            names.setdefault(key, None)
        objects = [
            {
                "type": "chat_node",
                "id": node,
                "data": {
                    "normalized_messages": rows,
                    "confirmed_new_ids": sorted(confirmed_ids[node]),
                    "initial": False,
                    "reconcile": False,
                    "history_checked": True,
                },
            }
            for node, rows in confirmed_rows.items()
        ]
        nodes = [node for node in names if str(node) not in confirmed_rows]
        for offset in range(0, len(nodes), 10):
            selected_ids = nodes[offset : offset + 10]
            selected_names = {node: names[node] for node in selected_ids}
            selected_cursors = {node: cursor_values.get(str(node), -1) for node in selected_ids}
            histories = await self.call(
                self.api_account.get_chats_histories,
                selected_names,
                selected_cursors,
            )
            by_node = {str(key): value for key, value in histories.items()}
            for raw_node in selected_ids:
                node = str(raw_node)
                if node not in by_node:
                    continue
                normalized = [normalize_message(message, self.user_id) for message in by_node[node]]
                objects.append(
                    {
                        "type": "chat_node",
                        "id": node,
                        "data": {
                            "normalized_messages": normalized,
                            "confirmed_new_ids": sorted(confirmed_ids.get(node, set())),
                            "initial": node in initial_nodes,
                            "reconcile": node in reconciliation_nodes,
                            "history_checked": True,
                        },
                    }
                )
        if any(
            e.type in {enums.EventTypes.INITIAL_CHAT, enums.EventTypes.CHATS_LIST_CHANGED}
            for e in events
        ):
            objects.append({"type": "chat_bookmarks"})
        if any(
            e.type
            in {
                enums.EventTypes.NEW_ORDER,
                enums.EventTypes.ORDER_STATUS_CHANGED,
                enums.EventTypes.INITIAL_ORDER,
            }
            for e in events
        ):
            objects.append({"type": "orders_counters"})
        return {"objects": objects}

    async def get_chats(self):
        chats = await self.call(self.api_account.get_chats, update=True)
        return [
            {
                "funpay_chat_id": str(chat.id),
                "interlocutor_name": chat.name,
                "interlocutor_id": 0,
                "interlocutor_avatar": None,
                "last_message_text": chat.last_message_text,
                "unread_count": int(chat.unread),
            }
            for chat in chats.values()
        ]

    async def runner(self, objects, action=None):
        if action is not None or any(item.get("type") != "chat_node" for item in objects):
            raise FunPayError("Неподдерживаемая низкоуровневая операция; используйте FunPayAPI")
        messages = []
        for item in objects:
            node = str(item["id"])
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", node):
                raise FunPayError("Некорректный ID чата")
            # SDK 1.1.0 assumes every message has .message-text and crashes on
            # current FunPay markup. Keep its authenticated transport, parse the
            # returned JSON with the tolerant local history parser.
            response = await self.call(
                self.api_account.method,
                "get",
                # `-1` is FunPay's cursor for the newest page.  A synthetic
                # huge cursor asks the server for an old page in long chats,
                # which made an opened conversation appear frozen in time.
                f"chat/history?node={quote(node)}&last_message=-1",
                {"accept": "*/*", "x-requested-with": "XMLHttpRequest"},
                {},
                raise_not_200=True,
            )
            try:
                chat = response.json().get("chat") or {}
            except ValueError:
                raise FunPayError("FunPayAPI не вернул историю чата") from None
            if chat.get("messages"):
                messages.extend(
                    parse_messages(
                        {"node": {"id": node}, "messages": chat["messages"]}, self.user_id
                    )
                )
        return {
            "objects": [{"type": "chat_node", "data": {"normalized_messages": messages}}]
            if messages
            else []
        }

    async def send_message(self, node, text, last_message=-1):
        image = re.fullmatch(r"\[img:(\d+)\]", text)
        request = {
            "action": "chat_message",
            "data": {
                "node": node,
                "last_message": -1,
                "content": "" if image else f"{self.api_account.bot_character}{text}",
            },
        }
        if image:
            request["data"]["image_id"] = int(image[1])
        objects = [
            {
                "type": "chat_node",
                "id": node,
                "tag": "00000000",
                "data": {"node": node, "last_message": -1, "content": ""},
            }
        ]
        response = await self.call(
            self.api_account.method,
            "post",
            "runner/",
            {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            {
                "objects": json.dumps(objects),
                "request": json.dumps(request),
                "csrf_token": self.api_account.csrf_token,
            },
            raise_not_200=True,
        )
        try:
            result = response.json()
            reply = result.get("response")
            raw = result["objects"][0]["data"]["messages"][-1]
            if not isinstance(reply, dict) or not reply or reply.get("error") is not None:
                raise ValueError("unconfirmed response")
            parsed = parse_messages({"node": {"id": str(node)}, "messages": [raw]}, self.user_id)
            message = parsed[0]
            if not message["is_my"] or not message["funpay_message_id"]:
                raise ValueError("unconfirmed author or ID")
            if not image:
                actual = message["text"].removeprefix(self.api_account.bot_character)
                if actual != text:
                    raise ValueError("unconfirmed text")
                message["text"] = actual
            elif not message["has_image"]:
                raise ValueError("unconfirmed image")
        except (KeyError, IndexError, TypeError, ValueError):
            raise FunPayError("UNKNOWN: FunPayAPI не подтвердил отправленное сообщение") from None
        return {
            "message_id": message["funpay_message_id"],
            "objects": [{"type": "chat_node", "data": {"normalized_messages": [message]}}],
        }

    async def upload_image(self, content, filename, content_type):
        with io.BytesIO(content) as stream:
            ident = await self.call(self.api_account.upload_image, stream)
        if not isinstance(ident, int) or ident <= 0:
            raise FunPayError("FunPayAPI не подтвердил загрузку изображения")
        return str(ident)

    async def get_orders(self, *, max_pages=3):
        """Bounded paged history; unknown fields are not filled with guesses."""
        cursor, cursors, found = None, set(), {}
        for _ in range(max(1, min(max_pages, 10))):
            cursor, rows = await self.call(self.api_account.get_sells, start_from=cursor)
            for order in rows:
                price = soup_of(order.html).select_one(".tc-price")
                raw = price.get_text(" ", strip=True) if price else ""
                unit = next(
                    (
                        name
                        for name, markers in (
                            ("RUB", ("₽", "руб", "RUB")),
                            ("USD", ("$", "USD")),
                            ("EUR", ("€", "EUR")),
                        )
                        if any(marker.casefold() in raw.casefold() for marker in markers)
                    ),
                    None,
                )
                if unit is None:
                    raise FunPayError("FunPayAPI: валюта заказа не определена; сумма не сохранена")
                status = order.status.name.lower()
                if status not in {"paid", "closed", "refunded"}:
                    raise FunPayError("FunPayAPI: неизвестный статус заказа")
                created = order_date(order.date, soup_of(order.html))
                found[order.id] = {
                    "funpay_order_id": order.id,
                    "description": order.description,
                    "sum": Decimal(str(order.price)),
                    "currency": unit,
                    "buyer_id": order.buyer_id,
                    "buyer_name": order.buyer_username,
                    "status": status,
                    "created_at": created,
                }
                if order.amount is not None:
                    self.order_quantities[order.id] = order.amount
            if not cursor or cursor in cursors:
                break
            cursors.add(cursor)
            if len(cursors) < max(1, min(max_pages, 10)):
                await asyncio.sleep(0.35)
        return list(found.values())

    async def get_lots(self, user_id=None):
        profile = await self.call(self.api_account.get_user, user_id or self.user_id)
        rows = []
        for lot in profile.get_lots():
            category = lot.subcategory
            if category.type is not enums.SubCategoryTypes.COMMON:
                continue  # Currency offers have a different editor and ID namespace.
            soup = soup_of(lot.html)
            amount_node = soup.select_one(".tc-amount")
            amount_text = re.sub(r"\s+", "", amount_node.get_text()) if amount_node else ""
            amount = int(amount_text) if re.fullmatch(r"\d+", amount_text) else None
            widget = soup.select_one(".tc-item")
            if widget is None:
                raise FunPayError("FunPayAPI: не удалось определить состояние лота")
            rows.append(
                {
                    "funpay_lot_id": str(lot.id),
                    "node_id": category.id,
                    "game_id": category.category.id,
                    "category_title": f"{category.category.name} / {category.name}",
                    "title": lot.description or "",
                    "price": Decimal(str(lot.price)),
                    "amount": amount,
                    "is_active": not bool(
                        {"offer-disabled", "warning"}.intersection(widget.get("class", []))
                    ),
                }
            )
        return rows

    async def lot_form_html(self, offer_id):
        link = f"lots/offerEdit?offer={int(offer_id)}"
        headers = {"accept": "*/*", "x-requested-with": "XMLHttpRequest"}
        delays = (0.0, 1.2, 3.0, 6.0)
        last_error: Exception | None = None
        for attempt, pause in enumerate(delays):
            if pause:
                await asyncio.sleep(pause)
            try:
                response = await self.call(
                    self.api_account.method,
                    "get",
                    link,
                    headers,
                    {},
                    raise_not_200=True,
                )
                try:
                    payload = response.json()
                except ValueError:
                    # Current FunPay serves the editor as HTML, including for AJAX.
                    html = response.text
                else:
                    html = payload.get("html") if isinstance(payload, dict) else None
            except (RequestFailedError, GatewayHTTPError) as error:
                if error.status_code not in {429, 500, 502, 503, 504}:
                    raise FunPayError(
                        "FunPay не разрешил открыть этот лот; проверьте его наличие и владельца"
                    ) from None
                last_error = error
                continue
            except UnauthorizedError:
                raise
            except (ValueError, TypeError):
                last_error = FunPayError("FunPayAPI не вернул форму лота")
                continue
            if isinstance(html, str) and html.strip():
                # Do not hand a login/challenge page to the editor or retry it.
                from .copylots import editor_form

                editor_form(soup_of(html))
                return html
            last_error = FunPayError("FunPayAPI не вернул форму лота")
        if isinstance(last_error, (RequestFailedError, GatewayHTTPError)):
            raise FunPayError(
                "FunPay временно отклонил запрос формы лота "
                "(проверьте, не заблокировал ли FunPay доступ)"
            ) from last_error
        raise last_error or FunPayError("FunPayAPI не вернул форму лота")

    async def save_lot_fields(self, fields):
        from .lot_editor import load_form

        ident = int(fields["offer_id"])
        if ident <= 0:
            raise FunPayError("Этот метод сохраняет только существующие лоты")
        if fields.get("deleted"):
            response = await self.call(
                self.api_account.method,
                "post",
                "lots/offerSave",
                {
                    "accept": "application/json",
                    "x-requested-with": "XMLHttpRequest",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
                {**fields, "location": "trade", "csrf_token": self.csrf},
                raise_not_200=True,
            )
            result = self.json_response(response)
            if result.get("error") or result.get("errors"):
                raise FunPayError("FunPay отклонил удаление лота")
            if result.get("done") is not True and not (
                "error" in result and type(result["error"]) in {bool, int} and result["error"] == 0
            ):
                raise ResponseFormatError(
                    "FunPay не подтвердил удаление. Обновите список лотов перед повтором."
                )
            return
        payload = ExactLotFields(ident, dict(fields))
        await self.call(self.api_account.save_lot, payload)
        _, confirmed = await load_form(self, offer_id=str(ident))
        ignored = {"csrf_token", "query", "location", "price_buyer", "buyer_price"}
        for name, expected in fields.items():
            if name in ignored:
                continue
            actual = str(confirmed.get(name) or "").replace("\r\n", "\n")
            expected = str(expected or "").replace("\r\n", "\n")
            if name in {"price", "amount"} and actual and expected:
                try:
                    if Decimal(actual) == Decimal(expected):
                        continue
                except ArithmeticError:
                    pass
            if actual != expected:
                raise FunPayError("UNKNOWN: сохранение лота требует сверки с FunPay")

    async def order_details(self, order_id):
        order = await self.call(self.api_account.get_order, order_id)
        if order.seller_id != self.user_id:
            raise FunPayError("Заказ не принадлежит продавцу")
        status = order.status.name.lower()
        if status not in {"paid", "closed", "refunded"}:
            raise FunPayError("FunPayAPI: неизвестный статус заказа")
        review = order.review
        return {
            "status": status,
            "buyer": {"user_id": order.buyer_id},
            "seller": {"user_id": order.seller_id},
            "quantity": self.order_quantities.get(order_id),
            "review": {
                "rating": review.stars,
                "text": review.text or "",
                "reply": review.reply or "",
            }
            if review
            else {},
        }

    async def reply_review(self, order_id, text):
        if not text.strip():
            raise FunPayError("Ответ на отзыв не может быть пустым")
        detail = await self.order_details(order_id)
        review = detail["review"]
        if detail["status"] != "closed" or review.get("rating") not in {1, 2, 3, 4, 5}:
            raise FunPayError("Ответ доступен только на существующий отзыв закрытого заказа")
        if review.get("reply"):
            raise FunPayError("Ответ продавца уже опубликован; автоматическая замена запрещена")
        # Account.send_review supports both buyer reviews and seller replies.
        # Seller ownership was verified above. Preserve the buyer's rating.
        result = await self.call(
            self.api_account.send_review, order_id, text, rating=review["rating"]
        )
        if not result:
            raise FunPayError("UNKNOWN: FunPayAPI не подтвердил ответ на отзыв")
        confirmed = await self.order_details(order_id)
        if confirmed["review"].get("reply", "").strip() != text.strip():
            raise FunPayError("UNKNOWN: ответ на отзыв требует сверки на FunPay")

    async def raise_lots(self, node_id, game_id, *, dry_run=False):
        def raise_selected():
            category = self.api_account.get_subcategory(enums.SubCategoryTypes.COMMON, node_id)
            if category is None or (game_id and category.category.id != game_id):
                raise FunPayError("Категория поднятия не совпадает с лотом")
            if dry_run:
                return {
                    "raised": False,
                    "dry_run": True,
                    "cooldown": 0,
                    "message": "Dry-run: категория проверена, поднятие не выполнялось",
                }
            try:
                result = self.api_account.raise_lots(category.category.id, subcategories=[node_id])
            except RaiseError as error:
                if error.wait_time is None or error.wait_time <= 0:
                    raise
                return {
                    "raised": False,
                    "cooldown": int(error.wait_time),
                    "message": "Поднятие отложено до окончания лимита FunPay",
                }
            if result is not True:
                raise FunPayError("FunPayAPI не подтвердил поднятие")
            return {"raised": True, "cooldown": 0, "message": "Категория поднята"}

        return await self.call(raise_selected)

    async def close(self):
        async with self.api_lock:
            self.executor.shutdown(wait=False, cancel_futures=True)
        await super().close()
