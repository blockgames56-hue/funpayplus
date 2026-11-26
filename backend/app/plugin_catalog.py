"""Catalog controls use the same settings as the individual plugin workers."""

import asyncio
import json

from sqlalchemy import func, select

from . import email_codes, lzt_market
from .db import Session
from .models import (
    Account,
    AppRouteMapping,
    AutoDumpRule,
    OfflineGuardAccount,
    PluginBinding,
    RentalAccount,
    Settings,
)
from .schemas import PluginSettings
from .security import vault
from .services import get_setting, get_settings, save_setting

control_lock = asyncio.Lock()
CATALOG_IDS = {
    "kosell-rent",
    "kosell",
    "email-code",
    "lzt",
    "discord-boost",
    "steam-gift",
    "app-route",
    "offline",
    "auto-dump",
    "auto-robux",
    "auto-smm",
    "steam-points",
    "steam-rent",
    "auto-stars",
    "support-tickets",
}
IDS = {
    "kosell-rent",
    "kosell",
    "email-code",
    "lzt",
    "discord-boost",
    "steam-gift",
    "app-route",
    "offline",
    "auto-dump",
    "auto-robux",
    "responder",
    "auto-ticket",
    "reviews",
}


async def catalog():
    from .integrated_config import credentials_ready
    from .integrated_schemas import CONFIG_MODELS, KINDS

    defaults = {
        key: {}
        for key in (
            "imap_mailbox_default",
            lzt_market.SETTINGS_KEY,
            "fulfillment_plugins",
            "plugins",
            "offline",
            "plugin_controls",
            "auto_robux",
        )
    }
    defaults["kosell"] = {}
    defaults["kosell-rent"] = {}
    defaults.update(
        {f"integrated:{kind}": CONFIG_MODELS[kind]().model_dump(mode="json") for kind in KINDS}
    )
    snapshot = await get_settings(defaults)
    mail, market, fulfillment = (
        snapshot["imap_mailbox_default"],
        snapshot[lzt_market.SETTINGS_KEY],
        snapshot["fulfillment_plugins"],
    )
    replies, controls = snapshot["plugins"], snapshot["plugin_controls"]
    async with Session() as db:
        mapped = bool(await db.scalar(select(AppRouteMapping.id).limit(1)))
        dump = bool(
            await db.scalar(
                select(AutoDumpRule.id).where(AutoDumpRule.is_enabled.is_(True)).limit(1)
            )
        )
        bound_kinds = set(
            (
                await db.scalars(
                    select(PluginBinding.kind).where(PluginBinding.is_active.is_(True)).distinct()
                )
            ).all()
        )
        kosell_verified = bool(
            await db.scalar(
                select(PluginBinding.id)
                .where(
                    PluginBinding.kind == "kosell",
                    PluginBinding.is_active.is_(True),
                    PluginBinding.provider_id == "verified",
                )
                .limit(1)
            )
        )
        rent_stock = bool(
            await db.scalar(
                select(RentalAccount.id)
                .join(PluginBinding)
                .where(PluginBinding.kind == "steam-rent", PluginBinding.is_active.is_(True))
                .limit(1)
            )
        )
        guard_count = int(await db.scalar(select(func.count(OfflineGuardAccount.id))) or 0)
    states = {
        "email-code": (
            bool(mail) and controls.get("email-code", True),
            bool(mail),
            "IMAP подключён" if email_codes.mailbox_connected() else "IMAP IDLE",
        ),
        "lzt": (
            market.get("enabled", False),
            bool(market.get("token") and market.get("filter_url")),
            "Автозакупка аккаунтов",
        ),
        "discord-boost": (
            fulfillment.get("discord_boost", {}).get("enabled", False),
            bool(fulfillment.get("discord_boost", {}).get("lot_phrase")),
            "Очередь выдачи бустов",
        ),
        "steam-gift": (
            fulfillment.get("steam_gift", {}).get("enabled", False),
            bool(fulfillment.get("steam_gift", {}).get("lot_phrase")),
            "Очередь Steam-подарков",
        ),
        "app-route": (
            mapped and controls.get("app-route", True),
            mapped,
            "Маршрутизация поставщикам",
        ),
        "offline": (
            bool(guard_count) and controls.get("offline", True),
            bool(guard_count),
            "Коды Steam Guard по maFile",
        ),
        "auto-dump": (dump and controls.get("auto-dump", True), dump, "Контроль цен лотов"),
        "auto-robux": (
            False,
            all(
                snapshot["auto_robux"].get(key) for key in ("cookie", "universe_id", "game_pass_id")
            ),
            "Проверка цены · без автопокупки",
        ),
        "responder": (replies.get("responder", False), True, "Ответы на сообщения"),
        "auto-ticket": (
            replies.get("auto_ticket", False),
            bool(replies.get("ticket_templates")),
            "Обработка арбитража",
        ),
        "reviews": (replies.get("auto_thanks", False), True, "Ответы на отзывы"),
    }
    ks = snapshot["kosell"]
    states["kosell"] = (
        ks.get("enabled", False),
        bool(ks.get("api_key"))
        and (kosell_verified if ks.get("only_verified", True) else "kosell" in bound_kinds),
        "Аренда через KoSell · отдельный поставщик",
    )
    result = [
        {
            "id": ident,
            "enabled": bool(enabled),
            "configured": bool(ready),
            "available": ident != "auto-robux",
            "status": status,
        }
        for ident, (enabled, ready, status) in states.items()
        if ident in CATALOG_IDS
    ]
    for kind in sorted(KINDS & CATALOG_IDS):
        data = snapshot[f"integrated:{kind}"]
        ready = (
            credentials_ready(kind, data)
            and (kind == "support-tickets" or kind in bound_kinds)
            and (kind != "steam-rent" or rent_stock)
        )
        result.append(
            {
                "id": kind,
                "enabled": bool(data.get("enabled")),
                "configured": bool(ready),
                "available": True,
                "status": "Адаптированный плагин",
            }
        )
    control = snapshot["kosell-rent"]
    ready, active = False, False
    if control.get("account_id"):
        ident = control["account_id"]
        prefix = f"kosell-rent:state:{ident}:"
        async with Session() as db:
            active = bool(
                await db.scalar(
                    select(Account.id).where(Account.id == ident, Account.is_active.is_(True))
                )
            )
            records = (
                await db.scalars(
                    select(Settings).where(
                        Settings.key.in_(
                            [prefix + "settings.json", prefix + "mappings.json"],
                        )
                    )
                )
            ).all()
        saved = {
            row.key: json.loads(vault.decrypt(json.loads(row.value)["encrypted"]))
            for row in records
        }
        ready = bool(
            active
            and saved.get(prefix + "settings.json", {}).get("api_key")
            and saved.get(prefix + "mappings.json")
        )
    result.append(
        {
            "id": "kosell-rent",
            "enabled": bool(control.get("enabled") and active),
            "configured": ready,
            "available": True,
            "status": "KOSellRent 1.1.52 · исходный плагин",
        }
    )
    return result


