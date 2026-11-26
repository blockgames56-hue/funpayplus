"""Authenticated web controls for the original KOSellRent engine."""

import asyncio
import copy
import json
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from . import kosellrent as runtime
from .db import Session
from .kosellrent_schema import metadata, validate_settings
from .kosellrent_source import load_source
from .models import Account, AppRouteMapping, Lot, PluginBinding, Settings
from .security import require_auth, vault
from .services import accounts, get_setting, save_setting

router = APIRouter(prefix="/api/plugins/kosell-rent", dependencies=[Depends(require_auth)])
definitions = load_source()
operation_task = None
batch_lock = asyncio.Lock()


class Config(BaseModel):
    account_id: int = Field(gt=0)
    settings: dict = Field(default_factory=dict)


class Mapping(BaseModel):
    lot_id: int = Field(gt=0)
    product_id: int = Field(gt=0)
    hours: int = Field(ge=1, le=8760)
    currency: Literal["RUB", "USD"] = "RUB"
    title_keyword: str = Field(default="", max_length=200)


class MappingEdit(BaseModel):
    product_id: int = Field(gt=0)
    hours: int = Field(ge=1, le=8760)
    currency: Literal["RUB", "USD"] = "RUB"


class MappingSelection(BaseModel):
    lot_ids: list[int] = Field(min_length=1, max_length=10000)


class PlanOptions(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    durations: list[int] | None = Field(default=None, min_length=1, max_length=100)

    @classmethod
    def validate_hours(cls, values):
        if values is not None and any(type(h) is not int or not 1 <= h <= 8760 for h in values):
            raise ValueError("Сроки аренды: целые часы от 1 до 8760")
        return values


class PricePage(BaseModel):
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=6, ge=1, le=12)
    competitors: bool = True


class OperationOptions(BaseModel):
    product_id: int | None = Field(default=None, gt=0)


def idle():
    if operation_task and not operation_task.done():
        raise HTTPException(409, "Дождитесь завершения операции или остановите её")


async def host_required():
    _, host = await runtime.selected()
    if not host:
        raise HTTPException(400, "Сначала выберите активный аккаунт FunPay и сохраните настройки")
    return host


def placeholders(text):
    return sorted(set(re.findall(r"\{[A-Za-z_][A-Za-z_0-9]*\}", text)))


@router.get("")
async def workspace():
    control = await get_setting(runtime.ID, {})
    host = runtime.instances.get(control.get("account_id"))
    if not host and control.get("account_id"):
        async with runtime.lock:
            host = await runtime.host_for(control["account_id"])
    module = host.module if host else definitions
    settings = copy.deepcopy(
        module.SETTINGS
        or {
            **module.DEFAULT_SETTINGS,
            "texts": module.DEFAULT_TEXTS,
        }
    )
    has_key = bool(settings.pop("api_key", ""))
    settings["enabled"] = bool(control.get("enabled"))
    async with Session() as db:
        account_rows = (await db.scalars(select(Account).order_by(Account.id))).all()
        lots = (
            await db.scalars(select(Lot).where(Lot.account_id == control.get("account_id", -1)))
        ).all()
    settings["enabled"] = settings["enabled"] and any(
        a.id == control.get("account_id") and a.is_active for a in account_rows
    )
    return {
        "name": module.NAME,
        "version": module.VERSION,
        "credits": module.CREDITS,
        "source_sha256": module.source_sha256,
        "account_id": control.get("account_id"),
        "settings": settings,
        "has_api_key": has_key,
        "accounts": [{"id": a.id, "title": a.title, "active": a.is_active} for a in account_rows],
        "lots": [
            {"id": lot.id, "funpay_lot_id": lot.funpay_lot_id, "title": lot.title} for lot in lots
        ],
        "groups": metadata(module),
        "texts": [
            {
                "key": key,
                "label": module.TEXT_LABELS.get(key, key),
                "default": text,
                "placeholders": placeholders(text),
            }
            for key, text in module.DEFAULT_TEXTS.items()
        ],
        "mappings": copy.deepcopy(module.MAPPINGS),
        "rentals": [
            dict(r, buyer_key=buyer)
            for buyer, rows in copy.deepcopy(module.RENTALS).items()
            for r in rows
        ],
        "hidden": copy.deepcopy(module.HIDDEN),
        "stats": copy.deepcopy(module.STATS),
        "balance": copy.deepcopy(module._bal_cache.get("data")),
        "products": copy.deepcopy(module._products_cache.get("data") or []),
        "operation": copy.deepcopy(module._ac_running),
        "plan": copy.deepcopy((module._ac_plan.get(1) or {}).get("items", [])),
        "help": runtime.clean_html(module.PLUGIN_HELP_TEXT),
        "lot_help": runtime.clean_html(module.AC_PLACEHOLDER_HINT),
    }


