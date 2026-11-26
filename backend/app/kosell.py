"""KoSell Rental: isolated purchases, supplier preparation and durable chat delivery."""

import asyncio
import contextlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import select

from .db import Session
from .events import bus
from .integrated_config import packed, pool, unpacked
from .message_templates import notify, order_context, render
from .models import (
    Account,
    BlacklistEntry,
    Chat,
    KosellMessage,
    KosellOperation,
    KosellRental,
    Lot,
    Order,
    PluginBinding,
    utcnow,
)
from .security import vault
from .services import accounts, get_setting, save_setting

ID = "kosell"
BASE = "https://ru.kosell.store/api/v1"
claim_lock = asyncio.Lock()
network_lock = asyncio.Lock()
message_lock = asyncio.Lock()
next_call = 0.0
running = {}


class KoSellError(ValueError):
    def __init__(self, message, status=0, retry_after=60):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


async def settings(private=False):
    value = {
        "enabled": False,
        "api_key": "",
        "currency": "RUB",
        "only_verified": True,
        "auto_hide": True,
        "auto_delivery": True,
        "review_bonus": False,
        "allow_loss": False,
        "balance_threshold": 0,
        "balance_currency": "RUB",
        "hide_low_balance": False,
        "account_scope": "all",
        "account_ids": [],
        "stock_interval": 60,
        "game_intervals": {},
        **await get_setting(ID, {}),
    }
    value = {**value}
    if private:
        value["api_key"] = vault.decrypt(value["api_key"]) if value["api_key"] else ""
    else:
        value["has_api_key"], value["api_key"] = bool(value["api_key"]), ""
    return value


async def api(method, path, *, key=None, data=None, operation_key=None):
    global next_call
    key = key if key is not None else (await settings(True))["api_key"]
    if not key:
        raise KoSellError("Сначала сохраните API-ключ KoSell")
    async with network_lock:
        blocked = await get_setting("kosell:cooldown", "")
        if blocked and datetime.fromisoformat(blocked) > utcnow():
            raise KoSellError(
                "KoSell ограничил частоту запросов",
                429,
                max(1, int((datetime.fromisoformat(blocked) - utcnow()).total_seconds())),
            )
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, next_call - loop.time()))
        next_call = loop.time() + 0.25
        headers = {"X-API-Key": key}
        if operation_key:
            headers["Idempotency-Key"] = operation_key
        try:
            response = await pool.request(
                method, BASE + path, headers=headers, **({"json": data} if data is not None else {})
            )
        except Exception:
            raise KoSellError(
                "Сеть KoSell недоступна; результат изменяющего запроса требует сверки"
            ) from None
        if response.status_code == 429:
            try:
                delay = min(86400, max(1, int(response.headers.get("Retry-After", "60"))))
            except ValueError:
                delay = 60
            await save_setting("kosell:cooldown", (utcnow() + timedelta(seconds=delay)).isoformat())
            raise KoSellError("KoSell ограничил частоту запросов", 429, delay)
        if response.status_code >= 400:
            raise KoSellError(
                f"KoSell вернул HTTP {response.status_code}; проверьте операцию у поставщика",
                response.status_code,
            )
        try:
            result = response.json()
        except ValueError:
            raise KoSellError("KoSell вернул неподтверждённый формат ответа") from None
        return result


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def end_time(value):
    result = aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    if result <= utcnow():
        raise KoSellError("Поставщик вернул истёкшую аренду")
    return result


async def log(text, level="info", account_id=None):
    # Telemetry must not change an already committed financial result.
    with contextlib.suppress(Exception):
        await bus.log("orders", text, account_id, level, source_plugin=ID)


async def products():
    rows = await api("GET", "/rental/products?currency=RUB")
    if not isinstance(rows, list) or any(
        not isinstance(r, dict) or not isinstance(r.get("id"), int) or "available_accounts" not in r
        for r in rows
    ):
        raise KoSellError("Каталог KoSell имеет неожиданный формат")
    await save_setting("kosell:catalog", {"items": rows, "updated_at": utcnow().isoformat()})
    return rows


