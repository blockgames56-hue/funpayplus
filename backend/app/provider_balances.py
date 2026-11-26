"""Balance-triggered visibility changes affect only lots hidden by this plugin."""

import asyncio
from decimal import Decimal

from sqlalchemy import select

from .db import Session
from .events import bus
from .integrated_config import plain_config
from .models import Account, Lot, PluginBinding
from .plugin_providers import points_request, smm_request
from .services import accounts, get_setting, save_setting, telegram


async def visibility(kind, provider_id, low):
    key = f"integrated:hidden:{kind}:{provider_id}"
    hidden = set(await get_setting(key, []))
    async with Session() as db:
        query = (
            select(PluginBinding, Lot)
            .join(Lot)
            .join(Account, Lot.account_id == Account.id)
            .where(
                PluginBinding.kind == kind,
                PluginBinding.is_active.is_(True),
                Account.is_active.is_(True),
            )
        )
        if provider_id:
            query = query.where(PluginBinding.provider_id == provider_id)
        rows = (await db.execute(query)).all()
        for _, lot in rows:
            if (low and lot.is_active) or (not low and lot.id in hidden and not lot.is_active):
                await (await accounts.client(lot.account_id)).update_lot(
                    lot.funpay_lot_id, active=not low
                )
                lot.is_active = not low
                if low:
                    hidden.add(lot.id)
                else:
                    hidden.discard(lot.id)
                await db.commit()
                await save_setting(key, sorted(hidden))
    await save_setting(key, sorted(hidden))


async def check(kind, provider, config):
    try:
        result = (
            await smm_request(provider, "balance")
            if kind == "auto-smm"
            else await points_request("balance", config)
        )
        balance = Decimal(str(result.get("balance", "NaN")))
        if not balance.is_finite():
            raise ValueError("Поставщик не вернул баланс")
        threshold = Decimal(str(config["low_balance_threshold"]))
        low = balance <= threshold
        await visibility(kind, provider.get("id", "") if provider else "", low)
        alert_key = f"integrated:balance-alert:{kind}:{provider.get('id', '') if provider else ''}"
        previous = await get_setting(alert_key, False)
        if low and not previous:
            await telegram.notify(
                "stock", f"{kind}: низкий баланс поставщика. Лоты временно скрыты."
            )
        await save_setting(alert_key, low)
    except Exception:
        await bus.log(
            "lots",
            f"{kind}: не удалось проверить баланс; видимость лотов не менялась без подтверждения",
            level="error",
         source_plugin=kind)


async def tick():
    tasks = []
    for kind in ("auto-smm", "steam-points"):
        config = await plain_config(kind)
        if not config.get("enabled") or not config.get("hide_on_low_balance"):
            continue
        if kind == "auto-smm":
            tasks.extend(check(kind, provider, config) for provider in config["providers"])
        elif config.get("api_key"):
            tasks.append(check(kind, None, config))
    await asyncio.gather(*tasks)