@router.put("/settings")
async def save_config(body: Config):
    idle()
    try:
        updates = validate_settings(body.settings, definitions)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    async with runtime.lock:
        idle()
        async with Session() as db:
            account = await db.get(Account, body.account_id)
        if not account or not account.is_active:
            raise HTTPException(400, "Выберите активный аккаунт FunPay")
        control = await get_setting(runtime.ID, {})
        if control.get("enabled") and control.get("account_id") != body.account_id:
            raise HTTPException(409, "Перед сменой аккаунта выключите плагин")
        previous_id = control.get("account_id")
        if previous_id and previous_id != body.account_id:
            previous = await runtime.host_for(previous_id)
            if previous.module.PENDING_QTY or any(
                r.get("expires_ts", 0) > time.time()
                for rows in previous.module.RENTALS.values()
                for r in rows
            ):
                raise HTTPException(
                    409,
                    "У текущего аккаунта есть действующие аренды или ожидающие заказы. Смените аккаунт после их завершения",
                )
        host = await runtime.host_for(body.account_id)
        module = host.module
        if updates.get("api_key") and updates["api_key"] != module.SETTINGS.get("api_key"):
            active = any(
                r.get("expires_ts", 0) > time.time()
                for rows in module.RENTALS.values()
                for r in rows
            )
            if active:
                raise HTTPException(409, "Нельзя менять ключ поставщика при действующих арендах")
            control["enabled"] = False
            module._products_cache.update(data=None, ts=0)
            module._bal_cache.update(data=None, ts=0)
        module.SETTINGS.update(updates)
        if "texts" in updates:
            module.SETTINGS["texts"] = {**module.DEFAULT_TEXTS, **module.SETTINGS["texts"]}
        if "ac_min_price" in updates and module.SETTINGS.get("ac_template"):
            module.SETTINGS["ac_template"]["min_price"] = updates["ac_min_price"]
        module._price_cache.clear()
        module.SETTINGS["enabled"] = bool(control.get("enabled"))
        module._ac_plan.clear()
        await runtime.run_sync(module.save_settings)
        await save_setting(runtime.ID, {**control, "account_id": body.account_id})
    return {"ok": True}


@router.post("/refresh")
async def refresh():
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        api = host.module._get_api()
        if not api:
            raise HTTPException(400, "Сохраните API-ключ KOSell")
        balance = await runtime.run_sync(api.balance)
        products = await runtime.run_sync(host.module._products, api, force=True)
        if balance is None or products is None or host.module._products_cache.get("err"):
            raise HTTPException(
                400, "KOSell не подтвердил подключение. Проверьте API-ключ и журнал"
            )
        host.module._bal_cache.update(data=balance, ts=time.time())
        return {"ok": True}


@router.post("/lots/sync")
async def sync_lots():
    host = await host_required()
    await accounts.sync_lots(host.account_id)
    return {"ok": True}


@router.post("/mappings")
async def save_mapping(body: Mapping):
    idle()
    async with runtime.lock:
        idle()
        return await _save_mapping(body, await host_required())