async def price(product_id, hours, key=None):
    result = await api(
        "POST", "/rental/calculate-price", key=key, data={"product_id": product_id, "hours": hours}
    )
    if (
        not isinstance(result, dict)
        or result.get("product_id") != product_id
        or result.get("hours") != hours
    ):
        raise KoSellError("KoSell не подтвердил параметры расчёта")
    for field in ("total_rub", "total_usd"):
        value = Decimal(str(result.get(field, "NaN")))
        if not value.is_finite() or value <= 0:
            raise KoSellError("KoSell не вернул однозначную стоимость")
    return result


async def manual(db, rental, reason):
    rental.status, rental.last_error = "MANUAL_CHECK", reason
    await db.commit()
    await log(f"Заказ #{rental.order_id}: MANUAL_CHECK. {reason}", "error")
    order = await db.get(Order, rental.order_id)
    with contextlib.suppress(Exception):
        await notify(ID, "kosell_manual_check", order_context(order, reason=reason))


async def ensure_for_order(order_id, *, manual=False):
    async with claim_lock:
        async with Session() as db:
            if await db.scalar(select(KosellRental.id).where(KosellRental.order_id == order_id)):
                return True
            if await db.scalar(
                select(KosellOperation.id).where(KosellOperation.order_id == order_id)
            ):
                return True
            order = await db.get(Order, order_id)
            if not order or order.status != "paid" or order.delivery_state != "pending":
                return False
            candidates = (
                await db.scalars(
                    select(PluginBinding)
                    .join(Lot)
                    .where(PluginBinding.kind == ID, Lot.account_id == order.account_id)
                )
            ).all()
            if not candidates:
                return False
            detail = await (await accounts.client(order.account_id)).order_details(
                order.funpay_order_id
            )
            from .app_route import detail_lot_id

            ident = detail_lot_id(detail) or order.funpay_lot_id
            lot = await db.scalar(
                select(Lot).where(Lot.account_id == order.account_id, Lot.funpay_lot_id == ident)
            )
            binding = next((b for b in candidates if lot and b.lot_id == lot.id), None)
            if not binding:
                return False
            config = await settings(True)
            if (
                not config["enabled"]
                or (not config["auto_delivery"] and not manual)
                or not binding.is_active
                or (
                    config["account_scope"] == "selected"
                    and order.account_id not in config["account_ids"]
                )
            ):
                return True
            if await db.scalar(
                select(BlacklistEntry.id).where(BlacklistEntry.user_id == order.buyer_id)
            ):
                order.delivery_state = "blocked"
                await db.commit()
                return True
            if (
                detail.get("status") != "paid"
                or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
            ):
                return True
            chat = await db.scalar(
                select(Chat).where(
                    Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
                )
            )
            units = detail.get("quantity", detail.get("amount", 1))
            if (
                not chat
                or isinstance(units, bool)
                or not str(units).isdigit()
                or not 1 <= int(units) * binding.quantity <= 8760
            ):
                return True
            if config["only_verified"] and binding.provider_id != "verified":
                return True
            from .kosell_extensions import consume_intent, execute

            operation = await consume_intent(
                db, order, chat, binding, int(units) * binding.quantity
            )
            if operation:
                task = asyncio.create_task(execute(operation))
                task_key = f"operation:{operation}"
                running[task_key] = task
                task.add_done_callback(lambda _: running.pop(task_key, None))
                return True
            rental = KosellRental(
                order_id=order.id,
                binding_id=binding.id,
                chat_id=chat.id,
                buyer_id=order.buyer_id,
                operation_key=str(uuid.uuid4()),
                payload=packed(
                    {
                        "key": config["api_key"],
                        "product_id": int(binding.service_id),
                        "hours": int(units) * binding.quantity,
                        "currency": config["currency"],
                        "lot_id": lot.funpay_lot_id,
                    }
                ),
            )
            db.add(rental)
            order.delivery_state = "kosell_pending"
            await db.commit()
            # A claimed row precedes *any* debit. A restart never repeats rent.
            ident = rental.id
    task = asyncio.create_task(purchase(ident))
    running[ident] = task
    task.add_done_callback(lambda _: running.pop(ident, None))
    return True


