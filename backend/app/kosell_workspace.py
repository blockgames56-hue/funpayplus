"""KoSell lot studio, reusable texts and price-policy previews."""

import asyncio
import uuid
from datetime import timedelta
from decimal import ROUND_UP, Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import kosell
from .db import Session
from .integrated_config import unpacked
from .kosell_api import supplier
from .lot_editor import load_form, merge_fields, save_form
from .message_templates import MessageFormatter
from .models import (
    Account,
    KosellMessage,
    KosellOperation,
    KosellRental,
    Lot,
    Order,
    PluginBinding,
    Settings,
    utcnow,
)
from .security import require_auth
from .services import accounts, get_setting, save_setting

router = APIRouter(prefix="/api/plugins/kosell", dependencies=[Depends(require_auth)])
lock = asyncio.Lock()
execution_lock = asyncio.Lock()
tasks = {}
TAGS = (
    "game",
    "duration_hours",
    "duration_days",
    "duration_minutes",
    "duration_label",
    "cost",
    "currency",
    "stock",
    "product_id",
    "price",
    "markup_percent",
    "margin",
    "min_hours",
    "max_hours",
)
DEFAULT_TEXTS = {
    "title": "{game} — аренда на {duration_hours} ч",
    "description": "Временный доступ к {game} на {duration_label}. Данные аккаунта придут автоматически после оплаты. Для Steam Guard напишите !код. Используйте аккаунт только в течение оплаченного срока, не меняйте настройки безопасности и не передавайте доступ другим людям.",
    "payment": "Данные аккаунта придут автоматически в этот чат. Для Steam Guard напишите !код. Остаток времени: !time.",
    "title_en": "{game} — {duration_hours}h rental",
    "description_en": "Temporary access to {game} for {duration_hours} hours. Account details are delivered automatically after payment. Send !код for Steam Guard. Use the account only during the paid rental period. Do not change security settings or share the account.",
    "payment_en": "Account details will be delivered in this chat. Send !код for Steam Guard. Remaining time: !time.",
}


