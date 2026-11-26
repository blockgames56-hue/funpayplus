from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
import secrets
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from sqlalchemy import case, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (
    app_route,
    auto_dump,
    auto_robux,
    email_codes,
    error_contract,
    fulfillment,
    lzt_market,
    plugin_catalog,
)
from .account_workspace import router as account_workspace_router
from .avatar_cache import cached_avatar
from .blacklist import scope as blacklist_scope
from .config import config
from .copylots import copy_form, publish_copy
from .db import Session, engine, get_db
from .diagnostics import router as diagnostics_router
from .events import bus
from .funpay import (
    ChallengeException,
    FunPayError,
    ResponseFormatError,
    UnauthorizedException,
)
from .funpay_gateway import FunPayGateway as FunPayClient
from .integrated_api import router as integrated_router
from .inventory import InventoryConflict
from .kosell_api import router as kosell_router
from .kosell_workspace import router as kosell_workspace_router
from .kosellrent_api import router as kosellrent_router
from .lot_editor import create_offers, load_form, merge_fields, quote_prices, save_form
from .loyalty import resolve as resolve_bonus
from .models import (
    Account,
    Activity,
    AppRouteDelivery,
    AppRouteMapping,
    AppRouteSupplier,
    AutoDeliveryRule,
    AutoDumpRule,
    BlacklistEntry,
    BonusBalance,
    BonusEntry,
    Chat,
    ChatMessage,
    FulfillmentTask,
    Lot,
    LotPromotion,
    MarketPurchase,
    Order,
    PluginBinding,
    Proxy,
    utcnow,
)
from .plugin_workspace import plugin_activity
from .plugin_workspace import router as plugin_workspace_router
from .promotions import (
    create as create_promotion,
)
from .promotions import (
    recover as recover_promotion,
)
from .promotions import (
    restore as restore_promotion,
)
from .rental_api import router as rental_router
from .schemas import (
    AccountCreate,
    AccountPatch,
    AppSettings,
    AutoDumpCreate,
    AutoDumpPatch,
    BlacklistCreate,
    BlacklistImport,
    ChatPatch,
    CopyPublish,
    EmailMailboxSettings,
    FulfillmentSettings,
    FulfillmentTarget,
    KeyPoolAppend,
    Login,
    LotBulk,
    LotCopies,
    LotCreate,
    LotSave,
    LoyaltySettings,
    MarketBuyerSettings,
    MarketConnection,
    MessageCreate,
    OfflineSettings,
    PluginSettings,
    PromotionCreate,
    ProxyBatch,
    RobuxConnection,
    RobuxSettings,
    RouteMappingCreate,
    RouteSupplierCreate,
    RouteSupplierUpdate,
    RuleCreate,
    TelegramSettings,
)
from .security import (
    allowed_origins,
    authorized,
    require_auth,
    require_origin,
    session_cookie,
    vault,
)
from .services import (
    accounts,
    automation,
    get_setting,
    parse_proxy,
    proxy_url,
    save_setting,
    telegram,
)
from .telegram_control import router as telegram_control_router
from .template_api import router as template_router
from .workers import workers


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .runtime import stop_background_tasks

    try:
        # Every supported launcher applies migrations before ASGI startup.
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        if config.workers_enabled:
            from . import kosellrent

            await workers.start()
            await kosellrent.start()
            await email_codes.start_saved_mailbox()
            await lzt_market.worker.start()
            from .telegram_control import control

            await control.start()
        yield
    finally:
        try:
            await stop_background_tasks(strict=False)
        finally:
            await engine.dispose()


app = FastAPI(title="FunPay Plus", version="0.1.9", lifespan=lifespan)
app.middleware("http")(plugin_activity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    error_contract.correlation(request)
    response = await call_next(request)
    error_contract.middleware_header(request, response)
    return response


app.add_exception_handler(error_contract.ApiError, error_contract.api_error_handler)
app.add_exception_handler(StarletteHTTPException, error_contract.http_error_handler)
app.add_exception_handler(RequestValidationError, error_contract.validation_error_handler)
app.add_exception_handler(FunPayError, error_contract.remote_error_handler)
app.add_exception_handler(InventoryConflict, error_contract.inventory_conflict_handler)
app.add_exception_handler(IntegrityError, error_contract.integrity_error_handler)
app.add_exception_handler(Exception, error_contract.internal_error_handler)


attempts: dict[str, deque] = defaultdict(deque)


@app.post("/api/auth/login")
async def login(payload: Login, request: Request, response: Response):
    host = request.client.host if request.client else "unknown"
    queue = attempts[host]
    while queue and queue[0] < time.monotonic() - 60:
        queue.popleft()
    if len(queue) >= 10:
        raise HTTPException(429, "Слишком много попыток. Подождите минуту.")
    queue.append(time.monotonic())
    if not secrets.compare_digest(payload.token, config.api_token):
        raise HTTPException(401, "Неверный ключ доступа")
    queue.clear()
    response.set_cookie(
        "pulse_session",
        session_cookie(),
        httponly=True,
        secure=config.secure_cookie,
        samesite="strict",
        max_age=86400,
        path="/",
    )
    return {"ok": True}


@app.post("/api/auth/connect")
async def connect_without_key(request: Request, response: Response):
    """Open this installation without a user-entered access key."""
    require_origin(request)
    from .telegram_control import protect_public_connect

    await protect_public_connect(request)
    incoming = request.headers.get("authorization", "").removeprefix("Bearer ")
    from .telegram_control import authorized_session

    if await authorized_session(incoming):
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True, "session_token": incoming}
    value = session_cookie()
    response.set_cookie("pulse_session", value, httponly=True, secure=config.secure_cookie,
                        samesite="strict", max_age=86400, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "session_token": value}


api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@api.get("/auth/session")
async def session():
    return {"ok": True}


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("pulse_session", path="/")
    return {"ok": True}


def public(record, *, exclude: set[str] | None = None) -> dict:
    hidden = {"golden_key", "password", "keys_pool", "delivery_text", "token", "result_data"} | (
        exclude or set()
    )
    return jsonable_encoder(
        {
            column.key: getattr(record, column.key)
            for column in inspect(record).mapper.column_attrs
            if column.key not in hidden
        }
    )


async def required(db: AsyncSession, model, ident):
    record = await db.get(model, ident)
    if not record:
        raise HTTPException(404, "Запись не найдена")
    return record


def scoped(statement, model, account_id):
    return statement.where(model.account_id == account_id) if account_id else statement


@api.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    return [public(row) for row in (await db.scalars(select(Account).order_by(Account.id))).all()]


@api.get("/avatars/{kind}/{ident}")
async def avatar_image(
    kind: Literal["account", "chat"], ident: int, db: AsyncSession = Depends(get_db)
):
    await required(db, Account if kind == "account" else Chat, ident)
    url = await accounts.ensure_avatar(kind, ident)
    if not url:
        raise HTTPException(404, "Пользователь не установил аватарку")
    try:
        return await cached_avatar(url)
    except FunPayError:
        refreshed = await accounts.ensure_avatar(kind, ident, force=True)
        if not refreshed:
            raise HTTPException(404, "Пользователь не установил аватарку") from None
        return await cached_avatar(refreshed)