async def _save_mapping(body: Mapping, host):
    module = host.module
    async with Session() as db:
        lot = await db.get(Lot, body.lot_id)
        if not lot or lot.account_id != host.account_id:
            raise HTTPException(400, "Лот принадлежит другому аккаунту или удалён")
        binding = await db.scalar(select(PluginBinding).where(PluginBinding.lot_id == lot.id))
        route = await db.scalar(
            select(AppRouteMapping.id).where(
                AppRouteMapping.account_id == lot.account_id,
                AppRouteMapping.funpay_item_id == lot.funpay_lot_id,
            )
        )
        if route or (binding and binding.kind != runtime.ID):
            raise HTTPException(409, "Лот уже используется другим плагином")
        api = module._get_api()
        if not api:
            raise HTTPException(400, "Сохраните API-ключ")
        products = await runtime.run_sync(module._products, api)
        product = next(
            (p for p in products or [] if str(p.get("id")) == str(body.product_id)), None
        )
        if not product:
            raise HTTPException(400, "Товар не найден в каталоге KOSell")
        original_mapping = next(
            (m for m in module.MAPPINGS if str(m.get("lot_id")) == lot.funpay_lot_id), {}
        )
        mapping = {
            **original_mapping,
            "lot_id": lot.funpay_lot_id,
            "subcategory_id": lot.node_id,
            "product_id": body.product_id,
            "product_name": product.get("name", ""),
            "hours": body.hours,
            "currency": body.currency,
            "title_keyword": body.title_keyword or lot.title[:40],
            "rent_code": original_mapping.get("rent_code")
            or module._make_rent_code(body.product_id, body.hours),
        }
        if module._autolot_block_reason(mapping):
            raise HTTPException(400, "Этот товар исключён автором исходного плагина")
        previous = copy.deepcopy(module.MAPPINGS)
        module.MAPPINGS[:] = [
            m for m in module.MAPPINGS if str(m.get("lot_id")) != lot.funpay_lot_id
        ]
        module.MAPPINGS.append(mapping)
        if not binding:
            binding = PluginBinding(kind=runtime.ID, lot_id=lot.id)
            db.add(binding)
        binding.service_id, binding.quantity = str(body.product_id), body.hours
        binding.is_active = True
        try:
            # Both writes share one database transaction, including encrypted source state.
            row = await db.get(Settings, host.key(module.MAPPINGS_FILE))
            encrypted = {
                "encrypted": vault.encrypt(json.dumps(module.MAPPINGS, ensure_ascii=False))
            }
            if row:
                row.value = json.dumps(encrypted)
            else:
                db.add(Settings(key=host.key(module.MAPPINGS_FILE), value=json.dumps(encrypted)))
            await db.commit()
            host.state["mappings.json"] = copy.deepcopy(module.MAPPINGS)
        except Exception:
            module.MAPPINGS[:] = previous
            raise
    return {"ok": True}


@router.delete("/mappings/{lot_id}")
async def remove_mapping(lot_id: int):
    return await delete_selected(MappingSelection(lot_ids=[lot_id]))


@router.put("/mappings/{lot_id}")
async def edit_mapping(lot_id: int, body: MappingEdit):
    async with runtime.lock:
        idle()
        host = await host_required()
        existing = next(
            (m for m in host.module.MAPPINGS if str(m.get("lot_id")) == str(lot_id)), None
        )
        if not existing:
            raise HTTPException(404, "Привязка не найдена")
        async with Session() as db:
            lot = await db.scalar(
                select(Lot).where(
                    Lot.account_id == host.account_id, Lot.funpay_lot_id == str(lot_id)
                )
            )
        if not lot:
            await accounts.sync_lots(host.account_id)
            async with Session() as db:
                lot = await db.scalar(
                    select(Lot).where(
                        Lot.account_id == host.account_id, Lot.funpay_lot_id == str(lot_id)
                    )
                )
        if not lot:
            raise HTTPException(409, "Лот не найден на FunPay. Обновите список лотов")
        local_id = lot.id
        keyword = existing.get("title_keyword", "")
        return await _save_mapping(
            Mapping(lot_id=local_id, title_keyword=keyword, **body.model_dump()), host
        )


