from __future__ import annotations

import asyncio
import json
import random
import re
from decimal import Decimal, InvalidOperation
from time import monotonic
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .avatar_urls import normalize_avatar_url
from .chat_dates import UNKNOWN_DATE, message_date

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
BASE = "https://funpay.com/"


class FunPayError(Exception):
    """Remote operation failed or its outcome cannot be confirmed."""


class UnauthorizedException(FunPayError):
    """Session is expired or the golden key is invalid."""


class ChallengeException(FunPayError):
    """A browser challenge needs operator intervention."""


class ResponseFormatError(FunPayError):
    """The remote result cannot be confirmed; stop batch writes."""


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html.replace("<br>", "\n").replace("<br/>", "\n"), "lxml")


def money(text: str) -> Decimal:
    normalized = re.sub(r"[^\d,.\-]", "", text.replace("\xa0", "")).replace(",", ".")
    try:
        return Decimal(normalized or "0").quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise FunPayError("Не удалось разобрать денежную сумму") from error


def currency(text: str) -> str:
    if "$" in text or "USD" in text.upper():
        return "USD"
    if "€" in text or "EUR" in text.upper():
        return "EUR"
    return "RUB"


def app_data(soup: BeautifulSoup) -> dict:
    body = soup.find("body")
    if body and body.get("data-app-data"):
        try:
            return json.loads(body["data-app-data"])
        except (ValueError, TypeError) as error:
            raise FunPayError("Некорректный data-app-data FunPay") from error
    for script in soup.find_all("script"):
        match = re.search(r"(?:app_data|appData)\s*=\s*(\{.*?\})\s*;", script.get_text(), re.S)
        if match:
            try:
                return json.loads(match[1])
            except ValueError:
                continue
    return {}


def avatar_url(element) -> str | None:
    """Only accept FunPay-hosted avatar assets from img or background-image markup."""
    if element is None:
        return None
    photo = (
        element
        if "avatar-photo" in element.get("class", [])
        else element.select_one(".avatar-photo")
    )
    candidate = None
    if photo:
        image = photo if photo.name == "img" else photo.select_one("img")
        candidate = (image.get("src") or image.get("data-src")) if image else None
    if not candidate:
        style = photo.get("style", "") if photo else ""
        match = re.search(r"url\(\s*['\"]?([^)'\"]+)", style)
        candidate = match.group(1) if match else None
    if not candidate and photo:
        candidate = photo.get("data-src") or photo.get("data-avatar")
    if not candidate:
        image = element if element.name == "img" else element.select_one("img.avatar, .avatar img")
        candidate = (image.get("src") or image.get("data-src")) if image else None
    if not candidate or candidate.startswith("data:"):
        return None
    return normalize_avatar_url(candidate)


def parse_runner_users(result: dict) -> dict[int, str]:
    """Extract verified avatar URLs from the optional Runner user payload."""
    users = result.get("users")
    if isinstance(users, dict):
        entries = users.items()
    elif isinstance(users, list):
        entries = ((item.get("id"), item) for item in users if isinstance(item, dict))
    else:
        return {}
    avatars: dict[int, str] = {}
    for key, value in entries:
        if not isinstance(value, dict):
            continue
        ident = value.get("id") or value.get("user_id") or key
        if not str(ident).isdigit():
            continue
        candidate = value.get("avatar") or value.get("avatar_url") or value.get("photo")
        if isinstance(candidate, str):
            markup = f'<div class="avatar-photo" style="background-image:url({candidate})"></div>'
            url = avatar_url(soup_of(markup))
            if url:
                avatars[int(ident)] = url
    return avatars