async def purchase(ident):
    async with Session() as db:
        rental = await db.get(KosellRental, ident)
        if not rental or rental.status != "PURCHASING":
            return
        payload = unpacked(rental.payload)
        order = await db.get(Order, rental.order_id)
        try:
            account = await db.get(Account, order.account_id)
            if not account or not account.is_active or not (await settings())["enabled"]:
                await manual(
                    db, rental, "Плагин или аккаунт остановлен до закупки. Списание не отправлялось"
                )
                return
            total = await price(payload["product_id"], payload["hours"], payload["key"])
            field = {"RUB": "total_rub", "USD": "total_usd"}.get(order.currency.upper())
            if not field or (
                not (await settings())["allow_loss"] and Decimal(str(total[field])) > order.sum
            ):
                await manual(
                    db,
                    rental,
                    "Закупка дороже продажи или валюта заказа не поддерживается. Списание не отправлялось",
                )
                return
            live = await (await accounts.client(order.account_id)).order_details(
                order.funpay_order_id
            )
            if (
                live.get("status") != "paid"
                or int(live.get("buyer", {}).get("user_id", 0)) != rental.buyer_id
            ):
                await manual(
                    db,
                    rental,
                    "Оплата или покупатель больше не подтверждены. Списание не отправлялось",
                )
                return
            response = await api(
                "POST",
                "/rental/rent",
                key=payload["key"],
                operation_key=rental.operation_key,
                data={
                    "product_id": payload["product_id"],
                    "duration_hours": payload["hours"],
                    "currency": payload["currency"],
                    "verify_before_issue": True,
                    "idempotency_key": rental.operation_key,
                },
            )
            if not isinstance(response, dict) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,128}", str(response.get("rental_uid", ""))
            ):
                raise KoSellError("Закупка не вернула корректный UID")
            rental.kosell_uid = response["rental_uid"]
            from .kosell_extensions import receipt

            payload["receipt"] = receipt(response, payload["currency"])
            payload["delivery"] = bool(response.get("delivery"))
            rental.payload = packed(payload)
            await db.commit()
            rental.end_time = end_time(response["expires_at"])
            rental.status = "PREPARING"
            await db.commit()
            await log(
                f"Заказ #{order.funpay_order_id}: закупка UID {rental.kosell_uid}, ожидаем подготовку",
                account_id=order.account_id,
            )
        except Exception as error:
            await db.rollback()
            rental = await db.get(KosellRental, ident, populate_existing=True)
            await manual(
                db,
                rental,
                str(error)
                if isinstance(error, KoSellError)
                else "Результат закупки требует ручной сверки",
            )


async def prepare(ident):
    async with Session() as db:
        rental = await db.get(KosellRental, ident)
        if not rental or rental.status != "PREPARING":
            return
        payload = unpacked(rental.payload)
        try:
            if payload.get("delivery"):
                state = await api(
                    "GET",
                    "/rental/delivery/" + quote(rental.kosell_uid, safe=""),
                    key=payload["key"],
                )
                if state.get("state") in {"queued", "checking"}:
                    return
                if state.get("state") != "ready" or state.get("refunded"):
                    await manual(
                        db, rental, "Подготовка поставщиком не подтверждена или закупка возвращена"
                    )
                    return
            credentials = await api(
                "GET",
                f"/rental/{quote(rental.kosell_uid, safe='')}/credentials",
                key=payload["key"],
            )
            if not credentials.get("steam_login") or not credentials.get("steam_password"):
                raise KoSellError("KoSell не вернул реквизиты")
            order = await db.get(Order, rental.order_id)
            payload["login"] = credentials["steam_login"]
            rental.payload = packed(payload)
            text = await render(
                ID,
                "kosell_credentials",
                order_context(
                    order,
                    login=credentials["steam_login"],
                    password=credentials["steam_password"],
                    expires=rental.end_time.isoformat(),
                    uid=rental.kosell_uid,
                    duration_hours=payload["hours"],
                    lot_id=payload.get("lot_id", ""),
                    product_id=payload["product_id"],
                    rental_uid=rental.kosell_uid,
                    expires_at=rental.end_time.isoformat(),
                ),
                db=db,
            )
            db.add(
                KosellMessage(
                    rental_id=rental.id,
                    event_key=f"credentials:{rental.id}",
                    kind="credentials",
                    body=vault.encrypt(text),
                    expires_at=rental.end_time,
                )
            )
            rental.status = "ACTIVE"
            await db.commit()
        except KoSellError as error:
            await log(
                f"UID {rental.kosell_uid}: подготовка недоступна (HTTP {error.status})", "warning"
            )