async def toggle(ident: str, enabled: bool):
    if ident == "kosell-rent":
        from . import kosellrent

        return await kosellrent.toggle(enabled)
    from .integrated_schemas import KINDS

    if ident in KINDS:
        if ident not in CATALOG_IDS:
            raise KeyError(ident)
        from .integrated_config import config_lock, configured, stored_config

        async with config_lock:
            settings = await stored_config(ident)
            if enabled and not await configured(ident, settings):
                raise ValueError("Сначала заполните настройки плагина и привяжите лоты")
            settings["enabled"] = enabled
            await save_setting(f"integrated:{ident}", settings)
        return {
            "id": ident,
            "enabled": enabled,
            "configured": await configured(ident, settings),
            "available": True,
        }
    if ident not in IDS or ident not in CATALOG_IDS:
        raise KeyError(ident)
    async with control_lock:
        state = next(row for row in await catalog() if row["id"] == ident)
        if not state["available"]:
            raise ValueError("Для этого модуля доступна только проверка геймпасса")
        if enabled and not state["configured"]:
            if ident == "auto-dump":
                raise ValueError("Сначала добавьте и включите хотя бы одно правило")
            if ident == "offline":
                raise ValueError("Сначала добавьте аккаунт Steam с maFile")
            raise ValueError("Сначала настройте плагин")
        if ident == "kosell":
            from .kosell import claim_lock

            async with claim_lock:
                settings = await get_setting("kosell", {})
                settings["enabled"] = enabled
                await save_setting("kosell", settings)
        elif ident in {"email-code", "app-route", "offline", "auto-dump"}:
            controls = await get_setting("plugin_controls", {})
            controls[ident] = enabled
            await save_setting("plugin_controls", controls)
            if ident == "email-code":
                await email_codes.stop_mailbox()
                if enabled:
                    await email_codes.start_saved_mailbox()
        elif ident == "lzt":
            settings = await get_setting(lzt_market.SETTINGS_KEY, {})
            settings["enabled"] = enabled
            await lzt_market.worker.stop()
            await save_setting(lzt_market.SETTINGS_KEY, settings)
            if enabled:
                await lzt_market.worker.start()
        elif ident in {"discord-boost", "steam-gift"}:
            settings = await get_setting("fulfillment_plugins", {})
            key = "discord_boost" if ident == "discord-boost" else "steam_gift"
            other = "steam_gift" if key == "discord_boost" else "discord_boost"
            if enabled and settings.get(other, {}).get("enabled"):
                phrase = settings[key]["lot_phrase"].casefold()
                other_phrase = settings[other].get("lot_phrase", "").casefold()
                if phrase in other_phrase or other_phrase in phrase:
                    raise ValueError("Фразы лотов пересекаются; укажите разные товары")
            settings.setdefault(key, {})["enabled"] = enabled
            await save_setting("fulfillment_plugins", settings)
        else:
            settings = {**PluginSettings().model_dump(), **(await get_setting("plugins", {}))}
            key = {
                "responder": "responder",
                "auto-ticket": "auto_ticket",
                "reviews": "auto_thanks",
            }[ident]
            settings[key] = enabled
            await save_setting("plugins", settings)
            if ident == "responder" and not enabled:
                from .automation_plugins import responder

                await responder.stop()
        return next(row for row in await catalog() if row["id"] == ident)