def sale_price(cost, markup):
    return (Decimal(str(cost)) * (1 + Decimal(str(markup)) / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_UP
    )


class Texts(BaseModel):
    title: str = Field(default=DEFAULT_TEXTS["title"], min_length=1, max_length=100)
    description: str = Field(default=DEFAULT_TEXTS["description"], max_length=20000)
    payment: str = Field(default=DEFAULT_TEXTS["payment"], max_length=5000)
    title_en: str = Field(default=DEFAULT_TEXTS["title_en"], max_length=100)
    description_en: str = Field(default=DEFAULT_TEXTS["description_en"], max_length=20000)
    payment_en: str = Field(default=DEFAULT_TEXTS["payment_en"], max_length=5000)


class Studio(Texts):
    source_lot_id: int = Field(gt=0)
    product_ids: list[int] = Field(min_length=1, max_length=100)
    hours: list[int] = Field(default=[24], min_length=1, max_length=12)
    currency: Literal["RUB"] = "RUB"
    price_mode: Literal["markup", "fixed"] = "markup"
    markup: float = Field(default=40, ge=0, le=10000, allow_inf_nan=False)
    fixed_price: float = Field(default=100, ge=1, le=10000000, allow_inf_nan=False)
    amount: int = Field(default=999999, ge=1, le=999999999)
    sources: dict[str, int] = Field(default_factory=dict)


class SavedTemplate(Texts):
    name: str = Field(min_length=1, max_length=100)
    hours: list[int] = Field(default=[24], min_length=1, max_length=12)
    price_mode: Literal["markup", "fixed"] = "markup"
    markup: float = Field(default=40, ge=0, le=10000, allow_inf_nan=False)
    fixed_price: float = Field(default=100, ge=1, le=10000000, allow_inf_nan=False)
    amount: int = Field(default=999999, ge=1, le=999999999)


class Policy(BaseModel):
    markup: float = Field(ge=0, le=10000, allow_inf_nan=False)
    auto_price: bool = True


class MarkupPreview(Policy):
    binding_ids: list[int] = Field(default_factory=list, max_length=500)


def validate_texts(body):
    for name in DEFAULT_TEXTS:
        text = getattr(body, name)
        if text:
            try:
                MessageFormatter.validate(
                    text,
                    TAGS,
                    max_length=100
                    if name.startswith("title")
                    else 20000
                    if name.startswith("description")
                    else 5000,
                )
            except ValueError as error:
                raise HTTPException(422, f"{name}: {error}") from None


@router.get("/workspace")
async def workspace():
    from .kosell_api import status

    data = await status()
    async with Session() as db:
        accounts_rows = (await db.scalars(select(Account))).all()
        policies = {
            r.key.removeprefix("kosell:binding:"): r.value
            for r in (
                await db.scalars(select(Settings).where(Settings.key.like("kosell:binding:%")))
            ).all()
        }
        import json

        policies = {k: json.loads(v) for k, v in policies.items()}
        for row in data["bindings"]:
            lot = await db.get(Lot, row["lot_id"])
            row.update(
                funpay_item_id=lot.funpay_lot_id,
                node_id=lot.node_id,
                lot_active=lot.is_active,
                policy=policies.get(str(row["id"]), {}),
            )
        rentals = (
            await db.execute(
                select(KosellRental, Order).join(Order).order_by(KosellRental.id.desc()).limit(100)
            )
        ).all()
        for public, (rental, order) in zip(data["rentals"], rentals):
            payload = unpacked(rental.payload)
            public.update(
                title=order.description,
                account_id=order.account_id,
                login=payload.get("login", ""),
                hours=payload.get("hours"),
                product_id=payload.get("product_id"),
                remaining=max(0, int((kosell.aware(rental.end_time) - utcnow()).total_seconds()))
                if rental.end_time
                else 0,
            )
            public["queue"] = [
                {"id": m.id, "kind": m.kind, "state": m.state, "attempts": m.attempts}
                for m in (
                    await db.scalars(
                        select(KosellMessage).where(KosellMessage.rental_id == rental.id)
                    )
                ).all()
            ]
        eligible = (
            await db.scalars(
                select(Order)
                .where(Order.status == "paid", Order.delivery_state == "pending")
                .order_by(Order.id.desc())
                .limit(100)
            )
        ).all()
        data["manual_orders"] = [
            {
                "id": o.id,
                "number": o.funpay_order_id,
                "buyer": o.buyer_name,
                "title": o.description,
                "account_id": o.account_id,
            }
            for o in eligible
        ]
        data["operations"] = [
            {
                "id": o.id,
                "rental_id": o.rental_id,
                "kind": o.kind,
                "state": o.state,
                "hours": o.hours,
            }
            for o in (
                await db.scalars(
                    select(KosellOperation).order_by(KosellOperation.id.desc()).limit(100)
                )
            ).all()
        ]
    data["accounts"] = [
        {"id": a.id, "username": a.username, "title": a.title, "is_active": a.is_active}
        for a in accounts_rows
    ]
    data["studio_defaults"] = DEFAULT_TEXTS
    data["studio_tags"] = TAGS
    data["finance"] = await finance()
    data["sync"] = await get_setting("kosell:sync", {})
    return data


async def finance():
    totals = {
        code: {"revenue": Decimal(0), "expenses": Decimal(0)} for code in ("RUB", "USD", "EUR")
    }
    unknown = 0
    async with Session() as db:
        rows = (await db.execute(select(KosellRental, Order).join(Order))).all()
        for r, o in rows:
            if o.status != "refunded" and r.kosell_uid and o.currency in totals:
                totals[o.currency]["revenue"] += o.sum
            receipt = unpacked(r.payload).get("receipt")
            if receipt and receipt["currency"] in totals:
                totals[receipt["currency"]]["expenses"] += Decimal(receipt["amount"])
            elif r.kosell_uid:
                unknown += 1
        for op in (await db.scalars(select(KosellOperation))).all():
            if op.state != "DONE":
                continue
            payload = unpacked(op.payload)
            receipt = payload.get("receipt")
            if receipt and receipt["currency"] in totals:
                totals[receipt["currency"]]["expenses"] += Decimal(receipt["amount"])
            else:
                unknown += 1
            order = await db.get(Order, op.order_id) if op.order_id else None
            if order and order.status != "refunded" and order.currency in totals:
                totals[order.currency]["revenue"] += order.sum
    return {
        "currencies": {code: {k: str(v) for k, v in row.items()} for code, row in totals.items()},
        "unconfirmed_costs": unknown,
    }


@router.get("/studio/templates")
async def templates():
    return await get_setting("kosell:studio_templates", [])


@router.post("/studio/templates")
@router.put("/studio/templates/{ident}")
async def save_template(body: SavedTemplate, ident: str | None = None):
    validate_texts(body)
    if any(h < 1 or h > 8760 for h in body.hours):
        raise HTTPException(400, "Срок: от 1 до 8760 часов")
    async with lock:
        rows = await templates()
        if ident and not any(r["id"] == ident for r in rows):
            raise HTTPException(404, "Шаблон не найден")
        if not ident and len(rows) >= 100:
            raise HTTPException(400, "Не более 100 шаблонов")
        ident = ident or str(uuid.uuid4())
        rows = [r for r in rows if r["id"] != ident] + [{"id": ident, **body.model_dump()}]
        await save_setting("kosell:studio_templates", rows)
    return {"id": ident}


@router.delete("/studio/templates/{ident}")
async def remove_template(ident: str):
    async with lock:
        rows = await templates()
        await save_setting("kosell:studio_templates", [r for r in rows if r["id"] != ident])
    return {"ok": True}


@router.post("/studio/preview")
async def studio_preview(body: Studio):
    validate_texts(body)
    if len(set(body.hours)) * len(set(body.product_ids)) > 120:
        raise HTTPException(400, "В одной партии не более 120 лотов")
    items = {p["id"]: p for p in await supplier(kosell.products())}
    plans = []
    async with Session() as db:
        for product_id in dict.fromkeys(body.product_ids):
            if len(body.product_ids) > 1 and not body.sources.get(str(product_id)):
                raise HTTPException(400, "Для каждой игры выберите исходный лот нужной категории")
            product = items.get(product_id)
            source = await db.get(Lot, body.sources.get(str(product_id), body.source_lot_id))
            if not source or not product:
                raise HTTPException(400, "Выберите существующий исходный лот и товар KoSell")
            account = await db.get(Account, source.account_id)
            if not account or not account.is_active:
                raise HTTPException(400, "Аккаунт исходного лота остановлен")
            for hours in dict.fromkeys(body.hours):
                if not product["min_hours"] <= hours <= product["max_hours"]:
                    raise HTTPException(
                        400,
                        f"{product['name']}: допустимо {product['min_hours']}–{product['max_hours']} ч",
                    )
                quote = await supplier(kosell.price(product_id, hours))
                cost = Decimal(str(quote["total_rub"]))
                price = (
                    sale_price(cost, body.markup)
                    if body.price_mode == "markup"
                    else Decimal(str(body.fixed_price)).quantize(Decimal(".01"))
                )
                if price < cost:
                    raise HTTPException(400, "Цена нового лота ниже подтверждённой закупки")
                values = {
                    "game": product["name"],
                    "duration_hours": hours,
                    "duration_days": round(hours / 24, 2),
                    "duration_minutes": hours * 60,
                    "duration_label": f"{hours} ч",
                    "cost": str(cost),
                    "currency": "RUB",
                    "stock": product["available_accounts"],
                    "product_id": product_id,
                    "price": str(price),
                    "markup_percent": body.markup,
                    "margin": str(price - cost),
                    "min_hours": product["min_hours"],
                    "max_hours": product["max_hours"],
                }
                texts = {
                    k: MessageFormatter.format(getattr(body, k), values) for k in DEFAULT_TEXTS
                }
                for name, text in texts.items():
                    limit = (
                        100
                        if name.startswith("title")
                        else 20000
                        if name.startswith("description")
                        else 5000
                    )
                    if len(text) > limit:
                        raise HTTPException(
                            400, f"Итоговый текст {name} превышает {limit} символов"
                        )
                plans.append(
                    {
                        "source_lot_id": source.id,
                        "account_id": source.account_id,
                        "node_id": source.node_id,
                        "product_id": product_id,
                        "game": product["name"],
                        "hours": hours,
                        "cost": str(cost),
                        "price": str(max(Decimal(1), price)),
                        "stock": product["available_accounts"],
                        "amount": body.amount,
                        "texts": texts,
                        "policy": {
                            "auto_price": body.price_mode == "markup",
                            "markup": body.markup,
                        },
                        "state": "planned",
                    }
                )
    ident = str(uuid.uuid4())
    batch = {
        "id": ident,
        "state": "preview",
        "created_at": utcnow().isoformat(),
        "expires_at": (utcnow() + timedelta(minutes=30)).isoformat(),
        "rows": plans,
    }
    await save_setting("kosell:studio:" + ident, batch)
    return batch


async def execute_batch(ident):
    async with execution_lock:
        await _execute_batch(ident)


async def _execute_batch(ident):
    key = "kosell:studio:" + ident
    batch = await get_setting(key, {})
    for row in batch["rows"]:
        if row["state"] != "planned":
            continue
        try:
            async with Session() as db:
                source = await db.get(Lot, row["source_lot_id"])
                if not source or source.account_id != row["account_id"]:
                    raise ValueError("Исходный лот удалён")
                client = await accounts.client(source.account_id)
                _, source_fields = await load_form(client, offer_id=source.funpay_lot_id)
            meta, fields = await load_form(client, node_id=row["node_id"])
            if meta.get("node_id") and meta["node_id"] != row["node_id"]:
                raise ValueError("Форма другой категории")
            allowed = {c["name"] for c in meta["controls"] if not c.get("readonly")}
            changes = {
                k: v
                for k, v in source_fields.items()
                if k in allowed
                and k.startswith("fields[")
                and not any(t in k for t in ("summary", "desc", "payment_msg", "images"))
            }
            for language, suffix in (("ru", ""), ("en", "_en")):
                for field, name in (
                    ("summary", "title"),
                    ("desc", "description"),
                    ("payment_msg", "payment"),
                ):
                    target = f"fields[{field}][{language}]"
                    if target in allowed:
                        changes[target] = row["texts"][name + suffix]
            if "fields[summary][ru]" not in allowed:
                raise ValueError("Категория не поддерживает текстовый заголовок")
            changes.update(
                {
                    k: v
                    for k, v in {
                        "price": row["price"],
                        "amount": str(row["amount"]),
                        "active": "",
                        "auto_delivery": "",
                        "secrets": "",
                    }.items()
                    if k in allowed
                }
            )
            merged = merge_fields(meta, fields, changes)
            merged.update(
                offer_id="0", node_id=str(row["node_id"]), active="", auto_delivery="", secrets=""
            )
            # Snapshot pre-existing IDs before a single creation POST.
            before = await client.get_lots()
            row["before_ids"] = [r["funpay_lot_id"] for r in before]
            row["state"] = "sending"
            await save_setting(key, batch)
            await save_form(client, merged)
            await accounts.sync_lots(row["account_id"])
            await reconcile_row(row)
            if row["state"] != "done":
                raise ValueError("Создание подтверждено, но новый ID не определён однозначно")
            await save_setting(key, batch)
        except Exception:
            if row["state"] in {"sending", "done"}:
                row["state"] = "manual_check"
                row["error"] = (
                    "Результат создания требует сверки. Повторная публикация заблокирована"
                )
            else:
                row["state"] = "failed"
                row["error"] = (
                    "Проверьте исходный лот и обязательные поля категории. Запрос создания не отправлялся"
                )
            batch["state"] = "manual_check" if row["state"] == "manual_check" else "failed"
            await save_setting(key, batch)
            await kosell.log(f"Партия {ident}: {row['error']}", "warning")
            return
    batch["state"] = "done"
    await save_setting(key, batch)
    await kosell.log(f"Студия: создано {len(batch['rows'])} выключенных лотов с привязками")


async def reconcile_row(row):
    async with Session() as db:
        candidates = (
            await db.scalars(
                select(Lot).where(
                    Lot.account_id == row["account_id"],
                    Lot.node_id == row["node_id"],
                    Lot.title == row["texts"]["title"],
                    Lot.price == Decimal(row["price"]),
                    Lot.is_active.is_(False),
                    Lot.funpay_lot_id.not_in(row["before_ids"]),
                )
            )
        ).all()
        if len(candidates) != 1:
            return
        lot = candidates[0]
        binding = await db.scalar(select(PluginBinding).where(PluginBinding.lot_id == lot.id))
        if binding and (
            binding.kind != "kosell"
            or binding.service_id != str(row["product_id"])
            or binding.quantity != row["hours"]
        ):
            return
        if not binding:
            binding = PluginBinding(
                kind="kosell",
                lot_id=lot.id,
                service_id=str(row["product_id"]),
                quantity=row["hours"],
                provider_id="verified",
                is_active=True,
            )
            db.add(binding)
            await db.commit()
        row.update(
            state="done", lot_id=lot.id, funpay_item_id=lot.funpay_lot_id, binding_id=binding.id
        )
        await save_setting(f"kosell:binding:{binding.id}", row["policy"])
        markers = await get_setting("kosell:hidden", {})
        markers[str(lot.id)] = {"reason": "studio", "price": str(lot.price)}
        await save_setting("kosell:hidden", markers)


@router.get("/studio/batches/{ident}")
async def batch_status(ident: str):
    result = await get_setting("kosell:studio:" + ident, None)
    if not result:
        raise HTTPException(404, "Партия не найдена")
    return result


@router.post("/studio/batches/{ident}/create")
async def create_batch(ident: str):
    async with lock:
        batch = await batch_status(ident)
        if batch["state"] != "preview":
            raise HTTPException(
                409, "Эта партия уже запускалась. Сверьте результат вместо повторной публикации"
            )
        if (
            kosell.aware(__import__("datetime").datetime.fromisoformat(batch["expires_at"]))
            < utcnow()
        ):
            raise HTTPException(400, "Предпросмотр устарел. Рассчитайте снова")
        batch["state"] = "applying"
        await save_setting("kosell:studio:" + ident, batch)
        task = asyncio.create_task(execute_batch(ident))
        tasks[ident] = task
        task.add_done_callback(lambda _: tasks.pop(ident, None))
    return batch


@router.post("/studio/batches/{ident}/reconcile")
async def reconcile_batch(ident: str):
    async with lock:
        batch = await batch_status(ident)
        if ident in tasks:
            raise HTTPException(409, "Партия ещё выполняется")
        if batch["state"] not in {"applying", "manual_check"}:
            return batch
        for row in batch["rows"]:
            if row["state"] not in {"sending", "manual_check"}:
                continue
            await accounts.sync_lots(row["account_id"])
            await reconcile_row(row)
        batch["state"] = (
            "done" if all(r["state"] == "done" for r in batch["rows"]) else "manual_check"
        )
        await save_setting("kosell:studio:" + ident, batch)
    return batch


@router.post("/studio/batches/{ident}/continue")
async def continue_batch(ident: str):
    async with lock:
        batch = await batch_status(ident)
        if ident in tasks or batch["state"] not in {"manual_check", "failed"}:
            raise HTTPException(409, "Партия не готова к продолжению")
        if any(row["state"] in {"sending", "manual_check"} for row in batch["rows"]):
            raise HTTPException(409, "Сначала сверьте все неоднозначные создания лотов")
        for row in batch["rows"]:
            if row["state"] == "failed":
                row["state"] = "planned"
        batch["state"] = "applying"
        await save_setting("kosell:studio:" + ident, batch)
        task = asyncio.create_task(execute_batch(ident))
        tasks[ident] = task
        task.add_done_callback(lambda _: tasks.pop(ident, None))
    return batch


@router.post("/markup/preview")
async def markup_preview(body: MarkupPreview):
    async with Session() as db:
        query = select(PluginBinding, Lot).join(Lot).where(PluginBinding.kind == "kosell")
        if body.binding_ids:
            query = query.where(PluginBinding.id.in_(body.binding_ids))
        rows = (await db.execute(query)).all()
        plans = []
        quotes = {}
        for binding, lot in rows:
            old = await get_setting(f"kosell:binding:{binding.id}", {})
            if not old.get("auto_price"):
                continue
            pair = (int(binding.service_id), binding.quantity)
            if pair not in quotes:
                quotes[pair] = await supplier(kosell.price(*pair))
            plans.append(
                {
                    "binding_id": binding.id,
                    "lot_id": lot.id,
                    "product_id": binding.service_id,
                    "hours": binding.quantity,
                    "title": lot.title,
                    "before": str(lot.price),
                    "after": str(
                        max(Decimal(1), sale_price(quotes[pair]["total_rub"], body.markup))
                    ),
                }
            )
    token = str(uuid.uuid4())
    await save_setting(
        "kosell:markup:" + token,
        {
            "rows": plans,
            "markup": body.markup,
            "expires_at": (utcnow() + timedelta(minutes=15)).isoformat(),
            "applied": False,
        },
    )
    return {"id": token, "rows": plans}


@router.post("/markup/{ident}/apply")
async def apply_markup(ident: str):
    from datetime import datetime

    async with lock:
        preview = await get_setting("kosell:markup:" + ident, {})
        if not preview or kosell.aware(datetime.fromisoformat(preview["expires_at"])) < utcnow():
            raise HTTPException(400, "Предпросмотр устарел")
        if preview["applied"]:
            return {"ok": True}
        import json

        async with Session() as db:
            records = []
            for row in preview["rows"]:
                binding = await db.get(PluginBinding, row["binding_id"])
                record = await db.get(Settings, f"kosell:binding:{row['binding_id']}")
                if (
                    not binding
                    or binding.kind != "kosell"
                    or binding.lot_id != row["lot_id"]
                    or binding.service_id != row["product_id"]
                    or binding.quantity != row["hours"]
                    or not record
                    or not json.loads(record.value).get("auto_price")
                ):
                    raise HTTPException(
                        409, "Привязки или правила изменились. Рассчитайте наценку заново"
                    )
                records.append(record)
            for record in records:
                record.value = json.dumps({"auto_price": True, "markup": preview["markup"]})
            preview["applied"] = True
            stored = await db.get(Settings, "kosell:markup:" + ident)
            stored.value = json.dumps(preview)
            await db.commit()

    return {"ok": True}


@router.put("/bindings/{ident}/policy")
async def save_policy(ident: int, body: Policy):
    async with Session() as db:
        row = await db.get(PluginBinding, ident)
        if not row or row.kind != "kosell":
            raise HTTPException(404, "Привязка не найдена")
    await save_setting(f"kosell:binding:{ident}", body.model_dump())
    return {"ok": True}


@router.post("/sync")
async def sync_now():
    if not (await kosell.settings())["enabled"]:
        raise HTTPException(
            409,
            "Включите KoSell для изменения связанных лотов. Каталог можно обновить в настройках",
        )
    await kosell.stock_check()
    return {"ok": True}


class Manual(BaseModel):
    order_id: int = Field(gt=0)


@router.post("/manual-delivery")
async def manual_delivery(body: Manual):
    if not (await kosell.settings())["enabled"]:
        raise HTTPException(409, "Сначала включите плагин")
    if not await kosell.ensure_for_order(body.order_id, manual=True):
        raise HTTPException(400, "Заказ не подходит: проверьте оплату и привязку лота")
    async with Session() as db:
        if not await db.scalar(
            select(KosellRental.id).where(KosellRental.order_id == body.order_id)
        ) and not await db.scalar(
            select(KosellOperation.id).where(KosellOperation.order_id == body.order_id)
        ):
            raise HTTPException(
                409,
                "Выдача не запущена. Проверьте доступность чата, привязку, выбранный аккаунт и правила автовыдачи",
            )
    return {"ok": True}


@router.post("/rentals/{ident}/retry-delivery")
async def retry_delivery(ident: int):
    async with kosell.message_lock, Session() as db:
        rental = await db.get(KosellRental, ident)
        if (
            not rental
            or rental.status != "ACTIVE"
            or not rental.end_time
            or kosell.aware(rental.end_time) <= utcnow()
        ):
            raise HTTPException(409, "Аренда не активна")
        queue = await db.scalar(
            select(KosellMessage).where(
                KosellMessage.rental_id == ident, KosellMessage.kind == "credentials"
            )
        )
        if not queue or queue.state == "sent":
            raise HTTPException(409, "Нет неподтверждённой выдачи для повтора")
        queue.state, queue.next_at = "pending", utcnow()
        await db.commit()
    return {"ok": True}


@router.get("/rentals/{ident}/access")
async def rental_access(ident: int):
    async with Session() as db:
        rental = await db.get(KosellRental, ident)
        if (
            not rental
            or rental.status != "ACTIVE"
            or not rental.end_time
            or kosell.aware(rental.end_time) <= utcnow()
        ):
            raise HTTPException(409, "Аренда не активна")
        order = await db.get(Order, rental.order_id)
        live = await (await accounts.client(order.account_id)).order_details(order.funpay_order_id)
        if (
            live.get("status") not in {"paid", "closed"}
            or int(live.get("buyer", {}).get("user_id", 0)) != rental.buyer_id
        ):
            raise HTTPException(409, "Оплата или покупатель не подтверждены")
        data = unpacked(rental.payload)
        result = await supplier(
            kosell.api("GET", f"/rental/{rental.kosell_uid}/credentials", key=data["key"])
        )
        if not result.get("steam_login") or not result.get("steam_password"):
            raise HTTPException(400, "KoSell не вернул реквизиты")
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"login": result["steam_login"], "password": result["steam_password"]},
            headers={"Cache-Control": "no-store"},
        )


