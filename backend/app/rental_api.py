"""Steam rental owner operations. No fictitious paid orders for manual leases."""

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from . import rental_verification as photos
from . import steam_rentals as rentals
from .db import Session
from .events import bus
from .integrated_config import packed, public_config, unpacked
from .integrated_schemas import RentalAccountCreate
from .message_templates import render
from .models import (
    Account,
    Activity,
    BlacklistEntry,
    Chat,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalAccountBinding,
    RentalAction,
    RentalBuyer,
    RentalLease,
    utcnow,
)
from .schemas import StrictModel
from .security import require_auth, vault
from .services import accounts, save_setting
from .steam_security import (
    describe_login_error,
    guard_code,
    new_password,
    rotate_password,
    verify_login,
)

router = APIRouter(prefix="/api/plugins/steam-rent", dependencies=[Depends(require_auth)])


class Connection(StrictModel):
    label: str = Field(min_length=1, max_length=150)
    game: str = Field(default="", max_length=150)
    notes: str = Field(default="", max_length=8000)
    binding_ids: list[int] = Field(default_factory=list, max_length=100)
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=100)
    mafile: str = Field(default="", max_length=100000)


class Metadata(StrictModel):
    label: str = Field(min_length=1, max_length=150)
    game: str = Field(default="", max_length=150)
    notes: str = Field(default="", max_length=8000)
    binding_ids: list[int] = Field(default_factory=list, max_length=100)


class ManualLease(StrictModel):
    account_id: int = Field(gt=0)
    chat_id: int = Field(gt=0)
    minutes: int = Field(ge=1, le=525600)
    request_key: str = Field(min_length=16, max_length=100)


class Extend(StrictModel):
    minutes: int = Field(ge=1, le=525600)
    request_key: str = Field(min_length=16, max_length=100)


class Review(StrictModel):
    action: str = Field(pattern="^(approve|reject|cancel)$")
    reason: str = Field(default="Нужно более чёткое фото рабочего места", max_length=1000)


class BuyerInput(StrictModel):
    notes: str = Field(default="", max_length=8000)
    blocked: bool = False
    reason: str = Field(default="", max_length=1000)


async def check_bindings(db, ids):
    rows = (
        await db.scalars(
            select(PluginBinding).where(
                PluginBinding.id.in_(ids),
                PluginBinding.kind == "steam-rent",
                PluginBinding.is_extension.is_(False),
            )
        )
    ).all()
    if len(rows) != len(set(ids)):
        raise HTTPException(422, "Выберите существующие основные лоты аренды")


async def check_identity(db, values, exclude=None):
    rows = (await db.scalars(select(RentalAccount))).all()
    for row in rows:
        if row.id == exclude:
            continue
        other = unpacked(row.credentials)
        if other["login"].casefold() == values["login"].casefold() or (
            values.get("steamid") and values.get("steamid") == other.get("steamid")
        ):
            raise HTTPException(409, "Этот Steam-аккаунт уже есть в пуле")


def connection_values(payload):
    if not payload.mafile.strip():
        return {"login": payload.login, "password": payload.password}, False
    try:
        mafile = json.loads(payload.mafile)
        session = mafile.get("Session", {})
        values = {
            "login": payload.login,
            "password": payload.password,
            "shared_secret": mafile.get("shared_secret", ""),
            "identity_secret": mafile.get("identity_secret", ""),
            "steamid": str(session.get("SteamID", mafile.get("steamid", ""))),
            "device_id": mafile.get("device_id", ""),
        }
        RentalAccountCreate(
            binding_id=payload.binding_ids[0] if payload.binding_ids else 1,
            label=payload.label,
            **values,
        )
        account_name = str(mafile.get("account_name", ""))
        if account_name and account_name.casefold() != payload.login.casefold():
            raise ValueError("maFile принадлежит другому логину")
    except Exception:
        raise HTTPException(
            422,
            "Проверьте maFile: логин, SteamID, shared_secret, identity_secret и device_id должны соответствовать аккаунту",
        ) from None
    return values, True


