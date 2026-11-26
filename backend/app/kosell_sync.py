"""Stock intervals, opt-in markup and reversible plugin-owned lot hiding."""

import asyncio
import contextlib
from decimal import Decimal

from sqlalchemy import select

from . import kosell
from .models import Account, Lot, PluginBinding, utcnow
from .services import get_setting, save_setting

sync_lock = asyncio.Lock()
due = {}
last_price = 0.0
last_balance = 0.0


def in_scope(config, account_id):
    return config["account_scope"] == "all" or account_id in config["account_ids"]


async def manual_override(lot_id):
    markers = await get_setting("kosell:hidden", {})
    if str(lot_id) in markers:
        markers.pop(str(lot_id), None)
        await save_setting("kosell:hidden", markers)


async def balance_check():
    config = await kosell.settings()
    result = await kosell.api("GET", "/account/balance")
    safe = {k: result.get(k) for k in ("username", "balance_rub", "balance_usd")}
    if safe["balance_rub"] is None or safe["balance_usd"] is None:
        raise kosell.KoSellError("Баланс не подтверждён")
    await save_setting("kosell:balance", safe)
    threshold = Decimal(str(config["balance_threshold"]))
    code = config["balance_currency"]
    low = threshold > 0 and Decimal(str(safe["balance_" + code.lower()])) < threshold
    latched = await get_setting("kosell:balance_alert", False)
    if low and not latched:
        await kosell.log(f"Баланс KoSell ниже порога {threshold} {code}", "warning")
        with contextlib.suppress(Exception):
            await kosell.notify(
                "kosell", "kosell_low_balance", {"threshold": str(threshold), "currency": code}
            )
    await save_setting("kosell:balance_alert", low)


async def sync(product_ids=None, reprice=True):
    async with sync_lock:
        config = await kosell.settings(True)
        if not config["enabled"]:
            return
        try:
            catalog = {p["id"]: p for p in await kosell.products()}
        except kosell.KoSellError as error:
            await kosell.log(
                f"Каталог недоступен: HTTP {error.status}. Лоты не изменены", "warning"
            )
            return
        balance = await get_setting("kosell:balance", {}) or {}
        threshold = Decimal(str(config["balance_threshold"]))
        value = balance.get("balance_" + config["balance_currency"].lower())
        low = (
            config["hide_low_balance"]
            and threshold > 0
            and value is not None
            and Decimal(str(value)) < threshold
        )
        markers = await get_setting("kosell:hidden", {})
        quotes = {}
        changed = 0
        async with kosell.Session() as db:
            rows = (
                await db.execute(
                    select(PluginBinding, Lot)
                    .join(Lot)
                    .join(Account, Lot.account_id == Account.id)
                    .where(
                        PluginBinding.kind == "kosell",
                        PluginBinding.is_active.is_(True),
                        Account.is_active.is_(True),
                    )
                )
            ).all()
            for binding, lot in rows:
                pid = int(binding.service_id)
                if not in_scope(config, lot.account_id) or (
                    product_ids is not None and pid not in product_ids
                ):
                    continue
                item = catalog.get(pid)
                empty = not item or item["available_accounts"] <= 0
                policy = await get_setting(f"kosell:binding:{binding.id}", {})
                expensive = False
                cost = None
                if not empty and reprice:
                    pair = (pid, binding.quantity)
                    try:
                        if pair not in quotes:
                            quotes[pair] = await kosell.price(*pair)
                        cost = Decimal(str(quotes[pair]["total_rub"]))
                        if policy.get("auto_price"):
                            from .kosell_workspace import sale_price

                            target = max(Decimal(1), sale_price(cost, policy["markup"]))
                            if target != lot.price:
                                await (await kosell.accounts.client(lot.account_id)).update_lot(
                                    lot.funpay_lot_id, price=target
                                )
                                lot.price = target
                                await db.commit()
                                changed += 1
                                await kosell.log(
                                    f"Лот #{lot.funpay_lot_id}: цена обновлена до {target} RUB",
                                    account_id=lot.account_id,
                                )
                        expensive = cost > lot.price and not config["allow_loss"]
                    except Exception:
                        await kosell.log(
                            f"Лот #{lot.funpay_lot_id}: цена не подтверждена, изменение пропущено",
                            "warning",
                        )
                        continue
                reason = (
                    "balance"
                    if low
                    else "stock"
                    if empty and config["auto_hide"]
                    else "price"
                    if expensive
                    else None
                )
                marker = markers.get(str(lot.id))
                if reason and lot.is_active:
                    try:
                        await (await kosell.accounts.client(lot.account_id)).update_lot(
                            lot.funpay_lot_id, active=False
                        )
                    except Exception:
                        await kosell.log(
                            f"Лот #{lot.funpay_lot_id}: FunPay не подтвердил выключение",
                            "error",
                            lot.account_id,
                        )
                        continue
                    lot.is_active = False
                    markers[str(lot.id)] = {"reason": reason, "price": str(lot.price)}
                    await db.commit()
                    await save_setting("kosell:hidden", markers)
                    changed += 1
                    await kosell.log(
                        f"Лот #{lot.funpay_lot_id} выключен: {reason}", "warning", lot.account_id
                    )
                    with contextlib.suppress(Exception):
                        await kosell.notify(
                            "kosell",
                            "kosell_out_of_stock_alert"
                            if reason == "stock"
                            else "kosell_price_alert"
                            if reason == "price"
                            else "kosell_balance_hidden",
                            {"item_name": item["name"] if item else binding.service_id},
                        )
                elif not reason and marker and not lot.is_active and not empty:
                    # Require a fresh price before restoring a plugin-hidden lot.
                    if not reprice or cost is None:
                        continue
                    try:
                        await (await kosell.accounts.client(lot.account_id)).update_lot(
                            lot.funpay_lot_id, active=True
                        )
                    except Exception:
                        continue
                    lot.is_active = True
                    markers.pop(str(lot.id), None)
                    await db.commit()
                    await save_setting("kosell:hidden", markers)
                    changed += 1
                    await kosell.log(
                        f"Лот #{lot.funpay_lot_id}: наличие и цена подтверждены, включён",
                        account_id=lot.account_id,
                    )
        await save_setting(
            "kosell:sync",
            {"updated_at": utcnow().isoformat(), "changed": changed, "price_check": reprice},
        )


async def monitor():
    global last_price, last_balance
    config = await kosell.settings()
    if not config["enabled"]:
        return
    now = asyncio.get_running_loop().time()
    if now - last_balance >= 60:
        try:
            await balance_check()
        except Exception:
            await kosell.log("Баланс KoSell временно не подтверждён", "warning")
        last_balance = now
    async with kosell.Session() as db:
        products = set(
            (
                await db.scalars(
                    select(PluginBinding.service_id).where(
                        PluginBinding.kind == "kosell", PluginBinding.is_active.is_(True)
                    )
                )
            ).all()
        )
    ready = {int(pid) for pid in products if now >= due.get(pid, 0)}
    reprice = now - last_price >= 300
    if not ready and not reprice:
        return
    await sync(None if reprice else ready, reprice=reprice)
    for pid in products:
        if int(pid) in ready:
            due[pid] = now + int(config["game_intervals"].get(pid, config["stock_interval"]))
    if reprice:
        last_price = now
