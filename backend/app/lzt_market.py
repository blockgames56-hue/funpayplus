"""Bounded LZT Market buyer. Purchase writes are never retried after an unknown result."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlparse

import httpx
from sqlalchemy import select

from .db import Session
from .events import bus
from .models import Activity, MarketPurchase, utcnow
from .security import vault
from .services import get_setting, save_setting

BASE = "https://api.lzt.market"
CATEGORIES = {"steam", "epicgames"}
SETTINGS_KEY = "lzt_autobuy"


def parse_filter(url: str) -> tuple[str, list[tuple[str, str]]]:
    parsed = urlparse(url.strip())
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Некорректный порт в ссылке фильтра") from error
    if parsed.scheme != "https" or parsed.hostname != "lzt.market" or port or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Нужна HTTPS-ссылка на фильтр lzt.market")
    category = parsed.path.strip("/")
    if category not in CATEGORIES:
        raise ValueError("Поддерживаются фильтры Steam и Epic Games")
    pairs = parse_qsl(parsed.query, keep_blank_values=False)
    if len(pairs) > 40:
        raise ValueError("Слишком много параметров фильтра")
    result = []
    for key, value in pairs:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\[\]]{0,63}", key) or len(value) > 200:
            raise ValueError("Некорректный параметр фильтра")
        if key == "currency" and value.lower() != "rub":
            raise ValueError("Для резерва баланса поддерживается только RUB")
        result.append(("pmax" if key == "max_price" else key, value))
    result = [(key, value) for key, value in result if key != "order_by"]
    result.append(("order_by", "pdate_to_down"))
    if not any(key == "currency" for key, _ in result):
        result.append(("currency", "rub"))
    return category, result


def amount(value) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Некорректная цена") from error


def retry_seconds(headers: httpx.Headers) -> float:
    value = headers.get("Retry-After", "")
    try:
        return min(3600, max(1, float(value)))
    except ValueError:
        try:
            return min(3600, max(1, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, OverflowError, ValueError):
            try:
                return min(3600, max(3, float(headers.get("X-RateLimit-Reset", "0")) - time.time()))
            except ValueError:
                return 30


class RateGate:
    def __init__(self, interval: float = 3):
        self.interval = interval
        self.next_at = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            delay = self.next_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_at = time.monotonic() + self.interval

    def freeze(self, seconds: float):
        self.next_at = max(self.next_at, time.monotonic() + seconds)


class MarketError(Exception):
    pass


class RateLimited(MarketError):
    pass


class MarketClient:
    def __init__(self, token: str, *, transport=None):
        self.http = httpx.AsyncClient(base_url=BASE, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, transport=transport, timeout=httpx.Timeout(20, read=30), follow_redirects=False)
        self.gate = RateGate()

    async def close(self):
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        await self.gate.wait()
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise MarketError("Сетевая ошибка LZT Market; результат покупки может быть неизвестен") from error
        if response.status_code == 429:
            self.gate.freeze(retry_seconds(response.headers))
            raise RateLimited("Лимит LZT Market; ожидание Retry-After")
        if response.status_code >= 400:
            raise MarketError(f"LZT Market вернул HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise MarketError("LZT Market вернул не JSON") from error
        if not isinstance(data, dict) or data.get("error") or data.get("success") is False:
            raise MarketError("LZT Market отклонил запрос")
        return data


def candidates(data: dict) -> list[dict]:
    rows = data.get("items", [])
    if isinstance(rows, dict):
        rows = [{"item_id": key, **value} for key, value in rows.items() if isinstance(value, dict)]
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def balance_rub(data: dict) -> Decimal | None:
    rows = data.get("balances") or data.get("balance")
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get("currency", "")).lower() == "rub":
            try:
                return amount(row.get("amount", row.get("balance")))
            except ValueError:
                return None
    return None


class MarketWorker:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.balance: Decimal | None = None
        self.last_error: str | None = None
        self._client: MarketClient | None = None

    async def start(self):
        if self.task and not self.task.done():
            return
        settings = await get_setting(SETTINGS_KEY, None)
        if not settings or not settings.get("enabled"):
            return
        self.balance = None
        self.last_error = None
        self.task = asyncio.create_task(self.run(settings), name="lzt-market-buyer")

    async def stop(self):
        task = self.task
        self.task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def run(self, settings: dict):
        category, params = parse_filter(settings["filter_url"])
        token = vault.decrypt(settings["token"])
        client = MarketClient(token)
        self._client = client
        self.last_error = None
        last_balance = 0.0
        try:
            while True:
                delay = 3
                try:
                    if time.monotonic() - last_balance > 60:
                        self.balance = balance_rub(await client.request("GET", "/balance/exchange"))
                        last_balance = time.monotonic()
                    search_params = [(k, v) for k, v in params if k != "pmax"]
                    cap = amount(settings["max_price"])
                    url_caps = [amount(v) for k, v in params if k == "pmax"]
                    search_params.append(("pmax", str(min([cap, *url_caps]))))
                    data = await client.request("GET", f"/{category}", params=search_params)
                    for item in candidates(data):
                        await self._consider(client, category, item, settings)
                    self.last_error = None
                except RateLimited as error:
                    self.last_error = str(error)
                except (MarketError, ValueError) as error:
                    self.last_error = str(error)
                    await bus.log("logs", self.last_error, level="warning", source_plugin='lzt')
                    delay = 30
                await asyncio.sleep(delay)
        finally:
            self._client = None
            await client.close()

    async def _consider(self, client: MarketClient, category: str, item: dict, settings: dict):
        ident = item.get("item_id", item.get("id"))
        if not str(ident).isdigit():
            return
        ident = int(ident)
        try:
            price = amount(item.get("price"))
        except ValueError:
            return
        reserve = amount(settings["reserve_balance"])
        if price > amount(settings["max_price"]) or (self.balance is None and reserve > 0) or (self.balance is not None and self.balance - price < reserve):
            return
        title = str(item.get("title", f"Лот #{ident}"))[:500]
        async with Session() as db:
            if await db.scalar(select(MarketPurchase.id).where(MarketPurchase.item_id == ident)):
                return
            game = str(item.get("game", item.get("category", category)))[:100]
            purchase = MarketPurchase(item_id=ident, category=category, game=game, title=title, price=price, currency="rub", status="pending")
            db.add(purchase)
            await db.commit()
        try:
            await client.request("POST", "/cart", json={"item_id": ident})
            await client.request("POST", f"/{ident}/check-account")
        except MarketError:
            await self._set_status(ident, "failed")
            return
        async with Session() as db:
            purchase = await db.scalar(select(MarketPurchase).where(MarketPurchase.item_id == ident))
            purchase.status = "buying"
            await db.commit()
        try:
            result = await client.request("POST", f"/{ident}/fast-buy", json={"price": float(price)})
        except MarketError:
            await self._set_status(ident, "uncertain")
            return
        try:
            details = await client.request("GET", f"/{ident}")
        except MarketError:
            details = result
        secrets_data = {}
        for payload in (result, details):
            credentials = payload.get("item") if isinstance(payload.get("item"), dict) else payload
            for key in ("login", "password", "email", "email_login_data", "login_password"):
                if isinstance(credentials.get(key), str) and credentials[key]:
                    secrets_data[key] = credentials[key]
        async with Session() as db:
            purchase = await db.scalar(select(MarketPurchase).where(MarketPurchase.item_id == ident))
            purchase.status = "confirmed"
            purchase.confirmed_at = utcnow()
            purchase.credentials = vault.encrypt(json.dumps(secrets_data)) if secrets_data else None
            db.add(Activity(channel="orders", text=f"Лот #{ident} куплен на LZT", source_plugin="lzt"))
            await db.commit()
        if self.balance is not None:
            self.balance -= price
        await bus.publish("orders", {"event": "MARKET_PURCHASE", "item_id": ident})

    async def _set_status(self, ident: int, status: str):
        async with Session() as db:
            purchase = await db.scalar(select(MarketPurchase).where(MarketPurchase.item_id == ident))
            if purchase:
                purchase.status = status
                await db.commit()


worker = MarketWorker()


async def save_config(data: dict):
    previous = await get_setting(SETTINGS_KEY, {})
    token = vault.encrypt(data["token"]) if data.get("token") else previous.get("token")
    if not token:
        raise ValueError("Укажите API ключ LZT Market")
    category, params = parse_filter(data["filter_url"])
    for key, value in params:
        if key == "pmax":
            amount(value)
    amount(data["max_price"])
    amount(data["reserve_balance"])
    stored = {**data, "token": token, "category": category}
    await worker.stop()
    await save_setting(SETTINGS_KEY, stored)
    await worker.start()