@router.get("/workspace")
async def workspace():
    async with Session() as db:
        inventory = (await db.scalars(select(RentalAccount).order_by(RentalAccount.id))).all()
        links = (
            await db.execute(
                select(RentalAccountBinding.account_id, RentalAccountBinding.binding_id)
            )
        ).all()
        leases = (
            await db.scalars(select(RentalLease).order_by(RentalLease.id.desc()).limit(300))
        ).all()
        rows = []
        for lease in leases:
            job, order, chat_id = await rentals.lease_context(db, lease)
            rented = next((r for r in inventory if r.id == lease.account_id), None)
            rows.append(
                {
                    "id": lease.id,
                    "account_id": lease.account_id,
                    "label": rented.label if rented else "",
                    "game": rented.game if rented else "",
                    "state": lease.state,
                    "expires_at": lease.expires_at,
                    "created_at": lease.created_at,
                    "finished_at": lease.finished_at,
                    "buyer": order.buyer_name if order else lease.buyer_name,
                    "order_number": order.funpay_order_id if order else None,
                    "chat_id": chat_id,
                    "manual": job is None,
                    "end_reason": lease.end_reason,
                }
            )
        pending = (
            await db.execute(
                select(PluginJob, Order)
                .join(Order)
                .where(
                    PluginJob.kind == "steam-rent",
                    PluginJob.state.in_(["waiting_review", "attention"]),
                )
                .order_by(PluginJob.id.desc())
                .limit(100)
            )
        ).all()
        chats = (
            await db.scalars(
                select(Chat).where(Chat.interlocutor_id > 0).order_by(Chat.id.desc()).limit(500)
            )
        ).all()
        buyers = (await db.scalars(select(RentalBuyer))).all()
        blocked = (await db.scalars(select(BlacklistEntry))).all()
        stats = {
            state: await db.scalar(
                select(func.count()).select_from(RentalAccount).where(RentalAccount.state == state)
            )
            for state in ("free", "leased", "frozen", "quarantine", "draft")
        }
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        stats["today"] = await db.scalar(
            select(func.count()).select_from(RentalLease).where(RentalLease.created_at >= today)
        )
        revenue = (
            await db.execute(
                select(Order.currency, func.sum(Order.sum))
                .join(PluginJob, PluginJob.order_id == Order.id)
                .where(
                    PluginJob.kind == "steam-rent",
                    Order.created_at >= today,
                    Order.status.in_(["paid", "closed"]),
                )
                .group_by(Order.currency)
            )
        ).all()
        stats["revenue"] = [{"currency": c, "amount": str(v)} for c, v in revenue]
        bindings = (
            await db.execute(
                select(PluginBinding, Lot).join(Lot).where(PluginBinding.kind == "steam-rent")
            )
        ).all()
        return {
            "stats": stats,
            "settings": await public_config("steam-rent"),
            "bindings": [
                {
                    "id": b.id,
                    "lot_id": lot.id,
                    "title": lot.title,
                    "extension": b.is_extension,
                    "account_id": lot.account_id,
                }
                for b, lot in bindings
            ],
            "inventory": [
                {
                    "id": r.id,
                    "binding_ids": sorted(
                        {
                            b
                            for b in [r.binding_id] + [b for a, b in links if a == r.id]
                            if b is not None
                        }
                    ),
                    "label": r.label,
                    "game": r.game,
                    "notes": r.notes,
                    "login": unpacked(r.credentials)["login"],
                    "state": r.state,
                    "last_error": r.last_error,
                }
                for r in inventory
            ],
            "leases": rows,
            "reviews": [
                {
                    "id": j.id,
                    "order_number": o.funpay_order_id,
                    "buyer": o.buyer_name,
                    "state": j.state,
                    "photo_state": unpacked(j.payload).get("photo_state"),
                    "report": unpacked(j.payload).get("photo_report"),
                    "rejects": unpacked(j.payload).get("photo_rejects", 0),
                    "deadline": unpacked(j.payload).get("photo_expires"),
                    "has_image": bool(unpacked(j.payload).get("photo_data")),
                }
                for j, o in pending
                if unpacked(j.payload).get("photo_state")
            ],
            "chats": [
                {
                    "id": c.id,
                    "buyer_id": c.interlocutor_id,
                    "buyer": c.interlocutor_name,
                    "account_id": c.account_id,
                }
                for c in chats
            ],
            "buyers": [
                {
                    "chat_id": c.id,
                    "account_id": c.account_id,
                    "buyer_id": c.interlocutor_id,
                    "buyer": c.interlocutor_name,
                    "notes": next(
                        (
                            b.notes
                            for b in buyers
                            if b.buyer_id == c.interlocutor_id and b.account_id == c.account_id
                        ),
                        "",
                    ),
                    "blocked": any(b.user_id == c.interlocutor_id for b in blocked),
                    "reason": next(
                        (b.reason for b in blocked if b.user_id == c.interlocutor_id), ""
                    ),
                }
                for c in chats
            ],
        }