def parse_profile(html: str) -> dict:
    soup = soup_of(html)
    data = app_data(soup)
    name = soup.select_one(".user-link-name")
    link = soup.select_one("a.user-link")
    body = soup.find("body")
    user_id = (
        data.get("userId")
        or (link.get("data-id") if link else None)
        or (body.get("data-id") if body else None)
    )
    if not user_id and link:
        match = re.search(r"/users/(\d+)", link.get("href", ""))
        user_id = match[1] if match else None
    if not name or not user_id:
        raise UnauthorizedException("FunPay не вернул авторизованный профиль")
    meta = soup.select_one('meta[name="csrf-token"]')
    csrf = (
        data.get("csrf-token") or data.get("csrf_token") or (meta.get("content") if meta else None)
    )
    if not csrf:
        raise FunPayError("CSRF-токен отсутствует: формат страницы изменился")
    result = {
        "funpay_id": int(user_id),
        "username": name.get_text(strip=True),
        "avatar_url": avatar_url(soup.select_one(".user-link")),
        "csrf": csrf,
        "balance_rub": Decimal(0),
        "balance_usd": Decimal(0),
        "balance_eur": Decimal(0),
        "active_sales": 0,
        "active_purchases": 0,
    }
    for balance in soup.select(".badge-balance"):
        text = balance.get_text(" ", strip=True)
        result[f"balance_{currency(text).lower()}"] = money(text)
    for key, selector in (
        ("active_sales", ".badge-trade"),
        ("active_purchases", ".badge-orders"),
    ):
        item = soup.select_one(selector)
        result[key] = int(re.sub(r"\D", "", item.get_text()) or 0) if item else 0
    return result


def parse_messages(data: dict, own_id: int) -> list[dict]:
    result = []
    previous_date = None
    node = data.get("node", {})
    node_id = str(node.get("id", "")) if isinstance(node, dict) else str(node)
    for item in sorted(
        data.get("messages", []),
        key=lambda row: int(row["id"]) if str(row.get("id", "")).isdigit() else 0,
    ):
        soup = soup_of(item.get("html", ""))
        timestamp = message_date(item, soup)
        if timestamp:
            previous_date = timestamp
        wrapper = soup.select_one(".chat-msg-item")
        text = soup.select_one(".chat-msg-text")
        sender = soup.select_one("a.chat-msg-author-link, .chat-msg-author a, a[href*='/users/']")
        match = re.search(r"/users/(\d+)", sender.get("href", "")) if sender else None
        sender_id = int(item.get("author", 0) or (match[1] if match else 0))
        image = soup.select_one("a.chat-img-link")
        sender_avatar = avatar_url(wrapper) if wrapper else None
        badge = soup.select_one(".chat-msg-author-label.label-success")
        is_arbitration = bool(
            badge
            and badge.get_text(" ", strip=True).casefold()
            in {"арбитраж", "арбітраж", "arbitration"}
        )
        raw_id = item.get("id") or (
            wrapper.get("id", "").removeprefix("message-") if wrapper else ""
        )
        if not str(raw_id).isdigit() or not node_id:
            continue
        result.append(
            {
                "funpay_message_id": int(raw_id),
                "node_id": node_id,
                "sender_id": sender_id,
                "sender_name": sender.get_text(strip=True) if sender else "FunPay",
                "text": text.get_text("\n", strip=True) if text else "",
                "is_my": sender_id == own_id,
                "has_image": bool(image),
                "image_url": urljoin(BASE, image["href"]) if image else None,
                "sender_avatar": sender_avatar,
                "is_arbitration": is_arbitration,
                "created_at": timestamp or previous_date or UNKNOWN_DATE,
            }
        )
    return result


def parse_lots(html: str) -> list[dict]:
    soup = soup_of(html)
    lots = []
    for link in soup.select("a.tc-item[href*='offer?'], a.tc-item[href*='offer/']"):
        match = re.search(r"(?:id=|offer/)(\d+)", link.get("href", ""))
        heading = link.find_previous("div", class_="offer-list-title-container")
        container = (
            heading.parent
            if heading
            else link.find_parent(class_="offer")
            or link.find_parent(class_="profile-offers-container")
            or soup
        )
        category_link = (
            heading.select_one("h3 a")
            if heading
            else container.select_one("a[href*='/lots/'][href*='/']")
        )
        node_match = (
            re.search(r"/lots/(\d+)", category_link.get("href", "")) if category_link else None
        )
        node_id = (
            link.get("data-node")
            or container.get("data-node")
            or (node_match[1] if node_match else 0)
        )
        title = link.select_one(".tc-desc-text, .tc-desc")
        price = link.select_one(".tc-price")
        if not match or not title or not price or not int(node_id):
            continue
        game = container.get("data-game") or container.get("data-id") or 0
        amount = link.select_one(".tc-amount")
        lots.append(
            {
                "funpay_lot_id": match[1],
                "node_id": int(node_id),
                "game_id": int(game) if str(game).isdigit() else 0,
                "category_title": category_link.get_text(" ", strip=True)
                if category_link
                else str(node_id),
                "title": title.get_text(" ", strip=True),
                "price": money(price.get("data-s", price.get_text())),
                "amount": int(re.sub(r"\D", "", amount.get_text()) or 1) if amount else 1,
                "is_active": not {"offer-disabled", "warning"}.intersection(link.get("class", [])),
            }
        )
    return lots


