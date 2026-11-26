"""Separate KoSell workspace. No purchase endpoint is exposed to the browser."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import kosell
from .db import Session
from .integrated_config import unpacked
from .models import AppRouteMapping, KosellRental, Lot, Order, PluginBinding
from .security import require_auth, vault
from .services import get_setting, save_setting

router = APIRouter(prefix="/api/plugins/kosell", dependencies=[Depends(require_auth)])


class Config(BaseModel):
    api_key: str = Field(default="", max_length=500)
    currency: Literal["RUB", "USD"] = "RUB"
    only_verified: bool = True
    auto_hide: bool = True
    auto_delivery: bool = True
    review_bonus: bool = False
    allow_loss: bool = False
    balance_threshold: float = Field(default=0, ge=0, le=10000000, allow_inf_nan=False)
    balance_currency: Literal["RUB", "USD"] = "RUB"
    hide_low_balance: bool = False
    account_scope: Literal["all", "selected"] = "all"
    account_ids: list[int] = Field(default_factory=list, max_length=1000)
    stock_interval: int = Field(default=60, ge=10, le=3600)
    game_intervals: dict[str, int] = Field(default_factory=dict)


class Binding(BaseModel):
    lot_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    hours: int = Field(ge=1, le=8760)
    is_active: bool = True


class Attach(BaseModel):
    uid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class Activation(BaseModel):
    enabled: bool


async def supplier(call):
    try:
        return await call
    except kosell.KoSellError as error:
        raise HTTPException(429 if error.status == 429 else 400, str(error)) from None


@router.get("")
async def status():
    async with Session() as db:
        bindings = (
            await db.execute(
                select(PluginBinding, Lot).join(Lot).where(PluginBinding.kind == kosell.ID)
            )
        ).all()
        rentals = (
            await db.execute(
                select(KosellRental, Order).join(Order).order_by(KosellRental.id.desc()).limit(100)
            )
        ).all()
    return {
        "settings": await kosell.settings(),
        "catalog": await get_setting("kosell:catalog", {"items": [], "updated_at": None}),
        "balance": await get_setting("kosell:balance", None),
        "bindings": [
            {
                "id": b.id,
                "lot_id": b.lot_id,
                "account_id": lot.account_id,
                "title": lot.title,
                "price": lot.price,
                "product_id": int(b.service_id),
                "hours": b.quantity,
                "verified": b.provider_id == "verified",
                "is_active": b.is_active,
            }
            for b, lot in bindings
        ],
        "rentals": [
            {
                "id": r.id,
                "order_id": o.funpay_order_id,
                "buyer": o.buyer_name,
                "uid": r.kosell_uid,
                "status": r.status,
                "end_time": r.end_time,
                "last_error": r.last_error,
            }
            for r, o in rentals
        ],
    }


@router.put("/settings")
async def save_config(body: Config):
    async with kosell.claim_lock:
        current = await get_setting(kosell.ID, {})
        data = body.model_dump()
        if body.account_scope == "selected" and not body.account_ids:
            raise HTTPException(400, "Выберите хотя бы один аккаунт FunPay")
        if any(
            not key.isdigit() or not 10 <= value <= 3600
            for key, value in body.game_intervals.items()
        ):
            raise HTTPException(400, "Интервалы игр: от 10 до 3600 секунд")
        key = data.pop("api_key").strip()
        if key and any(c.isspace() for c in key):
            raise HTTPException(400, "API-ключ не должен содержать пробелы")
        changed = bool(
            key and (not current.get("api_key") or vault.decrypt(current["api_key"]) != key)
        )
        if changed:
            current["enabled"] = False
            async with Session() as db:
                for binding in (
                    await db.scalars(select(PluginBinding).where(PluginBinding.kind == kosell.ID))
                ).all():
                    binding.provider_id = ""
                await db.commit()
            await save_setting("kosell:catalog", {"items": [], "updated_at": None})
            await save_setting("kosell:balance", None)
        await save_setting(
            kosell.ID,
            {
                **current,
                **data,
                "api_key": vault.encrypt(key) if key else current.get("api_key", ""),
            },
        )
    return await kosell.settings()


@router.post("/refresh")
async def refresh():
    balance = await supplier(kosell.api("GET", "/account/balance"))
    if not isinstance(balance, dict) or not all(
        k in balance for k in ("balance_rub", "balance_usd")
    ):
        raise HTTPException(400, "KoSell не вернул баланс")
    await supplier(kosell.products())
    safe = {k: balance.get(k) for k in ("balance_rub", "balance_usd", "username")}
    await save_setting("kosell:balance", safe)
    await kosell.log("Подключение проверено, баланс и каталог обновлены")
    return safe


async def validate_binding(body, db):
    lot = await db.get(Lot, body.lot_id)
    if not lot:
        raise HTTPException(404, "Лот не найден")
    if await db.scalar(
        select(AppRouteMapping.id).where(
            AppRouteMapping.account_id == lot.account_id,
            AppRouteMapping.funpay_item_id == lot.funpay_lot_id,
        )
    ):
        raise HTTPException(409, "Этот лот уже привязан к AppRoute")
    items = await supplier(kosell.products())
    product = next((p for p in items if p["id"] == body.product_id), None)
    if not product or not product["min_hours"] <= body.hours <= product["max_hours"]:
        raise HTTPException(400, "Товар отсутствует или срок не соответствует ограничениям KoSell")
    result = await supplier(kosell.price(body.product_id, body.hours))
    from decimal import Decimal

    if product["available_accounts"] <= 0 or (
        not (await kosell.settings())["allow_loss"]
        and Decimal(str(result["total_rub"])) > lot.price
    ):
        raise HTTPException(
            400, "Нет остатка или закупка дороже продажи. Исправьте цену/срок и проверьте снова"
        )


@router.post("/bindings")
@router.put("/bindings/{ident}")
async def save_binding(body: Binding, ident: int | None = None):
    async with kosell.claim_lock:
        async with Session() as db:
            row = await db.get(PluginBinding, ident) if ident else None
            if ident and (not row or row.kind != kosell.ID):
                raise HTTPException(404, "Привязка не найдена")
            await validate_binding(body, db)
            if row is None:
                row = PluginBinding(kind=kosell.ID)
                db.add(row)
            row.lot_id, row.quantity, row.service_id = body.lot_id, body.hours, str(body.product_id)
            row.is_active, row.provider_id = body.is_active, "verified"
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise HTTPException(
                    409, "Лот уже используется другим плагином или привязкой"
                ) from None
            return {"id": row.id, "verified": True}


@router.delete("/bindings/{ident}")
async def remove_binding(ident: int):
    async with kosell.claim_lock:
        async with Session() as db:
            row = await db.get(PluginBinding, ident)
            if not row or row.kind != kosell.ID:
                raise HTTPException(404, "Привязка не найдена")
            # Preserve running rentals when their future-sales mapping is removed.
            for rental in (
                await db.scalars(select(KosellRental).where(KosellRental.binding_id == ident))
            ).all():
                rental.binding_id = None
            await db.delete(row)
            await db.commit()
            await save_setting(f"kosell:binding:{ident}", {})
            from .kosell_sync import manual_override

            await manual_override(row.lot_id)
    return {"ok": True}


@router.put("/bindings/{ident}/enabled")
async def binding_enabled(ident: int, body: Activation):
    async with kosell.claim_lock:
        async with Session() as db:
            row = await db.get(PluginBinding, ident)
            if not row or row.kind != kosell.ID:
                raise HTTPException(404, "Привязка не найдена")
            if body.enabled:
                await validate_binding(
                    Binding(lot_id=row.lot_id, product_id=int(row.service_id), hours=row.quantity),
                    db,
                )
                row.provider_id = "verified"
            row.is_active = body.enabled
            await db.commit()
    return {"enabled": body.enabled}


@router.post("/rentals/{ident}/attach")
async def attach(ident: int, body: Attach):
    async with kosell.claim_lock:
        async with Session() as db:
            rental = await db.get(KosellRental, ident)
            if not rental or rental.status != "MANUAL_CHECK":
                raise HTTPException(409, "Только аренда MANUAL_CHECK допускает сверку UID")
            payload = unpacked(rental.payload)
            active = await supplier(kosell.api("GET", "/rental/active", key=payload["key"]))
            match = (
                next((r for r in active if r.get("rental_uid") == body.uid), None)
                if isinstance(active, list)
                else None
            )
            if (
                not match
                or match.get("product_id") != payload["product_id"]
                or match.get("duration_hours") != payload["hours"]
            ):
                raise HTTPException(
                    400, "UID не найден среди активных аренд этого ключа с нужным товаром и сроком"
                )
            # Operator confirms association, API checks ownership and parameters. No debit.
            rental.kosell_uid, rental.end_time = body.uid, kosell.end_time(match["expires_at"])
            payload["delivery"] = bool(match.get("delivery"))
            from .integrated_config import packed

            rental.payload, rental.status, rental.last_error = packed(payload), "PREPARING", None
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise HTTPException(409, "UID уже привязан к другому заказу") from None
    await kosell.log(f"Аренда #{ident}: оператор подтвердил UID {body.uid}")
    return {"ok": True}