@api.post("/accounts", status_code=201)
async def add_account(payload: AccountCreate, db: AsyncSession = Depends(get_db)):
    proxy = await required(db, Proxy, payload.proxy_id) if payload.proxy_id else None
    client = FunPayClient(
        payload.golden_key,
        payload.user_agent,
        proxy_url(proxy) if proxy else None,
        config.funpay_transport,
    )
    try:
        profile = await client.sync_profile()
    finally:
        await client.close()
    existing = await db.scalar(select(Account.id).where(Account.funpay_id == profile["funpay_id"]))
    if existing:
        raise HTTPException(409, "Этот аккаунт уже подключён")
    account = Account(
        **payload.model_dump(exclude={"golden_key"}),
        golden_key=vault.encrypt(payload.golden_key),
        title=profile["username"],
        **profile,
        status="online",
        last_synced_at=utcnow(),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    await bus.log("accounts", f"Подключён аккаунт {account.username}", account.id)
    return public(account)


@api.patch("/accounts/{ident}")
async def patch_account(ident: int, payload: AccountPatch, db: AsyncSession = Depends(get_db)):
    account = await required(db, Account, ident)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("proxy_id"):
        await required(db, Proxy, changes["proxy_id"])
    if any(value is None and key != "proxy_id" for key, value in changes.items()):
        raise HTTPException(422, "Пустое значение недопустимо")
    for key, value in changes.items():
        setattr(account, key, value)
    if changes.get("is_active") is False:
        account.status = "offline"
    await db.commit()
    await workers.stop_account(ident)
    if config.workers_enabled:
        await workers.reconcile()
    return public(account)


@api.delete("/accounts/{ident}")
async def delete_account(ident: int, db: AsyncSession = Depends(get_db)):
    account = await required(db, Account, ident)
    was_active = account.is_active
    account.is_active = False
    await db.commit()
    from .account_deletion import unlink_account_dependents
    from .account_workspace import remove_account_settings
    from .steam_rentals import inventory_lock

    try:
        await workers.stop_account(ident)
        async with inventory_lock:
            await unlink_account_dependents(db, ident)
            await remove_account_settings(db, ident)
            await db.delete(account)
            await db.commit()
    except Exception:
        await db.rollback()
        account = await db.get(Account, ident)
        if account:
            account.is_active = was_active
            await db.commit()
        if was_active and config.workers_enabled:
            await workers.reconcile()
        raise
    await bus.publish("accounts", {"deleted": ident})
    return {"ok": True}


@api.post("/accounts/{ident}/sync")
async def sync_account(ident: int, db: AsyncSession = Depends(get_db)):
    await required(db, Account, ident)
    await accounts.sync(ident)
    return {"ok": True}


@api.post("/accounts/{ident}/sync-lots")
async def sync_lots(ident: int, db: AsyncSession = Depends(get_db)):
    await required(db, Account, ident)
    return {"count": await accounts.sync_lots(ident)}


@api.post("/accounts/{ident}/sync-chats")
async def sync_chats(ident: int, db: AsyncSession = Depends(get_db)):
    await required(db, Account, ident)
    return {"count": await accounts.refresh_contacts(ident)}


@api.get("/chats")
async def list_chats(
    account_id: int | None = None,
    q: str = "",
    unread: bool = False,
    active_order: bool = False,
    sort: Literal["newest", "oldest"] = "newest",
    after: datetime | None = None,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    statement = scoped(select(Chat), Chat, account_id)
    if q:
        statement = statement.where(
            Chat.interlocutor_name.ilike(f"%{q[:200]}%")
            | Chat.last_message_text.ilike(f"%{q[:200]}%")
        )
    if unread:
        statement = statement.where(Chat.unread_count > 0)
    if active_order:
        statement = statement.where(Chat.current_order_id.is_not(None))
    if after:
        if after.tzinfo:
            after = after.astimezone(timezone.utc)
        statement = statement.where(Chat.last_message_at >= after)
    rows = (
        await db.scalars(
            statement.order_by(
                case((Chat.last_message_at < datetime(1971, 1, 1), 1), else_=0),
                Chat.last_message_at.desc() if sort == "newest" else Chat.last_message_at.asc(),
                Chat.id.desc(),
            )
            .offset(max(0, offset))
            .limit(min(500, max(1, limit)))
        )
    ).all()
    return [public(row) for row in rows]


@api.get("/chats/{ident}/messages")
async def messages(ident: int, before: int | None = None, db: AsyncSession = Depends(get_db)):
    await required(db, Chat, ident)
    statement = select(ChatMessage).where(ChatMessage.chat_id == ident)
    if before:
        statement = statement.where(ChatMessage.id < before)
    rows = (await db.scalars(statement.order_by(ChatMessage.id.desc()).limit(100))).all()
    return [public(row) for row in reversed(rows)]


@api.post("/chats/{ident}/refresh")
async def refresh_chat(ident: int):
    await accounts.refresh_chat(ident)
    return {"ok": True}


@api.post("/chats/{ident}/messages")
async def send_message(ident: int, payload: MessageCreate):
    result = await accounts.send(ident, payload.text, idempotency_key=payload.idempotency_key)
    return {"ok": True, **(result or {})}


@api.post("/chats/{ident}/image")
async def send_image(ident: int, image: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    chat = await required(db, Chat, ident)
    content = await image.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(413, "Изображение больше 8 МБ")
    try:
        with Image.open(io.BytesIO(content)) as parsed:
            if (
                parsed.format not in {"PNG", "JPEG", "WEBP", "GIF"}
                or parsed.width * parsed.height > 25000000
            ):
                raise HTTPException(422, "Неподдерживаемый формат или слишком большое изображение")
            mime = Image.MIME[parsed.format]
            parsed.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(422, "Файл не является допустимым изображением") from error
    client = await accounts.client(chat.account_id)
    image_id = await client.upload_image(content, "upload." + mime.split("/")[1], mime)
    await accounts.send(ident, f"[img:{image_id}]")
    return {"ok": True}


@api.patch("/chats/{ident}")
async def patch_chat(ident: int, payload: ChatPatch, db: AsyncSession = Depends(get_db)):
    chat = await required(db, Chat, ident)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(chat, key, value)
    await db.commit()
    return public(chat)


@api.get("/lots")
async def list_lots(account_id: int | None = None, db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(scoped(select(Lot), Lot, account_id).order_by(Lot.id).limit(10000))
        ).all()
    ]


@api.get("/lots/editor")
async def new_lot_editor(account_id: int, node_id: int, db: AsyncSession = Depends(get_db)):
    await required(db, Account, account_id)
    metadata, _ = await load_form(await accounts.client(account_id), node_id=node_id)
    return metadata


@api.get("/lots/{ident}/editor")
async def existing_lot_editor(ident: int, db: AsyncSession = Depends(get_db)):
    lot = await required(db, Lot, ident)
    metadata, _ = await load_form(await accounts.client(lot.account_id), offer_id=lot.funpay_lot_id)
    return metadata


@api.get("/lots/quote")
async def lot_quote(
    account_id: int, node_id: int, price: Decimal, db: AsyncSession = Depends(get_db)
):
    await required(db, Account, account_id)
    if node_id <= 0:
        raise HTTPException(422, "Укажите категорию")
    return await quote_prices(await accounts.client(account_id), node_id, price)


@api.post("/lots/create")
async def create_lots(payload: LotCreate, db: AsyncSession = Depends(get_db)):
    await required(db, Account, payload.account_id)
    client = await accounts.client(payload.account_id)
    result = await create_offers(client, payload.node_id, payload.count, payload.fields)
    try:
        await accounts.sync_lots(payload.account_id)
    except FunPayError:
        result["sync_pending"] = True
    return result


@api.post("/lots/{ident}/copies")
async def copy_own_lot(ident: int, payload: LotCopies, db: AsyncSession = Depends(get_db)):
    lot = await required(db, Lot, ident)
    client = await accounts.client(lot.account_id)
    metadata, source = await load_form(client, offer_id=lot.funpay_lot_id)
    source = merge_fields(metadata, source, payload.fields)
    changes = {
        control["name"]: source.get(control["name"], "")
        for control in metadata["controls"]
        if not control.get("readonly")
    }
    # Copies get their own stock; duplicating a key pool could sell the same key twice.
    if "secrets" in changes:
        changes["secrets"] = ""
        if "auto_delivery" in changes:
            changes["auto_delivery"] = ""
    result = await create_offers(
        client, lot.node_id, payload.count, changes, images=source.get("fields[images]")
    )
    try:
        await accounts.sync_lots(lot.account_id)
    except FunPayError:
        result["sync_pending"] = True
    return result


@api.put("/lots/{ident}")
async def edit_lot(ident: int, payload: LotSave, db: AsyncSession = Depends(get_db), dry_run: bool = False):
    lot = await required(db, Lot, ident)
    client = await accounts.client(lot.account_id)
    async with client.offer_lock(lot.funpay_lot_id):
        metadata, fields = await load_form(client, offer_id=lot.funpay_lot_id)
        changes = merge_fields(metadata, fields, payload.fields)
        if dry_run:
            return {"ok": True, "dry_run": True,
                    "changed_fields": [key for key in payload.fields if changes[key] != fields.get(key)]}
        await save_form(client, changes)
    if "active" in payload.fields and await db.scalar(
        select(PluginBinding.id).where(
            PluginBinding.kind == "kosell", PluginBinding.lot_id == lot.id
        )
    ):
        from .kosell_sync import manual_override

        await manual_override(lot.id)
    try:
        await accounts.sync_lots(lot.account_id)
    except FunPayError:
        return {"ok": True, "sync_pending": True}
    return {"ok": True}


async def remove_remote_lot(lot, db):
    if await db.scalar(select(PluginBinding.id).where(PluginBinding.lot_id == lot.id).limit(1)):
        raise FunPayError("Сначала удалите привязку этого лота в настройках плагина")
    client = await accounts.client(lot.account_id)
    async with client.offer_lock(lot.funpay_lot_id):
        _, fields = await load_form(client, offer_id=lot.funpay_lot_id)
        fields["deleted"] = "1"
        await save_form(client, fields)
    await db.delete(lot)
    await db.commit()


@api.delete("/lots/{ident}")
async def delete_lot(ident: int, db: AsyncSession = Depends(get_db)):
    lot = await required(db, Lot, ident)
    await remove_remote_lot(lot, db)
    await bus.publish("lots", {"deleted": ident})
    return {"ok": True}


@api.post("/lots/bulk")
async def bulk_lots(payload: LotBulk, db: AsyncSession = Depends(get_db)):
    if payload.dry_run and payload.action != "raise":
        raise HTTPException(422, "Dry-run поддерживается только для поднятия")
    results, raised = [], {}
    stop_reason = None
    for index, ident in enumerate(payload.ids):
        lot = await db.get(Lot, ident)
        base = {
            "id": ident,
            "title": lot.title if lot else f"Лот #{ident}",
            "funpay_lot_id": lot.funpay_lot_id if lot else "",
        }
        if stop_reason:
            results.append(
                {
                    **base,
                    "error": "Не выполнялось: пакет остановлен после ошибки подключения или подтверждения FunPay",
                    "skipped": True,
                }
            )
            continue
        if not lot:
            results.append({**base, "error": "Лот не найден. Обновите список"})
            continue
        try:
            if payload.action == "raise":
                group = (lot.account_id, lot.node_id, lot.game_id)
                if group not in raised:
                    raised[group] = (await accounts.raise_group(*group, dry_run=True)
                                     if payload.dry_run else await accounts.raise_group(*group))
                result = raised[group]
                results.append({**base, **result, "ok": bool(result.get("raised") or result.get("dry_run"))})
                continue
            if payload.action == "delete":
                await remove_remote_lot(lot, db)
                results.append({**base, "ok": True, "deleted": True})
                continue
            if payload.action.startswith("auto_raise"):
                lot.auto_raise = payload.action == "auto_raise_on"
            else:
                client = await accounts.client(lot.account_id)
                if payload.action == "price":
                    price = (lot.price * (1 + Decimal(str(payload.percent)) / 100)).quantize(
                        Decimal("0.01")
                    )
                    if price <= 0:
                        raise FunPayError("Цена должна быть положительной")
                    await client.update_lot(lot.funpay_lot_id, price=price)
                    lot.price = price
                else:
                    active = payload.action == "activate"
                    await client.update_lot(lot.funpay_lot_id, active=active)
                    lot.is_active = active
                    if await db.scalar(
                        select(PluginBinding.id).where(
                            PluginBinding.kind == "kosell", PluginBinding.lot_id == lot.id
                        )
                    ):
                        from .kosell_sync import manual_override

                        await manual_override(lot.id)
            await db.commit()
            results.append({**base, "ok": True})
            # Бережный темп: FunPay ограничивает частоту запросов,
            # сотни запросов подряд вызывают отказ формы лота.
            if not payload.action.startswith("auto_raise") and index + 1 < len(payload.ids):
                await asyncio.sleep(0.7)
        except FunPayError as error:
            results.append({**base, "error": str(error)})
            if payload.action == "raise":
                raised[(lot.account_id, lot.node_id, lot.game_id)] = {
                    "error": str(error),
                    "raised": False,
                }
            if isinstance(error, (UnauthorizedException, ChallengeException, ResponseFormatError)):
                stop_reason = str(error)
        except IntegrityError:
            await db.rollback()
            results.append(
                {
                    **base,
                    "error": "FunPay подтвердил операцию, но локальные связи требуют проверки. Синхронизируйте лоты перед повтором",
                    "sync_pending": True,
                }
            )
            stop_reason = "local database conflict"
    await bus.publish("lots", {"updated": sum(bool(r.get("ok")) for r in results)})
    return results


@api.get("/proxies")
async def list_proxies(db: AsyncSession = Depends(get_db)):
    return [public(row) for row in (await db.scalars(select(Proxy).order_by(Proxy.id))).all()]


@api.post("/proxies", status_code=201)
async def add_proxies(payload: ProxyBatch, db: AsyncSession = Depends(get_db)):
    lines = [line.strip() for line in payload.lines.splitlines() if line.strip()]
    if len(lines) > 100:
        raise HTTPException(422, "За один раз можно добавить до 100 прокси")
    try:
        rows = [Proxy(**parse_proxy(line)) for line in lines]
    except ValueError as error:
        raise HTTPException(
            422,
            "Неверный формат прокси. Используйте protocol://user:password@host:port",
        ) from error
    db.add_all(rows)
    await db.commit()
    return {"count": len(rows)}


@api.post("/proxies/check")
async def check_proxies():
    await workers.proxies()
    return {"ok": True}


@api.delete("/proxies/{ident}")
async def delete_proxy(ident: int, db: AsyncSession = Depends(get_db)):
    row = await required(db, Proxy, ident)
    bound = (await db.scalars(select(Account.id).where(Account.proxy_id == ident))).all()
    await db.delete(row)
    await db.commit()
    for account_id in bound:
        await workers.stop_account(account_id)
    return {"ok": True}


def public_rule(rule: AutoDeliveryRule):
    data = public(rule)
    pool = vault.decrypt(rule.keys_pool) if rule.keys_pool else ""
    data["remaining"] = len(pool.splitlines())
    return data


@api.get("/rules")
async def list_rules(account_id: int | None = None, db: AsyncSession = Depends(get_db)):
    return [
        public_rule(row)
        for row in (
            await db.scalars(
                scoped(select(AutoDeliveryRule), AutoDeliveryRule, account_id).order_by(
                    AutoDeliveryRule.id
                )
            )
        ).all()
    ]


@api.post("/rules", status_code=201)
async def add_rule(payload: RuleCreate, db: AsyncSession = Depends(get_db)):
    async with automation.delivery_lock:
        return await _add_rule(payload, db)


async def _add_rule(payload: RuleCreate, db: AsyncSession):
    from .inventory import ensure_keys_available
    await required(db, Account, payload.account_id)
    if payload.match_regex:
        try:
            re.compile(payload.lot_title_pattern)
        except re.error as error:
            raise HTTPException(422, "Некорректное регулярное выражение") from error
    data = payload.model_dump()
    keys = [line.strip() for line in payload.keys_pool.splitlines() if line.strip()]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "В пуле есть повторяющиеся ключи")
    if payload.delivery_type == "pool_keys" and not keys:
        raise HTTPException(422, "Добавьте хотя бы один ключ")
    data["keys_pool"] = vault.encrypt("\n".join(keys)) if keys else ""
    await ensure_keys_available(db, keys)
    rule = AutoDeliveryRule(**data)
    db.add(rule)
    await db.commit()
    return public_rule(rule)


@api.post("/rules/{ident}/toggle")
async def toggle_rule(ident: int, db: AsyncSession = Depends(get_db)):
    rule = await required(db, AutoDeliveryRule, ident)
    rule.is_active = not rule.is_active
    await db.commit()
    return public_rule(rule)


@api.post("/rules/{ident}/keys")
async def append_keys(ident: int, payload: KeyPoolAppend):
    from .inventory import ensure_keys_available
    async with automation.delivery_lock:
        async with Session() as db:
            rule = await required(db, AutoDeliveryRule, ident)
            if rule.delivery_type != "pool_keys":
                raise HTTPException(422, "Это правило не использует пул ключей")
            old = vault.decrypt(rule.keys_pool).splitlines() if rule.keys_pool else []
            new = [line.strip() for line in payload.keys.splitlines() if line.strip()]
            if not new or len(set(old + new)) != len(old + new):
                raise HTTPException(422, "Пустой список или повторяющиеся ключи")
            await ensure_keys_available(db, new)
            rule.keys_pool = vault.encrypt("\n".join(old + new))
            await db.commit()
            return public_rule(rule)


@api.delete("/rules/{ident}")
async def delete_rule(ident: int, db: AsyncSession = Depends(get_db)):
    await db.delete(await required(db, AutoDeliveryRule, ident))
    await db.commit()
    return {"ok": True}


@api.get("/blacklist")
async def blacklist(db: AsyncSession = Depends(get_db)):
    return [
        {**public(row), "scope": blacklist_scope(row)}
        for row in (
            await db.scalars(select(BlacklistEntry).order_by(BlacklistEntry.id.desc()))
        ).all()
    ]


@api.get("/blacklist/candidates")
async def blacklist_candidates(account_id: int, db: AsyncSession = Depends(get_db)):
    await required(db, Account, account_id)
    rows = (
        await db.scalars(
            select(Chat)
            .where(Chat.account_id == account_id, Chat.interlocutor_id > 0)
            .order_by(Chat.last_message_at.desc())
            .limit(500)
        )
    ).all()
    return [
        {
            "chat_id": row.id,
            "user_id": row.interlocutor_id,
            "username": row.interlocutor_name,
            "avatar_url": row.interlocutor_avatar,
        }
        for row in rows
    ]


@api.post("/blacklist/import")
async def import_blacklist(payload: BlacklistImport, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(
            select(Chat).where(
                Chat.account_id == payload.account_id,
                Chat.id.in_(set(payload.chat_ids)),
                Chat.interlocutor_id > 0,
            )
        )
    ).all()
    if len(rows) != len(set(payload.chat_ids)):
        raise HTTPException(422, "Часть покупателей не найдена в переписке этого аккаунта")
    existing = set(
        (
            await db.scalars(
                select(BlacklistEntry.user_id).where(
                    BlacklistEntry.account_id.is_(None)
                    if payload.scope == "global"
                    else BlacklistEntry.account_id == payload.account_id,
                    BlacklistEntry.chat_id.is_(None),
                )
            )
        ).all()
    )
    unique = {
        row.interlocutor_id: row.interlocutor_name
        for row in rows
        if row.interlocutor_id not in existing
    }
    db.add_all(
        BlacklistEntry(
            user_id=user_id,
            username=name,
            reason="Импорт из переписки FunPay",
            account_id=payload.account_id if payload.scope == "account" else None,
        )
        for user_id, name in unique.items()
    )
    await db.commit()
    return {"added": len(unique)}


@api.post("/blacklist", status_code=201)
async def add_blacklist(payload: BlacklistCreate, db: AsyncSession = Depends(get_db)):
    values = payload.model_dump(exclude={"scope"})
    if payload.scope == "account":
        await required(db, Account, payload.account_id)
    elif payload.scope == "chat":
        chat = await required(db, Chat, payload.chat_id)
        if chat.interlocutor_id != payload.user_id:
            raise HTTPException(422, "Покупатель не совпадает с выбранным диалогом")
        if payload.account_id is not None and payload.account_id != chat.account_id:
            raise HTTPException(422, "Диалог принадлежит другому аккаунту")
        values["account_id"] = chat.account_id
    if await db.scalar(
        select(BlacklistEntry.id).where(
            BlacklistEntry.user_id == payload.user_id,
            BlacklistEntry.account_id == values["account_id"]
            if values["account_id"] is not None
            else BlacklistEntry.account_id.is_(None),
            BlacklistEntry.chat_id == values["chat_id"]
            if values["chat_id"] is not None
            else BlacklistEntry.chat_id.is_(None),
        )
    ):
        raise HTTPException(409, "Такое исключение уже существует")
    row = BlacklistEntry(**values)
    db.add(row)
    await db.commit()
    return {**public(row), "scope": blacklist_scope(row)}


@api.delete("/blacklist/{ident}")
async def delete_blacklist(ident: int, db: AsyncSession = Depends(get_db)):
    await db.delete(await required(db, BlacklistEntry, ident))
    await db.commit()
    return {"ok": True}


@api.get("/loyalty/balances")
async def loyalty_balances(account_id: int | None = None, db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(
                scoped(select(BonusBalance), BonusBalance, account_id)
                .order_by(BonusBalance.points.desc())
                .limit(500)
            )
        ).all()
    ]