@router.post("/mappings/delete-selected")
async def delete_selected(body: MappingSelection):
    idle()
    if any(lot_id <= 0 for lot_id in body.lot_ids):
        raise HTTPException(400, "Некорректные ID лотов")
    async with runtime.lock:
        idle()
        host = await host_required()
        module = host.module
        ids = {str(ident) for ident in body.lot_ids}
        mappings = [m for m in module.MAPPINGS if str(m.get("lot_id")) not in ids]
        hidden = {key: value for key, value in module.HIDDEN.items() if key not in ids}
        removed = len(module.MAPPINGS) - len(mappings)
        async with Session() as db:
            for path, value in ((module.MAPPINGS_FILE, mappings), (module.HIDDEN_FILE, hidden)):
                key = host.key(path)
                row = await db.get(Settings, key)
                encrypted = json.dumps(
                    {"encrypted": vault.encrypt(json.dumps(value, ensure_ascii=False))}
                )
                if row:
                    row.value = encrypted
                else:
                    db.add(Settings(key=key, value=encrypted))
            lots = select(Lot.id).where(
                Lot.account_id == host.account_id, Lot.funpay_lot_id.in_(ids)
            )
            await db.execute(
                delete(PluginBinding).where(
                    PluginBinding.kind == runtime.ID, PluginBinding.lot_id.in_(lots)
                )
            )
            await db.commit()
        module.MAPPINGS[:] = mappings
        module.HIDDEN.clear()
        module.HIDDEN.update(hidden)
        module._ac_plan.clear()
        host.state["mappings.json"] = copy.deepcopy(mappings)
        host.state["hidden.json"] = copy.deepcopy(hidden)
    return {"ok": True, "removed": removed}


@router.post("/lots/{lot_id}/{action}")
async def lot_action(lot_id: int, action: Literal["hide", "restore"]):
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        if not any(str(m.get("lot_id")) == str(lot_id) for m in host.module.MAPPINGS):
            raise HTTPException(404, "Привязка не найдена")
        function = host.module._hide_lot if action == "hide" else host.module._restore_lot
        args = (lot_id, "Вручную из приложения") if action == "hide" else (lot_id,)
        await runtime.run_sync(function, *args)
        hidden = str(lot_id) in host.module.HIDDEN
        if (action == "hide" and not hidden) or (action == "restore" and hidden):
            raise HTTPException(
                400, "FunPay не подтвердил изменение состояния лота. Проверьте журнал"
            )
    return {"ok": True}


@router.post("/plan")
async def plan(body: PlanOptions | None = None):
    idle()
    options = body or PlanOptions()
    try:
        PlanOptions.validate_hours(options.durations)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    async with runtime.lock:
        idle()
        host = await host_required()
        if not host.module._ac_has_template():
            raise HTTPException(400, "Сначала сохраните шаблон автосоздания лотов")
        rows = await runtime.run_sync(
            host.module._build_plan, 1, product_id=options.product_id, durations=options.durations
        )
    return {"count": len(rows)}


@router.post("/prices")
async def preview_prices(body: PricePage):
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        module = host.module
        current = module._ac_plan.get(1) or {}
        items = current.get("items") or []
        if not items or time.time() - current.get("ts", 0) > 900:
            raise HTTPException(400, "Сначала обновите план создания лотов")
        supplier = module._get_api()
        if not supplier:
            raise HTTPException(400, "Сохраните API-ключ KOSell")

        def calculate():
            products = module._products(supplier)
            if products is None or module._products_cache.get("err"):
                raise HTTPException(400, "KOSell не вернул каталог товаров")
            by_id = {int(p["id"]): p for p in products}
            result = []
            for item in items[body.offset : body.offset + body.limit]:
                product = by_id.get(item["product_id"], {"id": item["product_id"]})
                cost = module._kosell_cost_rub(supplier, product, item["hours"])
                public_price = module._public_price_rub(
                    supplier, product, item["hours"], module._ac_tpl()
                )
                competitors = (
                    module._fp_lowest_prices_for(
                        item["subcategory_id"],
                        item["hours"],
                        item["game_name"] if item.get("source") == "misc" else None,
                        5,
                    )
                    if body.competitors
                    else []
                )
                result.append(
                    {
                        **item,
                        "cost_rub": cost,
                        "public_rub": public_price,
                        "competitor_prices": competitors,
                    }
                )
            return result

        result = await runtime.run_sync(calculate)
        offset = body.offset + len(result)
        return {
            "items": result,
            "total": len(items),
            "next_offset": offset if offset < len(items) else None,
        }


@router.post("/diagnostics")
async def diagnose_map():
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        text = await runtime.run_sync(host.module._ac_diagnostics_text)
    return {"text": runtime.clean_html(text)}


