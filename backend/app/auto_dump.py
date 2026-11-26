"""FunPay category repricing with bounded concurrency and a hard price floor."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import ROUND_DOWN, Decimal
from urllib.parse import urlparse

from sqlalchemy import select

from .db import Session
from .events import bus
from .funpay import FunPayError, currency, money, soup_of
from .lot_editor import quote_prices
from .models import Account, AutoDumpRule, Lot, LotPromotion, utcnow
from .services import accounts, get_setting

run_lock = asyncio.Lock()


def category_path(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "funpay.com"
        or not parsed.path.startswith("/lots/")
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Нужна HTTPS ссылка категории funpay.com/lots/…")
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def parse_offers(html: str) -> list[dict]:
    offers = []
    for row in soup_of(html).select("a.tc-item[href*='offer?'], a.tc-item[href*='offer/']"):
        offer = re.search(r"(?:id=|offer/)(\d+)", row.get("href", ""))
        seller = row.get("data-user") or row.get("data-seller-id") or row.get("data-user-id") or ""
        if not str(seller).isdigit():
            link = row.select_one("[data-href*='/users/'], [href*='/users/']")
            match = (
                re.search(r"/users/(\d+)", link.get("data-href", link.get("href", "")))
                if link
                else None
            )
            seller = match[1] if match else ""
        price_node = row.select_one(".tc-price")
        if (
            not str(seller).isdigit()
            or not price_node
            or currency(price_node.get_text(" ", strip=True)) != "RUB"
        ):
            continue
        try:
            price = money(price_node.get("data-s") or price_node.get_text(" ", strip=True))
        except FunPayError:
            continue
        if price <= 0:
            continue
        username = row.select_one(".media-user-name")
        description = row.select_one(".tc-desc-text, .tc-desc")
        stars = row.select_one(".rating-stars")
        rating = re.search(r"rating-(\d+)", " ".join(stars.get("class", []))) if stars else None
        offers.append(
            {
                "id": offer[1] if offer else "",
                "seller_id": int(seller),
                "username": username.get_text(strip=True).casefold() if username else "",
                "description": description.get_text(" ", strip=True).casefold()
                if description
                else "",
                "rating": int(rating[1]) if rating else 0,
                "price": price,
            }
        )
    return offers


def select_prices(offers, own_ids, own_lot_id, options=None):
    options = options or {}
    excluded = {name.casefold() for name in options.get("excluded_sellers", [])}
    included = {name.casefold() for name in options.get("only_sellers", [])}
    keywords = [
        [word.strip().casefold() for word in line.split("|") if word.strip()]
        for line in options.get("keywords", "").splitlines()
        if line.strip()
    ]
    return [
        offer["price"]
        for offer in offers
        if offer["id"] != own_lot_id
        and offer["seller_id"] not in own_ids
        and offer["username"] not in excluded
        and (not included or offer["username"] in included)
        and offer["rating"] >= options.get("min_rating", 0)
        and (not options.get("ignore_unrated") or offer["rating"] > 0)
        and all(any(word in offer["description"] for word in line) for line in keywords)
    ]


def parse_competitor_prices(
    html: str, own_ids: set[int], own_lot_id: str, options=None
) -> list[Decimal]:
    return select_prices(parse_offers(html), own_ids, own_lot_id, options)


def target_price(competitor: Decimal, minimum: Decimal, step: Decimal) -> Decimal | None:
    if competitor <= minimum or step <= 0:
        return None
    target = (competitor - step).quantize(Decimal("0.01"))
    return target if minimum <= target < competitor else None


async def fetch_prices(client, path: str, own_ids: set[int], own_lot_id: str) -> list[Decimal]:
    response = await client.request("GET", path, safe=True)
    return await asyncio.to_thread(parse_competitor_prices, response.text, own_ids, own_lot_id)


async def fetch_page(client, path):
    response = await client.request("GET", path, safe=True)
    return await asyncio.to_thread(parse_offers, response.text)


async def commission_ratio(client, node_id):
    methods = await quote_prices(client, node_id, Decimal("1000"))
    rubles = [
        Decimal(row["price"].replace(" ", "").replace(",", "."))
        for row in methods
        if row["unit"] in {"₽", "RUB", "руб."}
    ]
    if not rubles or min(rubles) <= 0:
        raise FunPayError("Не удалось определить комиссию FunPay; цена не менялась")
    return min(rubles) / Decimal("1000")


async def scan_rule(
    rule_id: int,
    own_ids: set[int],
    semaphore: asyncio.Semaphore,
    page_cache: dict | None = None,
    fee_cache: dict | None = None,
) -> None:
    async with semaphore:
        async with Session() as db:
            rule = await db.get(AutoDumpRule, rule_id)
            if not rule or not rule.is_enabled:
                return
            lot = await db.get(Lot, rule.lot_id)
            account = await db.get(Account, lot.account_id) if lot else None
            if not lot or not lot.is_active or not account or not account.is_active:
                return
            if not account.funpay_id:
                rule.status = "error"
                rule.last_error = "Сначала синхронизируйте ID продавца"
                await db.commit()
                return
            if await db.scalar(
                select(LotPromotion.id).where(
                    LotPromotion.lot_id == lot.id, LotPromotion.state == "active"
                )
            ):
                rule.status = "promotion"
                await db.commit()
                return
            try:
                changed_price = None
                path = category_path(rule.category_url)
                client = await accounts.client(lot.account_id)
                options = json.loads(rule.options or "{}")
                if page_cache is None:
                    html = await fetch_page(client, path)
                else:
                    cache_key = (lot.account_id, path)
                    if cache_key not in page_cache:
                        page_cache[cache_key] = asyncio.create_task(fetch_page(client, path))
                    html = await page_cache[cache_key]
                prices = select_prices(html, own_ids, lot.funpay_lot_id, options)
                ratio = Decimal(1)
                if prices and options.get("convert_commission", False):
                    fee_key = (lot.account_id, lot.node_id)
                    if fee_cache is None:
                        ratio = await commission_ratio(client, lot.node_id)
                    else:
                        if fee_key not in fee_cache:
                            fee_cache[fee_key] = asyncio.create_task(
                                commission_ratio(client, lot.node_id)
                            )
                        ratio = await fee_cache[fee_key]
                rule.competitor_price = min(prices) if prices else None
                rule.last_checked_at = utcnow()
                rule.last_error = None
                if not prices:
                    rule.status = "no_competitor"
                else:
                    target_buyer = rule.competitor_price - rule.step
                    divider = Decimal(str(options.get("divider", "0.01")))
                    target_buyer = (target_buyer / divider).to_integral_value(
                        rounding=ROUND_DOWN
                    ) * divider
                    target = (target_buyer / ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                    if options.get("max_price"):
                        target = min(target, Decimal(str(options["max_price"])))
                    if target < rule.absolute_min or target >= rule.competitor_price / ratio:
                        target = None
                    if target is None:
                        rule.status = "floor"
                    elif target == lot.price:
                        rule.status = "current"
                    else:
                        await client.update_lot(lot.funpay_lot_id, price=target)
                        lot.price = target
                        rule.status = "updated"
                        changed_price = target
                await db.commit()
                if changed_price is not None:
                    await bus.log(
                        "lots",
                        f"Автодемпинг: лот #{lot.funpay_lot_id} → {changed_price} ₽",
                        lot.account_id,
                     source_plugin='auto-dump')
            except (FunPayError, ValueError, OSError) as error:
                rule.status = "error"
                rule.last_error = str(error)[:300]
                rule.last_checked_at = utcnow()
                await db.commit()
                await bus.log(
                    "lots",
                    f"Автодемпинг: лот #{lot.funpay_lot_id}: {rule.last_error}",
                    lot.account_id,
                    "error",
                 source_plugin='auto-dump')


async def scan_all() -> None:
    if not (await get_setting("plugin_controls", {})).get("auto-dump", True):
        return
    if run_lock.locked():
        return
    async with run_lock:
        async with Session() as db:
            ids = (
                await db.scalars(
                    select(AutoDumpRule.id).where(AutoDumpRule.is_enabled.is_(True)).limit(50)
                )
            ).all()
            own_ids = set(
                (
                    await db.scalars(
                        select(Account.funpay_id).where(Account.funpay_id.is_not(None))
                    )
                ).all()
            )
        semaphore = asyncio.Semaphore(5)
        page_cache, fee_cache = {}, {}
        results = await asyncio.gather(
            *(scan_rule(ident, own_ids, semaphore, page_cache, fee_cache) for ident in ids),
            return_exceptions=True,
        )
        for ident, result in zip(ids, results):
            if isinstance(result, Exception):
                await bus.log(
                    "lots",
                    f"Автодемпинг: сбой обработки правила #{ident} ({type(result).__name__})",
                    level="error",
                 source_plugin='auto-dump')