@api.get("/loyalty/rewards")
async def loyalty_rewards(db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(
                select(BonusEntry)
                .where(BonusEntry.kind == "reward", BonusEntry.state == "uncertain")
                .order_by(BonusEntry.id.desc())
                .limit(100)
            )
        ).all()
    ]


@api.post("/loyalty/rewards/{ident}/resolve")
async def resolve_reward(ident: int, delivered: bool):
    try:
        await resolve_bonus(ident, delivered=delivered)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@api.get("/promotions")
async def promotions(account_id: int | None = None, db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(
                scoped(select(LotPromotion), LotPromotion, account_id)
                .order_by(LotPromotion.id.desc())
                .limit(200)
            )
        ).all()
    ]


@api.post("/promotions")
async def add_promotion(payload: PromotionCreate):
    try:
        result = await create_promotion(payload.lot_id, payload.percent, payload.hours)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return public(result)


@api.post("/promotions/{ident}/restore")
async def end_promotion(ident: int):
    try:
        await restore_promotion(ident)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@api.post("/promotions/{ident}/recover")
async def check_promotion(ident: int):
    try:
        return {"state": await recover_promotion(ident)}
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


def finance_since(period: str):
    durations = {
        "hour": timedelta(hours=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
        "year": timedelta(days=365),
    }
    if period not in durations:
        raise HTTPException(422, "Недопустимый период")
    return utcnow() - durations[period]


@api.get("/finance/summary")
async def finance_summary(
    period: str = "month", account_id: int | None = None, db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.scalars(
            scoped(
                select(Order).where(
                    Order.created_at >= finance_since(period),
                    Order.date_source != "history_unknown",
                    Order.status != "refunded",
                ),
                Order,
                account_id,
            )
        )
    ).all()
    totals = {cur: Decimal(0) for cur in ("RUB", "USD", "EUR")}
    for row in rows:
        if row.currency in totals:
            totals[row.currency] += row.sum
    return {"totals": {key: str(value) for key, value in totals.items()}, "count": len(rows)}


@api.get("/orders")
async def orders(
    account_id: int | None = None, period: str | None = None, db: AsyncSession = Depends(get_db)
):
    statement = scoped(select(Order), Order, account_id)
    if period:
        statement = statement.where(
            Order.created_at >= finance_since(period), Order.date_source != "history_unknown"
        )
    return [
        public(row)
        for row in (await db.scalars(statement.order_by(Order.created_at.desc()).limit(1000))).all()
    ]


@api.get("/orders/{ident}/delivery")
async def reserved_delivery(ident: int, response: Response, db: AsyncSession = Depends(get_db)):
    order = await required(db, Order, ident)
    response.headers["Cache-Control"] = "no-store"
    return {
        "state": order.delivery_state,
        "text": vault.decrypt(order.delivery_text) if order.delivery_text else "",
    }


@api.post("/orders/{ident}/confirm-delivery")
async def confirm_local_delivery(ident: int):
    async with automation.delivery_lock:
        async with Session() as db:
            order = await required(db, Order, ident)
            if order.delivery_state not in {"sending", "uncertain"}:
                raise HTTPException(409, "Этот заказ не ожидает ручной проверки выдачи")
            order.auto_delivered = True
            order.delivery_state = "sent_manual"
            await db.commit()
    await bus.log(
        "orders", f"Выдача #{order.funpay_order_id} подтверждена вручную", order.account_id
    )
    return {"ok": True}


@api.get("/dashboard")
async def dashboard(
    account_id: int | None = None,
    period: str = "days",
    db: AsyncSession = Depends(get_db),
):
    settings = await get_setting("app", {})
    zone = ZoneInfo(settings.get("timezone", "Europe/Tallinn"))
    now = utcnow().astimezone(zone)
    account_rows = (
        await db.scalars(
            select(Account).where(Account.id == account_id) if account_id else select(Account)
        )
    ).all()
    order_rows = (
        await db.scalars(
            scoped(
                select(Order).where(Order.created_at >= utcnow() - timedelta(days=365)),
                Order,
                account_id,
            )
        )
    ).all()
    balance = {
        cur: str(
            sum(
                (getattr(row, f"balance_{cur.lower()}") for row in account_rows),
                Decimal(0),
            )
        )
        for cur in ("RUB", "USD", "EUR")
    }
    today = {cur: Decimal(0) for cur in balance}
    monthly = {cur: Decimal(0) for cur in balance}
    count = 0
    points = {}
    step = "hours" if period == "hours" else "months" if period == "months" else "days"
    for index in range(24 if step == "hours" else 12 if step == "months" else 30):
        if step == "hours":
            stamp = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - index)
            key = stamp.strftime("%Y-%m-%d %H")
            label = stamp.strftime("%H:00")
        elif step == "months":
            number = now.year * 12 + now.month - 1 - (11 - index)
            key = f"{number // 12:04d}-{number % 12 + 1:02d}"
            label = key
        else:
            stamp = now - timedelta(days=29 - index)
            key = stamp.strftime("%Y-%m-%d")
            label = stamp.strftime("%d.%m")
        points[key] = {"label": label, "RUB": 0.0, "USD": 0.0, "EUR": 0.0}
    for order in order_rows:
        if order.date_source == "history_unknown":
            # The list page does not provide a reliable UTC creation time for imported history.
            # Never turn an initial import into fictitious "today's sales".
            continue
        if order.status == "refunded" or order.currency not in balance:
            continue
        stamp = order.created_at.replace(tzinfo=utcnow().tzinfo).astimezone(zone)
        if stamp.date() == now.date():
            today[order.currency] += order.sum
            count += 1
        if stamp.year == now.year and stamp.month == now.month:
            monthly[order.currency] += order.sum * (
                1 - Decimal(str(settings.get("commission_percent", 0))) / 100
            )
        key = stamp.strftime(
            "%Y-%m-%d %H" if step == "hours" else "%Y-%m" if step == "months" else "%Y-%m-%d"
        )
        if key in points:
            points[key][order.currency] += float(order.sum)
    activities = (
        await db.scalars(
            scoped(select(Activity), Activity, account_id).order_by(Activity.id.desc()).limit(10)
        )
    ).all()
    return {
        "balances": balance,
        "today": {key: str(value) for key, value in today.items()},
        "today_count": count,
        "monthly_net": {key: str(value) for key, value in monthly.items()},
        "commission_percent": settings.get("commission_percent", 0),
        "online": sum(row.status == "online" for row in account_rows),
        "total": len(account_rows),
        "errors": sum(row.status == "error" for row in account_rows),
        "series": list(points.values()),
        "activities": [public(row) for row in activities],
    }