@router.post("/connect", status_code=201)
async def connect(payload: Connection):
    values, complete = connection_values(payload)
    identity = hashlib.sha256(
        (values.get("steamid") or "login:" + values["login"].casefold()).encode()
    ).hexdigest()
    async with Session() as db:
        await check_bindings(db, payload.binding_ids)
        await check_identity(db, values)
        if await db.scalar(select(RentalAccount.id).where(RentalAccount.identity_hash == identity)):
            raise HTTPException(409, "Аккаунт уже добавлен")
    if complete:
        try:
            await verify_login(values)
        except Exception as error:
            detail = describe_login_error(error, credentials=values)
            await bus.log(
                "steam-rent",
                f"Вход в Steam не подтверждён при добавлении аккаунта «{payload.label}»: {detail}",
                level="error",
                source_plugin="steam-rent",
            )
            raise HTTPException(
                422,
                f"Steam не подтвердил вход ({detail}). Данные не добавлены в пул: проверьте логин, пароль и maFile",
            ) from None
    async with rentals.inventory_lock:
        async with Session() as db:
            await check_bindings(db, payload.binding_ids)
            await check_identity(db, values)
            # Автопривязка: если аккаунт добавляют без выбора лота, а активный
            # лот аренды один, связываем их сразу — настройки считаются
            # применёнными сразу после ввода.
            binding_ids = list(payload.binding_ids)
            if not binding_ids:
                single = await db.scalar(
                    select(PluginBinding.id).where(
                        PluginBinding.kind == "steam-rent",
                        PluginBinding.is_active.is_(True),
                        PluginBinding.is_extension.is_(False),
                    )
                )
                if single:
                    binding_ids = [single]
            row = RentalAccount(
                binding_id=binding_ids[0] if binding_ids else None,
                label=payload.label,
                game=payload.game,
                notes=payload.notes,
                credentials=packed(values),
                identity_hash=identity,
                state="free" if complete else "draft",
            )
            db.add(row)
            try:
                await db.flush()
                db.add_all(
                    [
                        RentalAccountBinding(account_id=row.id, binding_id=b)
                        for b in set(binding_ids)
                    ]
                )
                await db.commit()
            except IntegrityError:
                raise HTTPException(409, "Этот Steam-аккаунт уже есть в пуле") from None
    return {
        "id": row.id,
        "state": row.state,
        "message": "Вход подтверждён, аккаунт готов к выдаче"
        if complete
        else "Черновик сохранён. Для автоматической аренды добавьте maFile и подтвердите вход",
    }


@router.put("/inventory/{ident}/metadata")
async def metadata(ident: int, payload: Metadata):
    async with rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row:
                raise HTTPException(404, "Аккаунт не найден")
            await check_bindings(db, payload.binding_ids)
            row.label, row.game, row.notes = payload.label, payload.game, payload.notes
            row.binding_id = payload.binding_ids[0] if payload.binding_ids else None
            await db.execute(
                delete(RentalAccountBinding).where(RentalAccountBinding.account_id == ident)
            )
            db.add_all(
                [
                    RentalAccountBinding(account_id=ident, binding_id=b)
                    for b in set(payload.binding_ids)
                ]
            )
            await db.commit()
    return {"ok": True}


