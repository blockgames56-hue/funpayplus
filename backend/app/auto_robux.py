"""Game pass price verification; legacy web purchase API is no longer supported."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import httpx

from .security import vault
from .services import get_setting, save_setting


def price_for_target(amount: int, cover_fee: bool) -> int:
    if amount <= 0:
        raise ValueError("Количество Robux должно быть положительным")
    if not cover_fee:
        return amount
    return int((Decimal(amount) / Decimal("0.70")).to_integral_value(rounding=ROUND_CEILING))


def estimated_net(price: int) -> int:
    return int((Decimal(price) * Decimal("0.70")).to_integral_value(rounding=ROUND_FLOOR))


async def public_settings() -> dict:
    saved = await get_setting("auto_robux", {})
    return {
        "universe_id": saved.get("universe_id", 0),
        "game_pass_id": saved.get("game_pass_id", 0),
        "target_amount": saved.get("target_amount", 100),
        "cover_fee": saved.get("cover_fee", True),
        "has_cookie": bool(saved.get("cookie")),
    }


async def configure(data: dict) -> dict:
    old = await get_setting("auto_robux", {})
    cookie = data.pop("cookie", "")
    if cookie:
        if not cookie.startswith("_|WARNING:-DO-NOT-SHARE"):
            raise ValueError("Неверный формат .ROBLOSECURITY")
        data["cookie"] = vault.encrypt(cookie)
    else:
        data["cookie"] = old.get("cookie", "")
    await save_setting("auto_robux", data)
    return await public_settings()


async def verify_pass() -> dict:
    settings = await get_setting("auto_robux", {})
    if not settings.get("cookie"):
        raise ValueError("Сначала сохраните cookie Roblox")
    universe_id = int(settings.get("universe_id", 0))
    pass_id = int(settings.get("game_pass_id", 0))
    if universe_id <= 0 or pass_id <= 0:
        raise ValueError("Укажите ID мира и геймпасса")
    url = f"https://apis.roblox.com/game-passes/v1/universes/{universe_id}/game-passes/{pass_id}/creator"
    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            cookies={".ROBLOSECURITY": vault.decrypt(settings["cookie"])},
        ) as http:
            response = await http.get(url)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ValueError("Roblox не подтвердил геймпасс или сессию") from error
    if not isinstance(data, dict):
        raise ValueError("Roblox вернул неожиданные данные")
    price = data.get("priceInRobux") or data.get("price")
    if isinstance(data.get("sales"), dict):
        price = data["sales"].get("priceInRobux", price)
    if not isinstance(price, int) or price <= 0:
        raise ValueError("Roblox не вернул однозначную цену геймпасса")
    expected = price_for_target(int(settings["target_amount"]), bool(settings["cover_fee"]))
    return {
        "game_pass_id": pass_id,
        "name": str(data.get("name", ""))[:200],
        "actual_price": price,
        "expected_price": expected,
        "estimated_net": estimated_net(price),
        "matches": price == expected,
        "purchase_available": False,
    }