@router.post("/refresh-map")
async def refresh_map():
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        module = host.module

        def refresh_all():
            data = module._fetch_map(force=True)
            module._misc_cache.clear()
            module._products_cache.update(data=None, ts=0, err=False)
            for cache in (
                module._pub_lots_cache,
                module._commission_cache,
                module._node_form_cache,
                module._price_cache,
            ):
                cache.clear()
            products = module._products(module._get_api(), force=True)
            return data, products

        data, products = await runtime.run_sync(refresh_all)
        module._ac_plan.clear()
        if products is None or module._products_cache.get("err"):
            raise HTTPException(400, "Каталог KOSell недоступен. Проверьте подключение")
        return {
            "games": len((data or {}).get("games", [])),
            "products": len(products),
            "cached": bool(module._map_cache.get("warned")),
        }


@router.delete("/mappings")
async def clear_mappings():
    idle()
    async with runtime.lock:
        idle()
        host = await host_required()
        module = host.module
        count = len(module.MAPPINGS)
        module.MAPPINGS.clear()
        module.HIDDEN.clear()
        module._ac_plan.clear()
        await runtime.run_sync(module.save_mappings)
        await runtime.run_sync(module.save_hidden)
        await sync_registry(host)
    return {"removed": count}


async def perform(host, action, product_id=None):
    try:
        # Long Cardinal lot workers run independently of new-order/message handlers.
        # Configuration stays protected by operation_task + the short claim lock.
        async with batch_lock:
            function = {
                "create": host.module._autocreate_run,
                "reprice": host.module._reprice_thread_entry,
                "delete": host.module._delete_plugin_run,
            }[action]
            if action == "reprice" and product_id is not None:
                await runtime.run_sync(function, 1, product_id=product_id)
            else:
                await runtime.run_sync(function, 1)
            await accounts.sync_lots(host.account_id)
            await sync_registry(host)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await runtime.bus.log(
            "plugins",
            f"Операция KOSell Rent: {type(error).__name__}",
            host.account_id,
            "error",
            source_plugin=runtime.ID,
        )
    finally:
        host.module._ac_running["on"] = False


async def sync_registry(host):
    """Reserve newly created source lots in the application's shared registry."""
    mappings = {str(m["lot_id"]): m for m in host.module.MAPPINGS if m.get("lot_id")}
    async with Session() as db:
        lots = (await db.scalars(select(Lot).where(Lot.account_id == host.account_id))).all()
        for lot in lots:
            binding = await db.scalar(select(PluginBinding).where(PluginBinding.lot_id == lot.id))
            mapping = mappings.get(lot.funpay_lot_id)
            if not mapping:
                if binding and binding.kind == runtime.ID:
                    await db.delete(binding)
                continue
            if binding and binding.kind != runtime.ID:
                continue
            if not binding:
                binding = PluginBinding(kind=runtime.ID, lot_id=lot.id)
                db.add(binding)
            binding.service_id = str(mapping["product_id"])
            binding.quantity = int(mapping["hours"])
            binding.is_active = True
        await db.commit()


@router.post("/operations/{action}")
async def operation(
    action: Literal["create", "reprice", "delete", "cancel"], body: OperationOptions | None = None
):
    global operation_task
    if runtime.stopping:
        raise HTTPException(
            409, "Локальный сервер останавливается. Подключитесь к работающему серверу"
        )
    if action == "cancel":
        host = await host_required()
        host.module._ac_running["cancel"] = True
        return {"ok": True}
    async with runtime.lock:
        idle()
        host = await host_required()
        module = host.module
        product_id = body.product_id if body else None
        if product_id is not None and action != "reprice":
            raise HTTPException(
                400, "Выбор игры применяется к пересчёту; создание использует сохранённый план"
            )
        if action == "create":
            current_plan = module._ac_plan.get(1) or {}
            if not current_plan.get("items") or time.time() - current_plan.get("ts", 0) > 900:
                raise HTTPException(400, "Сначала обновите и проверьте план создания лотов")
        if not module._ac_begin(
            {"create": "Создание лотов", "reprice": "Пересчёт цен", "delete": "Удаление лотов"}[
                action
            ]
        ):
            raise HTTPException(409, "Операция уже выполняется")
        operation_task = asyncio.create_task(
            perform(host, action, product_id), name="kosell-rent-operation"
        )
    return {"ok": True}


async def stop_operation():
    if operation_task and not operation_task.done():
        for host in runtime.instances.values():
            host.module._ac_running["cancel"] = True
        await asyncio.shield(operation_task)