@router.put("/inventory/{ident}/connection")
async def reconnect(ident: int, payload: Connection):
    values, complete = connection_values(payload)
    if not complete:
        raise HTTPException(422, "Для подключения нужен maFile")
    async with rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row or row.state not in {"draft", "free", "frozen", "quarantine"}:
                raise HTTPException(409, "Аккаунт занят")
            previous = unpacked(row.credentials)
            if previous.get("steamid") and previous["steamid"] != values["steamid"]:
                raise HTTPException(
                    409, "Нельзя заменить аккаунт другим SteamID; добавьте отдельную запись"
                )
            if row.state == "quarantine" and previous["password"] == values["password"]:
                raise HTTPException(409, "После аренды требуется новый пароль")
            await check_bindings(db, payload.binding_ids)
            await check_identity(db, values, exclude=ident)
            row.state = "verifying"
            await db.commit()
    try:
        await verify_login(values)
    except Exception as error:
        detail = describe_login_error(error, credentials=values)
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            row.state, row.last_error = (
                "quarantine",
                f"Steam не подтвердил вход с обновлёнными данными: {detail}",
            )
            await db.commit()
        await bus.log(
            "steam-rent",
            f"Вход с обновлёнными данными не подтверждён (#{ident}): {detail}",
            level="error",
            source_plugin="steam-rent",
        )
        raise HTTPException(422, f"Steam не подтвердил вход: {detail}") from None
    async with Session() as db:
        row = await db.get(RentalAccount, ident)
        row.credentials, row.identity_hash = (
            packed(values),
            hashlib.sha256(values["steamid"].encode()).hexdigest(),
        )
        row.pending_password, row.last_error, row.state = None, None, "free"
        for lease in (
            await db.scalars(
                select(RentalLease).where(
                    RentalLease.account_id == ident, RentalLease.state == "attention"
                )
            )
        ).all():
            lease.state, lease.finished_at = "finished", utcnow()
        await db.commit()
    await metadata(ident, Metadata(**payload.model_dump(exclude={"login", "password", "mafile"})))
    return {"ok": True}


@router.post("/manual-lease", status_code=201)
async def manual_lease(payload: ManualLease):
    async with rentals.inventory_lock:
        async with Session() as db:
            existing = await db.scalar(
                select(RentalLease).where(RentalLease.request_key == payload.request_key)
            )
            if existing:
                if existing.account_id != payload.account_id or existing.chat_id != payload.chat_id:
                    raise HTTPException(409, "Ключ операции уже использован")
                return {
                    "id": existing.id,
                    "message": "Эта выдача уже была зарегистрирована; сообщение повторно не отправлено",
                }
            rented, chat = (
                await db.get(RentalAccount, payload.account_id),
                await db.get(Chat, payload.chat_id),
            )
            if not rented or rented.state != "free" or not chat or not chat.interlocutor_id:
                raise HTTPException(409, "Нужен свободный аккаунт и существующий чат покупателя")
            if not await db.scalar(
                select(Account.id).where(Account.id == chat.account_id, Account.is_active.is_(True))
            ):
                raise HTTPException(409, "Аккаунт FunPay выключен")
            if await db.scalar(
                select(BlacklistEntry.id).where(BlacklistEntry.user_id == chat.interlocutor_id)
            ):
                raise HTTPException(409, "Покупатель в чёрном списке")
            credentials = unpacked(rented.credentials)
            if not credentials.get("identity_secret"):
                raise HTTPException(409, "Подключите maFile и проверьте вход")
            lease = RentalLease(
                account_id=rented.id,
                chat_id=chat.id,
                buyer_id=chat.interlocutor_id,
                buyer_name=chat.interlocutor_name,
                funpay_account_id=chat.account_id,
                request_key=payload.request_key,
                expires_at=utcnow() + timedelta(minutes=payload.minutes),
            )
            db.add(lease)
            rented.state = "leased"
            await db.flush()
            text = await render(
                "steam-rent",
                "rent_credentials",
                {
                    "buyer": chat.interlocutor_name,
                    "login": credentials["login"],
                    "password": credentials["password"],
                    "expires": rentals.display_time(lease.expires_at),
                    "game": rented.game or rented.label,
                },
                db=db,
            )
            db.add(
                Activity(
                    channel="orders",
                    text=f"Ручная аренда #{lease.id}: аккаунт #{rented.id}, {payload.minutes} мин.",
                    account_id=chat.account_id,
                    source_plugin="steam-rent",
                )
            )
            await db.commit()
    try:
        await accounts.send(chat.id, text)
    except Exception:
        async with Session() as db:
            rented = await db.get(RentalAccount, payload.account_id)
            rented.last_error = "Результат отправки данных неизвестен. Аренда сохранена; проверьте чат перед повторной отправкой."
            await db.commit()
        raise HTTPException(
            502,
            "Аренда создана, но отправка данных требует проверки в чате. Аккаунт остаётся занятым",
        ) from None
    return {"id": lease.id}