class FunPayClient:
    def __init__(
        self,
        golden_key: str,
        user_agent: str = USER_AGENT,
        proxy: str | None = None,
        transport: str = "httpx",
    ):
        self.csrf = ""
        self.user_id = 0
        self.tags: dict[str, str] = {}
        self.last_messages: dict[str, int] = {}
        self.transport = transport
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": BASE,
        }
        cookies = {"golden_key": golden_key}
        if transport == "curl":
            from curl_cffi.requests import AsyncSession

            self.http = AsyncSession(
                headers=headers,
                cookies=cookies,
                proxy=proxy,
                timeout=15,
                impersonate="chrome",
            )
        else:
            self.http = httpx.AsyncClient(
                headers=headers,
                cookies=cookies,
                proxy=proxy,
                timeout=15,
                http2=True,
                follow_redirects=False,
            )
        self.lock = asyncio.Lock()
        self.request_lock = asyncio.Lock()
        self.network_slots = asyncio.Semaphore(5)
        self.offer_locks: dict[str, asyncio.Lock] = {}
        self.next_request_at = 0.0

    async def close(self):
        if self.transport == "curl":
            await self.http.close()
        else:
            await self.http.aclose()

    async def request(self, method: str, path: str, *, safe: bool = False, **kwargs):
        url = urljoin(BASE, path)
        if urlparse(url).netloc != "funpay.com":
            raise FunPayError("Запрещён внешний адрес запроса")
        attempts = 3 if safe else 1
        for attempt in range(attempts):
            try:
                async with self.network_slots:
                    async with self.request_lock:
                        delay = self.next_request_at - monotonic()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        self.next_request_at = monotonic() + 0.35
                    response = await self.http.request(method, url, **kwargs)
            except Exception as error:
                if attempt + 1 == attempts:
                    raise FunPayError(
                        "Сетевая ошибка. Результат изменяющего запроса может быть неизвестен."
                    ) from error
                await asyncio.sleep(2**attempt + random.random())
                continue
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if "login" in location:
                    raise UnauthorizedException("Сессия FunPay истекла")
                raise FunPayError("Неожиданный редирект FunPay")
            lower = response.text[:10000].lower()
            if response.status_code == 403 or "cf-chl-" in lower or "g-recaptcha" in lower:
                raise ChallengeException(
                    "FunPay требует проверку в браузере. Проверьте сессию и прокси."
                )
            if response.status_code == 401:
                raise UnauthorizedException("FunPay отклонил сессию")
            if response.status_code == 429:
                try:
                    retry_after = min(60, max(1, int(response.headers.get("retry-after", "5"))))
                except ValueError:
                    retry_after = 5
                self.next_request_at = max(self.next_request_at, monotonic() + retry_after)
            if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                await asyncio.sleep(2**attempt + random.random())
                continue
            if response.status_code >= 400:
                raise FunPayError(f"FunPay вернул HTTP {response.status_code}")
            return response
        raise FunPayError("Лимит сетевых повторов исчерпан")

    async def sync_profile(self) -> dict:
        response = await self.request("GET", "", safe=True)
        profile = parse_profile(response.text)
        self.csrf = profile.pop("csrf")
        self.user_id = profile["funpay_id"]
        try:
            profile["avatar_url"] = await self.get_user_avatar(self.user_id)
            profile.update(getattr(self, "profile_metrics", {}))
        except FunPayError:
            pass
        return profile

    async def runner(self, objects: list[dict], action: dict | None = None) -> dict:
        if not self.csrf:
            await self.sync_profile()
        # Browser protocol uses a form containing JSON strings, not an application/json body.
        response = await self.request(
            "POST",
            "runner/",
            safe=action is None,
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={
                "objects": json.dumps(objects),
                "request": json.dumps(action) if action else "false",
                "csrf_token": self.csrf,
            },
        )
        result = self.json_response(response)
        if result.get("error"):
            raise FunPayError("Runner отклонил запрос: " + str(result["error"])[:200])
        confirmation = result.get("response")
        if action and (
            not isinstance(confirmation, dict)
            or not confirmation
            or confirmation.get("error") is not None
        ):
            raise FunPayError("Отправка не подтверждена FunPay")
        for item in result.get("objects", []):
            self.tags[f"{item['type']}:{item['id']}"] = item.get("tag", "00000000")
            if item.get("type") == "chat_node" and isinstance(item.get("data"), dict):
                data = item["data"]
                node = data.get("node") or {}
                aliases = {str(item["id"])}
                if isinstance(node, dict):
                    aliases.update(str(node[key]) for key in ("id", "name") if node.get(key))
                message_ids = [
                    int(message["id"])
                    for message in data.get("messages", [])
                    if str(message.get("id", "")).isdigit()
                ]
                for alias in aliases:
                    self.tags[f"chat_node:{alias}"] = item.get("tag", "00000000")
                    if message_ids:
                        self.last_messages[alias] = max(
                            self.last_messages.get(alias, -1), max(message_ids)
                        )
        return result

    @staticmethod
    def json_response(response) -> dict:
        try:
            data = response.json()
        except ValueError as error:
            raise ResponseFormatError(
                "FunPay вернул HTML вместо JSON. Проверьте сессию и результат операции на сайте перед повтором."
            ) from error
        if not isinstance(data, dict):
            raise ResponseFormatError("Неожиданный формат ответа FunPay")
        return data

    async def poll(self, chat_ids: list[str]) -> dict:
        specs = [
            ("chat_bookmarks", str(self.user_id)),
            ("orders_counters", str(self.user_id)),
        ]
        objects = [
            {
                "type": kind,
                "id": ident,
                "tag": self.tags.get(f"{kind}:{ident}", "00000000"),
                "data": False,
            }
            for kind, ident in specs
        ]
        for ident in chat_ids[:100]:
            objects.append(
                {
                    "type": "chat_node",
                    "id": ident,
                    "tag": self.tags.get(f"chat_node:{ident}", "00000000"),
                    "data": {
                        "node": ident,
                        "last_message": self.last_messages.get(ident, -1),
                        "content": "",
                    },
                }
            )
        return await self.runner(objects)

    async def send_message(self, node: str, text: str, last_message: int = -1) -> dict:
        async with self.lock:
            return await self.runner(
                [],
                {
                    "action": "chat_message",
                    "data": {
                        "node": node,
                        "last_message": last_message,
                        "content": text,
                    },
                },
            )

    async def upload_image(self, content: bytes, filename: str, content_type: str) -> str:
        if self.transport == "curl":
            from curl_cffi import CurlMime

            multipart = CurlMime()
            multipart.addpart(
                name="file", content_type=content_type, filename=filename, data=content
            )
            multipart.addpart(name="csrf_token", data=self.csrf.encode())
            try:
                response = await self.request(
                    "POST",
                    "chat/upload",
                    multipart=multipart,
                )
            finally:
                multipart.close()
        else:
            response = await self.request(
                "POST",
                "chat/upload",
                data={"csrf_token": self.csrf},
                files={"file": (filename, content, content_type)},
            )
        data = self.json_response(response)
        ident = data.get("id") or data.get("image_id")
        if data.get("error") or not str(ident).isdigit():
            raise FunPayError("FunPay не подтвердил загрузку изображения")
        return str(ident)

    async def get_chats(self) -> list[dict]:
        response = await self.request("GET", "chat/", safe=True)
        soup = soup_of(response.text)
        chats = []
        for item in soup.select(".contact-item[data-id]"):
            name = item.select_one(".contact-item-name, .media-user-name")
            text = item.select_one(".contact-item-message")
            ident = str(item["data-id"])
            link = item.select_one("a[href*='/users/']")
            user_match = re.search(r"/users/(\d+)", link.get("href", "")) if link else None
            node_match = re.fullmatch(r"users-\d+-(\d+)", ident)
            user_id = (
                item.get("data-user")
                or (user_match[1] if user_match else None)
                or (node_match[1] if node_match else 0)
            )
            if name:
                chats.append(
                    {
                        "funpay_chat_id": ident,
                        "interlocutor_id": int(user_id) if str(user_id).isdigit() else 0,
                        "interlocutor_name": name.get_text(strip=True),
                        "interlocutor_avatar": avatar_url(item),
                        "last_message_text": text.get_text(" ", strip=True) if text else "",
                        "unread_count": 1 if "unread" in item.get("class", []) else 0,
                    }
                )
        return chats

    async def get_user_avatar(self, user_id: int) -> str | None:
        if user_id <= 0:
            return None
        page = await self.request("GET", f"users/{user_id}/", safe=True)
        soup = soup_of(page.text)
        if user_id == self.user_id:
            metrics = {}
            rating = soup.select_one(".rating-value .big")
            count = soup.select_one(".rating-full-count")
            if rating:
                match = re.search(r"[0-5](?:[.,]\d{1,2})?", rating.get_text(strip=True))
                if match:
                    metrics["rating"] = Decimal(match[0].replace(",", "."))
            if count:
                match = re.search(r"\d[\d\s]*", count.get_text(" ", strip=True))
                if match:
                    metrics["review_count"] = int(re.sub(r"\s", "", match[0]))
            self.profile_metrics = metrics
        return avatar_url(
            soup.select_one(".profile-header .avatar-photo, .profile-cover .avatar-photo")
            or soup.select_one(".avatar-photo")
            or soup
        )

    async def get_lots(self, user_id: int | None = None) -> list[dict]:
        response = await self.request("GET", f"users/{user_id or self.user_id}/", safe=True)
        return parse_lots(response.text)

    async def raise_lots(self, node_id: int, game_id: int) -> dict:
        if not self.csrf:
            await self.sync_profile()
        if not game_id:
            page = await self.request("GET", f"lots/{node_id}/", safe=True)
            button = soup_of(page.text).select_one(
                ".js-lot-raise[data-game], [data-game][data-node]"
            )
            game_id = int(button.get("data-game", 0)) if button else 0
            if not game_id:
                raise FunPayError(
                    "Не удалось определить ID игры для поднятия; проверьте категорию на FunPay"
                )
        response = await self.request(
            "POST",
            "lots/raise",
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={
                "node_id": node_id,
                "node_ids[]": str(node_id),
                "game_id": game_id,
                "csrf_token": self.csrf,
            },
        )
        data = self.json_response(response)
        message = str(data.get("msg", ""))
        if "error" not in data or data.get("modal") or data.get("url"):
            raise ResponseFormatError(
                "FunPay не подтвердил поднятие выбранной категории. Проверьте её на сайте"
            )
        wait = data.get("wait", 0)
        cooldown = min(86400, max(0, int(wait))) if str(wait).isdigit() else 0
        if data.get("error"):
            hours = re.search(r"(\d+)\s*(?:час|hour)", message)
            minutes = re.search(r"(\d+)\s*(?:мин|minute)", message)
            seconds = re.search(r"(\d+)\s*(?:сек|second)", message)
            cooldown = cooldown or (
                (int(hours[1]) * 3600 if hours else 0)
                + (int(minutes[1]) * 60 if minutes else 0)
                + (int(seconds[1]) if seconds else 0)
            )
            if not cooldown:
                raise FunPayError(message[:300] or "Поднятие отклонено FunPay")
        return {
            "raised": not bool(data.get("error")),
            "cooldown": cooldown,
            "message": message,
        }

    async def update_lot(
        self, offer_id: str, *, price: Decimal | None = None, active: bool | None = None
    ) -> None:
        async with self.offer_lock(offer_id):
            await self._update_lot(offer_id, price=price, active=active)

    def offer_lock(self, offer_id):
        return self.offer_locks.setdefault(str(offer_id), asyncio.Lock())

    async def _update_lot(self, offer_id, *, price=None, active=None):
        from .lot_editor import load_form, save_form

        metadata, fields = await load_form(self, offer_id=str(offer_id))
        if price is not None:
            if "price" not in fields:
                raise FunPayError("Поле цены не найдено")
            fields["price"] = str(price)
        if active is not None:
            control = next(
                (item for item in metadata["controls"] if item["name"] == "active"), None
            )
            if not control:
                raise FunPayError("Переключатель активности не найден в форме лота")
            fields["active"] = control["checked_value"] if active else ""
        await save_form(self, fields, allow_no_error_ack=True)

    async def order_details(self, order_id: str) -> dict:
        response = await self.request(
            "POST",
            "api/orders/get",
            safe=True,
            json={"order_uids": [order_id], "include": ["details", "users", "review"]},
        )
        result = self.json_response(response)
        detail = result.get("data", {}).get(order_id)
        if result.get("status") != "SUCCESS" or not isinstance(detail, dict):
            raise FunPayError("FunPay не вернул сведения о заказе")
        if int(detail.get("seller", {}).get("user_id", 0)) != self.user_id:
            raise FunPayError("Заказ не принадлежит продавцу")
        return detail

    async def reply_review(self, order_id: str, text: str):
        response = await self.request(
            "POST",
            "orders/review",
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={
                "authorId": self.user_id,
                "orderId": order_id,
                "text": text,
                "rating": "",
                "csrf_token": self.csrf,
            },
        )
        result = self.json_response(response)
        if result.get("error") or not result.get("content"):
            raise FunPayError("Ответ на отзыв не подтверждён FunPay")

    async def refund(self, order_id: str):
        detail = await self.order_details(order_id)
        if detail.get("status") == "refunded":
            return
        if detail.get("status") != "paid":
            raise FunPayError("Возврат доступен только для оплаченного незакрытого заказа")
        if not self.csrf:
            await self.sync_profile()
        response = await self.request(
            "POST",
            "orders/refund",
            headers={"X-Requested-With": "XMLHttpRequest"},
            data={"id": order_id, "csrf_token": self.csrf},
        )
        result = self.json_response(response)
        if result.get("error"):
            raise FunPayError("FunPay отклонил возврат")
        if (await self.order_details(order_id)).get("status") != "refunded":
            raise FunPayError("Возврат не подтверждён. Требуется сверка")

    async def viewed_lot(self, buyer_id: int):
        result = await self.runner(
            [{"type": "c-p-u", "id": str(buyer_id), "tag": "00000000", "data": False}]
        )
        for obj in result.get("objects", []):
            if obj.get("type") != "c-p-u" or str(obj.get("id")) != str(buyer_id):
                continue
            html = (obj.get("data") or {}).get("html") or {}
            soup = soup_of(html.get("desktop", "") if isinstance(html, dict) else str(html))
            from urllib.parse import parse_qs

            for link in soup.select("a[href]"):
                url = urlparse(urljoin(BASE, link["href"]))
                if url.scheme != "https" or url.hostname not in {"funpay.com", "www.funpay.com"}:
                    continue
                match = re.fullmatch(r"/lots/offer/(\d+)/?", url.path)
                ident = (
                    match[1]
                    if match
                    else (
                        parse_qs(url.query).get("id", [""])[0]
                        if url.path.rstrip("/") == "/lots/offer"
                        else ""
                    )
                )
                if ident.isdigit() and int(ident) > 0:
                    return ident
        return None

    async def get_orders(self) -> list[dict]:
        response = await self.request("GET", "orders/trade", safe=True)
        soup = soup_of(response.text)
        orders = []
        for row in soup.select("a.tc-item[href*='/orders/']"):
            ident = re.search(r"/orders/([A-Za-z0-9]+)/?", row.get("href", ""))
            price = row.select_one(".tc-price")
            desc = row.select_one(".order-desc, .tc-desc-text, .tc-desc")
            buyer = row.select_one(".media-user-name, .tc-user .media-user-name")
            buyer_link = row.select_one("[data-href*='/users/'], a[href*='/users/']")
            buyer_id = (
                re.search(
                    r"/users/(\d+)",
                    buyer_link.get("data-href", buyer_link.get("href", "")),
                )
                if buyer_link
                else None
            )
            classes = " ".join(row.get("class", []))
            status = (
                "refunded"
                if "warning" in classes or "refunded" in classes
                else "paid"
                if "info" in row.get("class", [])
                else "closed"
            )
            if ident and price and desc:
                offer_link = row.select_one("a[href*='/lots/offer/'], a[href*='/lots/offerEdit']")
                offer_source = row.get("data-offer-id", "") or (
                    offer_link.get("href", "") if offer_link else ""
                )
                offer_match = re.search(
                    r"(?:offer(?:Edit)?/|offer_id=|^)(\d+)(?:\D|$)", offer_source
                )
                orders.append(
                    {
                        "funpay_order_id": ident[1],
                        "funpay_lot_id": offer_match[1] if offer_match else None,
                        "description": desc.get_text(" ", strip=True),
                        "sum": money(price.get_text()),
                        "currency": currency(price.get_text()),
                        "buyer_id": int(buyer_id[1]) if buyer_id else 0,
                        "buyer_name": buyer.get_text(strip=True) if buyer else "",
                        "status": status,
                    }
                )
        return orders