@api.get("/health")
async def health():
    start = time.perf_counter()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {
        "ok": True,
        "database": "postgresql" if config.database_url.startswith("postgres") else "sqlite-wal",
        "database_ms": round((time.perf_counter() - start) * 1000, 1),
        "workers": len([task for task in workers.tasks.values() if not task.done()]),
        "scheduler": workers.scheduler.running,
        "subscribers": len(bus.subscribers),
    }


@api.get("/logs")
async def logs(account_id: int | None = None, db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(
                scoped(select(Activity), Activity, account_id)
                .order_by(Activity.id.desc())
                .limit(200)
            )
        ).all()
    ]


@api.get("/plugins/fulfillment")
async def fulfillment_status(db: AsyncSession = Depends(get_db)):
    settings = await get_setting(fulfillment.SETTINGS_KEY, {})
    discord = settings.get("discord_boost", {})
    steam = settings.get("steam_gift", {})
    rows = (
        await db.execute(
            select(FulfillmentTask, Order)
            .join(Order, FulfillmentTask.order_id == Order.id)
            .order_by(FulfillmentTask.id.desc())
            .limit(100)
        )
    ).all()
    return {
        "settings": {
            "discord_boost": {
                "enabled": discord.get("enabled", False),
                "lot_phrase": discord.get("lot_phrase", ""),
                "boost_count": discord.get("boost_count", 2),
                "success_message": discord.get("success_message", "Бусты активированы!"),
            },
            "steam_gift": {
                "enabled": steam.get("enabled", False),
                "lot_phrase": steam.get("lot_phrase", ""),
                "seller_steamid": steam.get("seller_steamid", ""),
                "api_key": "",
                "has_api_key": bool(steam.get("api_key")),
                "success_message": steam.get("success_message", "Подарок отправлен!"),
            },
        },
        "tasks": [
            {**public(task), "order_number": order.funpay_order_id, "buyer_name": order.buyer_name}
            for task, order in rows
        ],
    }