@router.post("/leases/{ident}/extend")
async def extend(ident: int, payload: Extend):
    async with rentals.inventory_lock:
        async with Session() as db:
            old = await db.get(RentalAction, payload.request_key)
            if old:
                if old.lease_id != ident:
                    raise HTTPException(409, "Ключ операции уже использован")
                return {"ok": True}
            lease = await db.get(RentalLease, ident)
            if not lease or lease.state != "active" or rentals.aware(lease.expires_at) <= utcnow():
                raise HTTPException(409, "Продлить можно только активную аренду до окончания срока")
            lease.expires_at = rentals.aware(lease.expires_at) + timedelta(minutes=payload.minutes)
            lease.warned_at = lease.warned_30_at = None
            db.add(RentalAction(request_key=payload.request_key, lease_id=ident, kind="extend"))
            rented = await db.get(RentalAccount, lease.account_id)
            _, order, chat_id = await rentals.lease_context(db, lease)
            text = await render(
                "steam-rent",
                "rent_extended",
                {
                    "login": unpacked(rented.credentials)["login"],
                    "hours": payload.minutes / 60,
                    "expires": rentals.display_time(lease.expires_at),
                    "order_id": order.funpay_order_id if order else "",
                },
                db=db,
            )
            db.add(
                Activity(
                    channel="orders",
                    text=f"Аренда #{ident} вручную продлена на {payload.minutes} мин.",
                    source_plugin="steam-rent",
                    account_id=lease.funpay_account_id,
                )
            )
            await db.commit()
    if chat_id:
        await accounts.send(chat_id, text)
    return {"ok": True}


@router.post("/leases/{ident}/cancel")
async def cancel(ident: int):
    async with rentals.inventory_lock:
        async with Session() as db:
            lease = await db.get(RentalLease, ident)
            if not lease or lease.state != "active":
                raise HTTPException(409, "Аренда не активна")
            lease.expires_at, lease.end_reason = utcnow(), "cancelled"
            await db.commit()
    await rentals.tick()
    return {"message": "Отмена запущена: аккаунт освободится после проверенной смены пароля"}


@router.get("/inventory/{ident}/guard")
async def guard(ident: int):
    async with Session() as db:
        rented = await db.get(RentalAccount, ident)
        if not rented:
            raise HTTPException(404, "Аккаунт не найден")
        credentials = unpacked(rented.credentials)
        if not credentials.get("shared_secret"):
            raise HTTPException(409, "Сначала добавьте maFile")
    from .integrated_config import pool

    try:
        response = await pool.request(
            "POST", "https://api.steampowered.com/ITwoFactorService/QueryTime/v0001"
        )
        response.raise_for_status()
        when = int(response.json()["response"]["server_time"])
        return {"code": guard_code(credentials["shared_secret"], when), "seconds": 30 - when % 30}
    except Exception:
        raise HTTPException(502, "Не удалось синхронизировать часы Steam") from None


async def rotate_inventory(ident, credentials, password, previous):
    try:
        async with rentals.rotation_slots:
            await rotate_password(credentials, password)
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            credentials["password"] = password
            row.credentials, row.pending_password, row.state, row.last_error = (
                packed(credentials),
                None,
                previous,
                None,
            )
            db.add(
                Activity(
                    channel="logs",
                    text=f"Пароль Steam-аккаунта #{ident} изменён и проверен",
                    source_plugin="steam-rent",
                )
            )
            await db.commit()
    except Exception:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            row.state, row.last_error = (
                "quarantine",
                "Смена пароля не подтверждена. Проверьте сохранённый новый пароль через восстановление",
            )
            db.add(
                Activity(
                    channel="logs",
                    text=f"Смена пароля Steam-аккаунта #{ident} требует проверки",
                    level="error",
                    source_plugin="steam-rent",
                )
            )
            await db.commit()
    finally:
        rentals.rotation_tasks.pop(-ident, None)