@router.post("/operations/{ident}/reconcile")
async def reconcile_operation(ident: int):
    async with kosell.claim_lock, Session() as db:
        op = await db.get(KosellOperation, ident)
        if not op or op.state != "MANUAL_CHECK":
            raise HTTPException(409, "Операция не требует сверки")
        rental = await db.get(KosellRental, op.rental_id)
        payload = unpacked(op.payload)
        previous = payload.get("previous_end")
        if not previous:
            raise HTTPException(
                409, "Исходный срок неизвестен. Проверьте продление вручную в кабинете KoSell"
            )
        rows = await supplier(kosell.api("GET", "/rental/active", key=payload["key"]))
        match = (
            next((r for r in rows if r.get("rental_uid") == rental.kosell_uid), None)
            if isinstance(rows, list)
            else None
        )
        if not match:
            raise HTTPException(400, "Аренда не найдена среди активных")
        from datetime import datetime

        new_end = kosell.end_time(match["expires_at"])
        if new_end < kosell.aware(datetime.fromisoformat(previous)) + timedelta(hours=op.hours):
            raise HTTPException(
                409, "KoSell пока не подтверждает увеличение срока на нужное количество часов"
            )
        rental.end_time = new_end
        rental.status = "ACTIVE"
        op.state = "DONE"
        if op.order_id:
            order = await db.get(Order, op.order_id)
            order.auto_delivered, order.delivery_state = True, "sent"
        await db.commit()
    await kosell.log(f"Операция #{ident}: продление подтверждено оператором по сроку KoSell")
    return {"ok": True}


async def stop():
    pending = list(tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    tasks.clear()