async def handle_message(chat_id, account_id, message):
    if message.get("is_my"):
        return False
    from .kosell_extensions import command as extra_command

    handled = await extra_command(chat_id, account_id, message)
    if handled is not None:
        return handled
    command = re.fullmatch(
        r"!код(?:\s+([#A-Za-z0-9_-]+)|\(([A-Za-z0-9_-]+)\))?", message.get("text", "").strip(), re.I
    )
    if not command or not (await settings(True))["enabled"]:
        return False
    async with claim_lock, Session() as db:
        chat = await db.get(Chat, chat_id)
        if (
            not chat
            or chat.account_id != account_id
            or chat.interlocutor_id != message.get("sender_id")
        ):
            return False
        rows = (
            await db.scalars(
                select(KosellRental)
                .join(Order)
                .where(
                    KosellRental.chat_id == chat_id,
                    KosellRental.buyer_id == chat.interlocutor_id,
                    Order.account_id == account_id,
                )
                .order_by(KosellRental.id.desc())
            )
        ).all()
        target = command[1] or command[2]
        if target:
            rows = [
                r
                for r in rows
                if r.kosell_uid == target
                or unpacked(r.payload).get("login") == target
                or (await db.get(Order, r.order_id)).funpay_order_id == target.lstrip("#")
            ]
        if not rows:
            return False
        active = [
            r for r in rows if r.status == "ACTIVE" and r.end_time and aware(r.end_time) > utcnow()
        ]
        if not target:
            from .steam_rentals import buyer_leases

            local = await buyer_leases(db, account_id, chat.interlocutor_id)
            if not active and local:
                return False
            if len(active) > 1 or (active and local):
                await accounts.send(
                    chat_id,
                    await render(
                        ID,
                        "kosell_select",
                        {"uids": ", ".join(r.kosell_uid for r in active)},
                        db=db,
                    ),
                )
                return True
        rental = active[0] if active else rows[0]
        event = message.get("local_id") or message.get("id")
        # Incoming event IDs are durable; duplicate ingestion cannot queue two codes.
        event_key = (
            f"guard:{rental.id}:{event}"
            if event is not None
            else f"guard:{rental.id}:{int(utcnow().timestamp()) // 30}"
        )
        if not await db.scalar(
            select(KosellMessage.id).where(KosellMessage.event_key == event_key)
        ):
            db.add(
                KosellMessage(
                    rental_id=rental.id,
                    kind="guard",
                    event_key=event_key,
                    expires_at=utcnow() + timedelta(minutes=2),
                )
            )
            await db.commit()
    return True