@router.post("/inventory/{ident}/rotate")
async def change_password(ident: int):
    async with rentals.inventory_lock:
        async with Session() as db:
            row = await db.get(RentalAccount, ident)
            if not row or row.state not in {"free", "frozen"}:
                raise HTTPException(
                    409, "Завершите аренду или проверку аккаунта перед сменой пароля"
                )
            credentials = unpacked(row.credentials)
            password, previous = new_password(), row.state
            row.pending_password, row.state = vault.encrypt(password), "rotating"
            await db.commit()
        task = asyncio.create_task(
            rotate_inventory(ident, credentials, password, previous), name=f"steam-password-{ident}"
        )
        rentals.rotation_tasks[-ident] = task
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
    return {"message": "Смена пароля запущена. Результат появится в статусе аккаунта и логах"}


@router.put("/buyers/{account_id}/{buyer_id}")
async def buyer(account_id: int, buyer_id: int, payload: BuyerInput):
    async with Session() as db:
        chat = await db.scalar(
            select(Chat).where(Chat.account_id == account_id, Chat.interlocutor_id == buyer_id)
        )
        if not chat:
            raise HTTPException(404, "Покупатель не найден")
        row = await db.scalar(
            select(RentalBuyer).where(
                RentalBuyer.account_id == account_id, RentalBuyer.buyer_id == buyer_id
            )
        )
        if not row:
            row = RentalBuyer(account_id=account_id, buyer_id=buyer_id)
            db.add(row)
        row.notes = payload.notes
        blocked = await db.scalar(select(BlacklistEntry).where(BlacklistEntry.user_id == buyer_id))
        if payload.blocked:
            if not blocked:
                blocked = BlacklistEntry(user_id=buyer_id, username=chat.interlocutor_name)
                db.add(blocked)
            blocked.reason = payload.reason
        elif blocked:
            await db.delete(blocked)
        await db.commit()
    return {"ok": True}


@router.post("/reviews/{ident}/decision")
async def decision(ident: int, payload: Review):
    try:
        await photos.decide(ident, payload.action, payload.reason)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True}


@router.get("/reviews/{ident}/image")
async def review_image(ident: int):
    async with Session() as db:
        job = await db.get(PluginJob, ident)
        if not job or job.kind != "steam-rent":
            raise HTTPException(404, "Проверка не найдена")
        data = unpacked(job.payload).get("photo_data")
        if not data:
            raise HTTPException(404, "Нет сохранённого изображения")
        return Response(
            base64.b64decode(data), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
        )


@router.get("/references")
async def list_references():
    return [{"id": r["id"], "name": r["name"]} for r in await photos.references()]


@router.post("/references")
async def add_reference(file: UploadFile = File(...)):
    raw = await file.read(photos.MAX_BYTES + 1)
    try:
        _, normalized = await asyncio.to_thread(photos.image_info, raw)
    except Exception:
        raise HTTPException(422, "Нужен JPEG, PNG или WebP до 8 МБ и 24 Мп") from None
    async with rentals.inventory_lock:
        rows = await photos.references()
        if len(rows) >= 10:
            raise HTTPException(409, "Можно добавить до 10 референсов")
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "name": (file.filename or "Фото")[:150],
                "data": base64.b64encode(normalized).decode(),
            }
        )
        await save_setting("rent:photo:references", packed({"images": rows}))
    return {"ok": True}


@router.delete("/references/{ident}")
async def remove_reference(ident: str):
    async with rentals.inventory_lock:
        rows = await photos.references()
        await save_setting(
            "rent:photo:references", packed({"images": [r for r in rows if r["id"] != ident]})
        )
    return {"ok": True}