@api.get("/plugins/catalog")
async def plugins_catalog():
    return await plugin_catalog.catalog()


@api.put("/plugins/catalog/{ident}")
async def plugin_toggle(ident: str, payload: AutoDumpPatch):
    try:
        return await plugin_catalog.toggle(ident, payload.is_enabled)
    except KeyError:
        raise HTTPException(404, "Плагин не найден") from None
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@api.put("/plugins/fulfillment")
async def fulfillment_settings(payload: FulfillmentSettings):
    data = payload.model_dump(mode="json")
    for kind in ("discord_boost", "steam_gift"):
        if data[kind]["enabled"] and not data[kind]["lot_phrase"].strip():
            raise HTTPException(422, "Укажите фразу для распознавания лота")
    discord_phrase = data["discord_boost"]["lot_phrase"].strip().casefold()
    steam_phrase = data["steam_gift"]["lot_phrase"].strip().casefold()
    if (
        data["discord_boost"]["enabled"]
        and data["steam_gift"]["enabled"]
        and (discord_phrase in steam_phrase or steam_phrase in discord_phrase)
    ):
        raise HTTPException(422, "Фразы лотов пересекаются; укажите разные товары")
    seller = data["steam_gift"]["seller_steamid"]
    if seller and not re.fullmatch(r"7656119\d{10}", seller):
        raise HTTPException(422, "Нужен SteamID64 продавца")
    previous = await get_setting(fulfillment.SETTINGS_KEY, {})
    key = data["steam_gift"]["api_key"]
    data["steam_gift"]["api_key"] = (
        vault.encrypt(key) if key else previous.get("steam_gift", {}).get("api_key", "")
    )
    await save_setting(fulfillment.SETTINGS_KEY, data)
    return {"ok": True}


fulfillment_settings_lock = asyncio.Lock()


@api.put("/plugins/fulfillment/{kind}/settings")
async def scoped_fulfillment_settings(kind: str, payload: dict):
    from pydantic import ValidationError

    if kind not in {"discord_boost", "steam_gift"}:
        raise HTTPException(404, "Плагин выдачи не найден")
    async with fulfillment_settings_lock:
        previous = await get_setting(fulfillment.SETTINGS_KEY, {})
        data = FulfillmentSettings().model_dump()
        for key in data:
            data[key].update(previous.get(key, {}))
        data["steam_gift"]["api_key"] = ""
        data[kind].update(payload)
        data[kind]["enabled"] = previous.get(kind, {}).get("enabled", False)
        try:
            model = FulfillmentSettings.model_validate(data)
        except ValidationError:
            raise HTTPException(422, "Проверьте поля настроек выдачи") from None
        return await fulfillment_settings(model)