async def flush_messages():
    async with message_lock:
        async with Session() as db:
            messages = (
                await db.scalars(
                    select(KosellMessage)
                    .join(KosellRental)
                    .join(Order)
                    .join(Account, Order.account_id == Account.id)
                    .where(
                        KosellMessage.state == "pending",
                        KosellMessage.next_at <= utcnow(),
                        Account.is_active.is_(True),
                    )
                    .order_by(KosellMessage.id)
                    .limit(20)
                )
            ).all()
            for entry in messages:
                if aware(entry.expires_at) <= utcnow():
                    entry.state = "expired"
                    await db.commit()
                    continue
                rental = await db.get(KosellRental, entry.rental_id)
                order = await db.get(Order, rental.order_id)
                account = await db.get(Account, order.account_id)
                if not account or not account.is_active:
                    continue
                payload = unpacked(rental.payload)
                try:
                    chat = await db.get(Chat, rental.chat_id)
                    if (
                        not chat
                        or chat.account_id != order.account_id
                        or chat.interlocutor_id != rental.buyer_id
                        or await db.scalar(
                            select(BlacklistEntry.id).where(
                                BlacklistEntry.user_id == rental.buyer_id
                            )
                        )
                    ):
                        entry.state = "cancelled"
                        await db.commit()
                        continue
                    live = await (await accounts.client(order.account_id)).order_details(
                        order.funpay_order_id
                    )
                    buyer = int(live.get("buyer", {}).get("user_id", 0))
                    valid = (
                        rental.status == "ACTIVE"
                        and rental.end_time
                        and aware(rental.end_time) > utcnow()
                        and order.status in {"paid", "closed"}
                        and live.get("status") in {"paid", "closed"}
                        and buyer == rental.buyer_id
                    )
                    if entry.kind in {"credentials", "notice"}:
                        if not valid:
                            entry.state = "cancelled"
                            await db.commit()
                            continue
                        text = vault.decrypt(entry.body)
                    elif not valid:
                        text = await render(ID, "kosell_rent_expired", order_context(order), db=db)
                    else:
                        try:
                            result = await api(
                                "GET",
                                f"/rental/{quote(rental.kosell_uid, safe='')}/code",
                                key=payload["key"],
                            )
                            if result.get("success") is not True or not re.fullmatch(
                                r"[A-Z0-9]{5}", result.get("code") or ""
                            ):
                                raise KoSellError("KoSell не подтвердил Guard код")
                            text = await render(
                                ID,
                                "kosell_guard_success",
                                order_context(
                                    order,
                                    code=result["code"],
                                    login=payload.get("login", ""),
                                    lot_id=payload.get("lot_id", ""),
                                    product_id=payload["product_id"],
                                    duration_hours=payload["hours"],
                                    expires_at=rental.end_time.isoformat(),
                                    rental_uid=rental.kosell_uid,
                                ),
                                db=db,
                            )
                        except KoSellError as error:
                            text = await render(
                                ID,
                                "kosell_guard_limit"
                                if error.status == 429
                                else "kosell_guard_fail_network",
                                order_context(order),
                                db=db,
                            )
                            if error.status != 429:
                                # Persist a fresh-code retry; don't retain a short-lived Guard value.
                                entry.next_at = utcnow() + timedelta(seconds=30)
                                entry.attempts += 1
                                await db.commit()
                                if entry.attempts == 1:
                                    await accounts.send(rental.chat_id, text)
                                continue
                    entry.state = "sending"
                    entry.attempts += 1
                    await db.commit()
                    await accounts.send(rental.chat_id, text)
                    entry.state = "sent"
                    if entry.kind == "credentials":
                        order.auto_delivered, order.delivery_state = True, "sent"
                    await db.commit()
                except Exception:
                    entry.state, entry.next_at = "pending", utcnow() + timedelta(seconds=30)
                    await db.commit()
                    await log(
                        f"Очередь FunPay: сообщение #{entry.id} отложено без повторной закупки",
                        "warning",
                        order.account_id,
                    )


async def stock_check():
    from .kosell_sync import sync

    await sync()


async def tick():
    if not (await settings(True))["enabled"]:
        return
    await flush_messages()
    async with Session() as db:
        rows = (
            await db.scalars(
                select(KosellRental).where(KosellRental.status.in_(["PREPARING", "ACTIVE"]))
            )
        ).all()
        ids = [r.id for r in rows if r.status == "PREPARING"]
        for row in rows:
            if row.end_time and aware(row.end_time) <= utcnow():
                row.status = "EXPIRED"
        await db.commit()
    for ident in ids:
        try:
            await prepare(ident)
        except Exception:
            await log(f"Аренда #{ident}: требуется проверка подготовки", "warning")
    await flush_messages()


async def recover():
    async with Session() as db:
        for row in (
            await db.scalars(select(KosellRental).where(KosellRental.status == "PURCHASING"))
        ).all():
            await manual(db, row, "Перезапуск во время закупки. Повторное списание запрещено")
        for row in (
            await db.scalars(select(KosellMessage).where(KosellMessage.state == "sending"))
        ).all():
            row.state = "pending"
        await db.commit()


async def stop():
    tasks = list(running.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    running.clear()