@api.put("/plugins/fulfillment/{ident}/target")
async def fulfillment_target(ident: int, payload: FulfillmentTarget):
    try:
        await fulfillment.set_target(ident, payload.value)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"ok": True}


@api.post("/plugins/fulfillment/{ident}/resolve")
async def resolve_fulfillment(ident: int):
    try:
        await fulfillment.resolve_sent(ident)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True}


@api.post("/plugins/fulfillment/{ident}/report/{key}")
async def fulfillment_report(ident: int, key: str):
    try:
        await fulfillment.report_issue(ident, key)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True}


@api.post("/plugins/fulfillment/{ident}/verify-invite")
async def fulfillment_invite(ident: int):
    try:
        return {"guild_id": await fulfillment.verify_invite(ident)}
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "Discord недоступен") from error


@api.post("/plugins/fulfillment/{ident}/friend-requested")
async def fulfillment_friend_requested(ident: int):
    try:
        await fulfillment.mark_friend_requested(ident)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"ok": True}


@api.post("/plugins/fulfillment/{ident}/confirm")
async def fulfillment_confirm(ident: int):
    try:
        await fulfillment.confirm_sent(ident)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"ok": True}


@api.get("/plugins/lzt")
async def lzt_status(db: AsyncSession = Depends(get_db)):
    settings = await get_setting(lzt_market.SETTINGS_KEY, {})
    app_config = await get_setting("app", {})
    try:
        local_zone = ZoneInfo(app_config.get("timezone", "Europe/Tallinn"))
    except (KeyError, ValueError):
        local_zone = timezone.utc
    today = (
        datetime.now(local_zone)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    purchased = (
        await db.scalars(
            select(MarketPurchase).where(
                MarketPurchase.status == "confirmed", MarketPurchase.confirmed_at >= today
            )
        )
    ).all()
    rows = (
        await db.scalars(select(MarketPurchase).order_by(MarketPurchase.id.desc()).limit(100))
    ).all()
    return {
        "settings": {
            "filter_url": settings.get("filter_url", ""),
            "max_price": settings.get("max_price", "100"),
            "reserve_balance": settings.get("reserve_balance", "0"),
            "enabled": settings.get("enabled", False),
            "has_token": bool(settings.get("token")),
        },
        "running": bool(lzt_market.worker.task and not lzt_market.worker.task.done()),
        "last_error": lzt_market.worker.last_error,
        "balance": str(lzt_market.worker.balance)
        if lzt_market.worker.balance is not None
        else None,
        "today_count": len(purchased),
        "today_spent": str(sum((row.price for row in purchased), Decimal(0))),
        "purchases": [public(row, exclude={"credentials"}) for row in rows],
    }


@api.put("/plugins/lzt")
async def lzt_settings(payload: MarketBuyerSettings):
    try:
        await lzt_market.save_config(payload.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"ok": True}


@api.put("/plugins/lzt/connection")
async def lzt_connection(payload: MarketConnection):
    settings = await get_setting(lzt_market.SETTINGS_KEY, {})
    settings["token"] = vault.encrypt(payload.token)
    await lzt_market.worker.stop()
    await save_setting(lzt_market.SETTINGS_KEY, settings)
    await lzt_market.worker.start()
    return {"ok": True}


@api.post("/plugins/lzt/test")
async def lzt_test():
    settings = await get_setting(lzt_market.SETTINGS_KEY, {})
    if not settings.get("token"):
        raise HTTPException(422, "Сначала сохраните ключ LZT")
    client = lzt_market.worker._client or lzt_market.MarketClient(vault.decrypt(settings["token"]))
    own_client = client is not lzt_market.worker._client
    try:
        balance = lzt_market.balance_rub(await client.request("GET", "/balance/exchange"))
        return {"balance": str(balance)}
    except (lzt_market.MarketError, ValueError):
        raise HTTPException(502, "LZT не подтвердил доступ. Проверьте ключ и журнал.") from None
    finally:
        if own_client:
            await client.close()


@api.get("/plugins/lzt/purchases/{ident}/credentials")
async def lzt_purchase_credentials(ident: int, db: AsyncSession = Depends(get_db)):
    row = await required(db, MarketPurchase, ident)
    if row.status != "confirmed" or not row.credentials:
        raise HTTPException(404, "Учётные данные недоступны")
    return json.loads(vault.decrypt(row.credentials))


@api.get("/plugins/email-code")
async def email_code_status():
    saved = await get_setting("imap_mailbox_default", None)
    return {
        "configured": bool(saved),
        "connected": email_codes.mailbox_connected(),
        "host": saved.get("host", "") if saved else "",
        "port": saved.get("port", 993) if saved else 993,
        "username": saved.get("username", "") if saved else "",
        "has_password": bool(saved and saved.get("password")),
    }


async def mailbox_payload(payload: EmailMailboxSettings) -> email_codes.MailboxConfig:
    password = payload.password
    if not password:
        try:
            existing = await email_codes.load_mailbox("default")
            password = existing.password
        except ValueError:
            raise HTTPException(422, "Укажите пароль приложения") from None
    return email_codes.MailboxConfig(
        ident="default",
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=password,
    )


@api.put("/plugins/email-code")
async def save_email_code(payload: EmailMailboxSettings):
    config = await mailbox_payload(payload)
    try:
        await email_codes.restart_mailbox(config)
    except Exception as error:
        raise HTTPException(
            503, "Не удалось сохранить почту в системном хранилище ключей"
        ) from error
    return {"ok": True, "connected": email_codes.mailbox_connected()}


@api.post("/plugins/email-code/test")
async def test_email_code(payload: EmailMailboxSettings):
    config = await mailbox_payload(payload)

    def probe():
        client = email_codes.connect(config)
        try:
            client.select_folder(config.folder, readonly=True)
        finally:
            client.shutdown()

    try:
        await asyncio.to_thread(probe)
    except Exception as error:
        raise HTTPException(502, "IMAP: соединение или авторизация не удались") from error
    return {"ok": True}


@api.get("/settings/{key}")
async def read_settings(key: str):
    defaults = {
        "app": AppSettings().model_dump(),
        "plugins": PluginSettings().model_dump(),
        "loyalty": LoyaltySettings().model_dump(mode="json"),
        "telegram": TelegramSettings().model_dump(),
    }
    if key not in defaults:
        raise HTTPException(404, "Раздел настроек не найден")
    result = await get_setting(key, defaults[key])
    if key == "telegram":
        result = {**result, "token": "", "has_token": bool(result.get("token"))}
    return result


@api.put("/settings/app")
async def app_settings(payload: AppSettings):
    await save_setting("app", payload.model_dump())
    return {"ok": True}


@api.put("/settings/plugins")
async def plugin_settings(payload: PluginSettings):
    if payload.reply_delay_min > payload.reply_delay_max:
        raise HTTPException(422, "Минимальная задержка больше максимальной")
    try:
        re.compile(payload.keywords)
        for rule in payload.ticket_templates:
            re.compile(rule.pattern)
    except re.error as error:
        raise HTTPException(422, "Некорректное регулярное выражение") from error
    await save_setting("plugins", payload.model_dump())
    return {"ok": True}


plugin_settings_lock = asyncio.Lock()


@api.put("/plugins/{ident}/settings")
async def scoped_plugin_settings(ident: str, payload: dict):
    from pydantic import ValidationError

    fields = {
        "responder": {
            "first_message",
            "commands",
            "reply_delay_min",
            "reply_delay_max",
            "keywords",
            "keyword_reply",
            "night_start",
            "night_end",
            "night_reply",
        },
        "auto-ticket": {"ticket_templates"},
        "reviews": {"review_replies", "thanks_text"},
    }
    if ident not in fields:
        raise HTTPException(404, "Настройки плагина не найдены")
    async with plugin_settings_lock:
        previous = {**PluginSettings().model_dump(), **(await get_setting("plugins", {}))}
        previous.update({key: value for key, value in payload.items() if key in fields[ident]})
        try:
            model = PluginSettings.model_validate(previous)
        except ValidationError:
            raise HTTPException(422, "Проверьте значения настроек и шаблонов") from None
        await plugin_settings(model)
    return {"ok": True}


@api.put("/settings/loyalty")
async def loyalty_settings(payload: LoyaltySettings):
    await save_setting("loyalty", payload.model_dump(mode="json"))
    return {"ok": True}


@api.put("/settings/telegram")
@api.patch("/settings/telegram")
async def telegram_settings(payload: TelegramSettings, request: Request):
    from .telegram_control import fingerprint, lock, sign_session, update_menu

    changes = payload.model_dump(exclude_unset=True)
    replacement_session = ""
    async with lock:
        previous = await get_setting("telegram", {})
        if previous.get("token"):
            from .services import scope_legacy_telegram_links

            await scope_legacy_telegram_links(vault.decrypt(previous["token"]))
        data = TelegramSettings.model_validate({**previous, "token": "", **changes}).model_dump()
        token_changed = bool(payload.token and (not previous.get("token") or vault.decrypt(previous["token"]) != payload.token))
        me = await telegram.request(payload.token, "getMe", {}) if token_changed else None
        data["token"] = vault.encrypt(payload.token) if payload.token else previous.get("token", "")
        if me:
            await save_setting("telegram_bot", {"bot": fingerprint(payload.token), "username": me["username"]})
            owner = await get_setting("telegram_owner", {})
            if previous.get("token") and owner.get("bot") == fingerprint(vault.decrypt(previous["token"])):
                owner = {**owner, "bot": fingerprint(payload.token), "generation": secrets.token_hex(16)}
                await save_setting("telegram_owner", owner)
                if data["control_enabled"] and request.headers.get("authorization", "").startswith("Bearer tg1."):
                    replacement_session = sign_session(owner)
            await save_setting("telegram_pair", {})
        await save_setting("telegram", data)
        if data["mini_app_url"]:
            await save_setting("telegram_public_lock", True)
    if data["token"] and previous.get("mini_app_url", "") != data["mini_app_url"]:
        try:
            await update_menu(vault.decrypt(data["token"]), data)
        except FunPayError:
            pass
    await bus.publish("notifications", {"event": "TELEGRAM_SETTINGS"})
    return {**data, "token": "", "has_token": bool(data["token"]), **({"session_token": replacement_session} if replacement_session else {})}


@api.post("/telegram/test")
async def telegram_test():
    await telegram.notify("errors", "FunPay Plus: тестовое уведомление ✓", force=True)
    return {"ok": True}


@api.get("/copy-lots/preview")
async def copy_preview(account_id: int, seller_id: int):
    if seller_id <= 0:
        raise HTTPException(422, "Укажите ID продавца")
    rows = await (await accounts.client(account_id)).get_lots(seller_id)
    return jsonable_encoder(rows)


@api.get("/copy-lots/form")
async def copy_editor(account_id: int, source_id: int):
    if source_id <= 0:
        raise HTTPException(422, "Укажите ID исходного предложения")
    preview, _fields = await copy_form(await accounts.client(account_id), source_id)
    return preview


@api.post("/copy-lots/publish")
async def copy_publish(payload: CopyPublish):
    result = await publish_copy(
        await accounts.client(payload.account_id),
        payload.source_id,
        payload.title,
        payload.description,
        payload.price,
        payload.amount,
        payload.extra,
    )
    await bus.log(
        "lots", f"FunPay принял копию предложения #{payload.source_id}", payload.account_id
    )
    return result


@api.get("/auto-dump/rules")
async def dump_rules(db: AsyncSession = Depends(get_db)):
    return [
        {**public(row), "options": json.loads(row.options or "{}")}
        for row in (await db.scalars(select(AutoDumpRule).order_by(AutoDumpRule.id))).all()
    ]


@api.post("/auto-dump/rules", status_code=201)
async def dump_add_rule(payload: AutoDumpCreate, db: AsyncSession = Depends(get_db)):
    lot = await required(db, Lot, payload.lot_id)
    count = len((await db.scalars(select(AutoDumpRule.id))).all())
    if count >= 50:
        raise HTTPException(422, "Доступно не более 50 товаров")
    try:
        path = auto_dump.category_path(payload.category_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not path.startswith(f"/lots/{lot.node_id}/"):
        raise HTTPException(422, "Категория ссылки не совпадает с категорией лота")
    data = payload.model_dump(exclude={"options"})
    data["options"] = json.dumps(payload.options.model_dump(mode="json"))
    if payload.options.max_price is not None and payload.options.max_price < payload.absolute_min:
        raise HTTPException(422, "Максимальная цена ниже абсолютного минимума")
    row = AutoDumpRule(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return public(row)


@api.patch("/auto-dump/rules/{ident}")
async def dump_toggle(ident: int, payload: AutoDumpPatch, db: AsyncSession = Depends(get_db)):
    row = await required(db, AutoDumpRule, ident)
    row.is_enabled = payload.is_enabled
    await db.commit()
    return public(row)


@api.put("/auto-dump/rules/{ident}")
async def dump_edit_rule(ident: int, payload: AutoDumpCreate, db: AsyncSession = Depends(get_db)):
    row = await required(db, AutoDumpRule, ident)
    if row.lot_id != payload.lot_id:
        raise HTTPException(422, "Для другого лота создайте отдельное правило")
    lot = await required(db, Lot, payload.lot_id)
    try:
        path = auto_dump.category_path(payload.category_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not path.startswith(f"/lots/{lot.node_id}/"):
        raise HTTPException(422, "Категория ссылки не совпадает с лотом")
    if payload.options.max_price is not None and payload.options.max_price < payload.absolute_min:
        raise HTTPException(422, "Максимальная цена ниже абсолютного минимума")
    for key, value in payload.model_dump(exclude={"options"}).items():
        setattr(row, key, value)
    row.options = json.dumps(payload.options.model_dump(mode="json"))
    row.status, row.last_error = "idle", None
    await db.commit()
    return {"ok": True}


@api.delete("/auto-dump/rules/{ident}")
async def dump_delete(ident: int, db: AsyncSession = Depends(get_db)):
    await db.delete(await required(db, AutoDumpRule, ident))
    await db.commit()
    return {"ok": True}


@api.post("/auto-dump/scan")
async def dump_scan_now():
    await auto_dump.scan_all()
    return {"ok": True}


@api.get("/auto-robux/settings")
async def robux_settings():
    return await auto_robux.public_settings()


@api.put("/auto-robux/settings")
async def save_robux_settings(payload: RobuxSettings):
    try:
        return await auto_robux.configure(payload.model_dump())
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@api.put("/auto-robux/connection")
async def robux_connection(payload: RobuxConnection):
    saved = await auto_robux.public_settings()
    saved.pop("has_cookie", None)
    try:
        return await auto_robux.configure({**saved, "cookie": payload.cookie})
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@api.post("/auto-robux/verify")
async def robux_verify():
    try:
        return await auto_robux.verify_pass()
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@api.get("/app-route/suppliers")
async def route_suppliers(db: AsyncSession = Depends(get_db)):
    return [
        {**public(row), "has_token": bool(row.token)}
        for row in (
            await db.scalars(select(AppRouteSupplier).order_by(AppRouteSupplier.id.desc()))
        ).all()
    ]


@api.post("/app-route/suppliers", status_code=201)
async def route_add_supplier(payload: RouteSupplierCreate, db: AsyncSession = Depends(get_db)):
    try:
        endpoint = app_route.validate_endpoint(payload.endpoint_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    row = AppRouteSupplier(
        name=payload.name,
        api_type=payload.api_type,
        endpoint_url=endpoint,
        token=vault.encrypt(payload.token) if payload.token else None,
        is_active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return public(row)


@api.put("/app-route/suppliers/{ident}")
async def route_edit_supplier(
    ident: int, payload: RouteSupplierUpdate, db: AsyncSession = Depends(get_db)
):
    row = await required(db, AppRouteSupplier, ident)
    try:
        endpoint = app_route.validate_endpoint(payload.endpoint_url)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    row.name, row.api_type, row.endpoint_url = payload.name, payload.api_type, endpoint
    row.is_active = payload.is_active
    if payload.token:
        row.token = vault.encrypt(payload.token)
    await db.commit()
    return public(row)


@api.delete("/app-route/suppliers/{ident}")
async def route_delete_supplier(ident: int, db: AsyncSession = Depends(get_db)):
    row = await required(db, AppRouteSupplier, ident)
    if await db.scalar(select(AppRouteMapping.id).where(AppRouteMapping.supplier_id == ident)):
        raise HTTPException(409, "Сначала удалите связи с товарами")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@api.get("/app-route/mappings")
async def route_mappings(db: AsyncSession = Depends(get_db)):
    return [
        public(row)
        for row in (
            await db.scalars(select(AppRouteMapping).order_by(AppRouteMapping.id.desc()))
        ).all()
    ]


@api.post("/app-route/mappings", status_code=201)
async def route_add_mapping(payload: RouteMappingCreate, db: AsyncSession = Depends(get_db)):
    await required(db, Account, payload.account_id)
    await required(db, AppRouteSupplier, payload.supplier_id)
    if await db.scalar(
        select(PluginBinding.id)
        .join(Lot)
        .where(Lot.account_id == payload.account_id, Lot.funpay_lot_id == payload.funpay_item_id)
    ):
        raise HTTPException(409, "Этот лот уже привязан к плагину выдачи")
    row = AppRouteMapping(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return public(row)


@api.put("/app-route/mappings/{ident}")
async def route_edit_mapping(
    ident: int, payload: RouteMappingCreate, db: AsyncSession = Depends(get_db)
):
    row = await required(db, AppRouteMapping, ident)
    await required(db, Account, payload.account_id)
    await required(db, AppRouteSupplier, payload.supplier_id)
    if not await db.scalar(
        select(Lot.id).where(
            Lot.account_id == payload.account_id, Lot.funpay_lot_id == payload.funpay_item_id
        )
    ):
        raise HTTPException(
            422, "Лот не найден у выбранного аккаунта. Сначала синхронизируйте лоты."
        )
    if await db.scalar(
        select(PluginBinding.id)
        .join(Lot)
        .where(Lot.account_id == payload.account_id, Lot.funpay_lot_id == payload.funpay_item_id)
    ):
        raise HTTPException(409, "Этот лот уже привязан к плагину выдачи")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    await db.commit()
    return public(row)


@api.delete("/app-route/mappings/{ident}")
async def route_delete_mapping(ident: int, db: AsyncSession = Depends(get_db)):
    await db.delete(await required(db, AppRouteMapping, ident))
    await db.commit()
    return {"ok": True}


@api.get("/app-route/deliveries")
async def route_deliveries(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AppRouteDelivery, Order)
            .join(Order)
            .order_by(AppRouteDelivery.id.desc())
            .limit(100)
        )
    ).all()
    return [
        {**public(row), "order_number": order.funpay_order_id, "buyer": order.buyer_name}
        for row, order in rows
    ]


@api.get("/offline/settings")
async def offline_settings():
    return OfflineSettings.model_validate(await get_setting("offline", {}))


@api.put("/offline/settings")
async def save_offline_settings(payload: OfflineSettings):
    await save_setting("offline", payload.model_dump())
    return payload


@api.post("/offline/prepare-shutdown")
async def prepare_shutdown(db: AsyncSession = Depends(get_db)):
    if not (await get_setting("plugin_controls", {})).get("offline", True):
        return {"ok": True, "hidden": 0}
    settings = await get_setting("offline", {})
    if not settings.get("hide_on_exit"):
        return {"ok": True, "hidden": 0}
    lots = (
        await db.scalars(
            select(Lot).join(Account).where(Lot.is_active.is_(True), Account.is_active.is_(True))
        )
    ).all()
    hidden = 0
    for lot in lots:
        try:
            await (await accounts.client(lot.account_id)).update_lot(
                lot.funpay_lot_id, active=False
            )
        except FunPayError as error:
            await bus.log(
                "lots",
                f"Не удалось скрыть лот #{lot.funpay_lot_id}: {error}",
                lot.account_id,
                "error",
            )
            raise HTTPException(
                502, f"Лот #{lot.funpay_lot_id} не скрыт. Выход отменён."
            ) from error
        lot.is_active = False
        hidden += 1
        await db.commit()
    return {"ok": True, "hidden": hidden}


@api.post("/offline/pause-local")
async def pause_local(request: Request):
    """Drain desktop workers before switching to VPS; do not hide remote offers."""
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "Остановка доступна только локальному desktop-клиенту")
    from .runtime import stop_background_tasks

    config.workers_enabled = False
    await stop_background_tasks()
    return {"ok": True}


app.include_router(template_router)
app.include_router(rental_router)
app.include_router(integrated_router)
app.include_router(kosell_router)
app.include_router(kosell_workspace_router)
app.include_router(kosellrent_router)
app.include_router(plugin_workspace_router)

app.include_router(account_workspace_router)
app.include_router(diagnostics_router)
app.include_router(telegram_control_router)
app.include_router(api)


socket_tickets: dict[str, float] = {}
socket_ticket_sessions: dict[str, str] = {}


@app.post("/api/ws-ticket", dependencies=[Depends(require_auth)])
async def ws_ticket(request: Request):
    now = time.monotonic()
    for key, expiry in list(socket_tickets.items()):
        if expiry < now:
            del socket_tickets[key]
            socket_ticket_sessions.pop(key, None)
    ticket = secrets.token_urlsafe(32)
    socket_tickets[ticket] = now + 30
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if token.startswith("tg1."):
        socket_ticket_sessions[ticket] = token
    return {"ticket": ticket}


@app.websocket("/api/ws")
async def websocket(socket: WebSocket):
    token = socket.headers.get("authorization", "").removeprefix("Bearer ")
    origin = socket.headers.get("origin", "")
    allowed = allowed_origins()
    same_origin = origin.removeprefix("https://").removeprefix("http://") == socket.headers.get(
        "host"
    )
    ticket_expiry = socket_tickets.pop(socket.query_params.get("ticket", ""), 0)
    telegram_session = socket_ticket_sessions.pop(socket.query_params.get("ticket", ""), "")
    is_authorized = ticket_expiry > time.monotonic() or authorized(
        token, socket.cookies.get("pulse_session")
    )
    if telegram_session:
        from .telegram_control import authorized_session

        is_authorized = is_authorized and await authorized_session(telegram_session)
    if not is_authorized or (origin and origin not in allowed and not same_origin):
        await socket.close(code=1008)
        return
    await socket.accept()
    queue = bus.subscribe(
        socket,
        socket.query_params.get(
            "channels", "accounts,chats,notifications,logs,lots,orders,loyalty,tickets"
        ).split(","),
    )

    async def send_events():
        next_telegram_check = 0.0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), 25)
            except asyncio.TimeoutError:
                event = {"channel": "ping", "data": {"time": time.time()}}
            if telegram_session and (time.monotonic() >= next_telegram_check or event.get("data", {}).get("event") in {"TELEGRAM_SETTINGS", "TELEGRAM_PAIRED", "TELEGRAM_ACCESS_CHANGED"}):
                if not await authorized_session(telegram_session):
                    await socket.close(code=1008)
                    return
                next_telegram_check = time.monotonic() + 5
            await socket.send_json(jsonable_encoder(event))

    sender = asyncio.create_task(send_events())
    try:
        while True:
            payload = await socket.receive_text()
            if len(payload) > 4096:
                await socket.close(code=1009)
                break
    except WebSocketDisconnect:
        return
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await sender
        bus.remove(socket)


web_dist = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "apps" / "web" / "dist"
if web_dist.is_dir():

    class SPAFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            normalized_path = path.replace("\\", "/").lstrip("/")
            if normalized_path.startswith("api/"):
                raise StarletteHTTPException(404, "API endpoint not found")
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as error:
                if (
                    error.status_code != 404
                    or "." in normalized_path.split("/")[-1]
                ):
                    raise
                return await super().get_response("index.html", scope)

    app.mount("/", SPAFiles(directory=web_dist, html=True), name="web")
