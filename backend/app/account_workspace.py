"""Authenticated account control panels and unified response configuration."""

import asyncio
from datetime import timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from . import reply_config as rc
from .db import Session
from .funpay import FunPayError
from .funpay_gateway import FunPayGateway as FunPayClient
from .models import Account, Chat, Lot, Order, Proxy, ReplyEvent, Settings, utcnow
from .schemas import AccountCreate
from .security import require_auth, vault
from .services import accounts, get_setting, save_setting

router = APIRouter(prefix="/api/workspaces", dependencies=[Depends(require_auth)])


class CoreToggle(BaseModel):
    is_enabled: bool


@router.put("/automation/{feature}")
async def toggle_core(feature: Literal["responder", "reviews"], body: CoreToggle):
    async with rc.lock:
        current = await get_setting("plugins", {})
        current["responder" if feature == "responder" else "auto_thanks"] = body.is_enabled
        await save_setting("plugins", current)
    return {"is_enabled": body.is_enabled}


async def account_rows():
    app = await get_setting("app", {})
    now = utcnow().astimezone(ZoneInfo(app.get("timezone", "Europe/Tallinn")))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    from .reply_engine import configured

    structured = await configured()
    async with Session() as db:
        from .main import public

        rows = (await db.scalars(select(Account).order_by(Account.id))).all()
        proxies = {p.id: p for p in (await db.scalars(select(Proxy))).all()}
        today = (
            await db.execute(
                select(Order.account_id, Order.currency, func.sum(Order.sum), func.count())
                .where(
                    Order.created_at >= start,
                    Order.date_source == "observed",
                    Order.status != "refunded",
                )
                .group_by(Order.account_id, Order.currency)
            )
        ).all()
        sales = {}
        for aid, code, total, count in today:
            sales.setdefault(aid, {})[code] = {"sum": str(total), "count": count}
        lot_flags = dict(
            (
                await db.execute(
                    select(Lot.account_id, func.count())
                    .where(Lot.auto_raise.is_(True))
                    .group_by(Lot.account_id)
                )
            ).all()
        )
        pending = dict(
            (
                await db.execute(
                    select(Order.account_id, func.count())
                    .where(Order.status == "paid")
                    .group_by(Order.account_id)
                )
            ).all()
        )
        master = await get_setting("plugins", {})
        result = []
        for account in rows:
            cfg = await rc.policy(account.id)
            if not await get_setting(f"account-automation:{account.id}", None):
                cfg["auto_raise"] = bool(lot_flags.get(account.id))
            proxy = proxies.get(account.proxy_id)
            result.append(
                {
                    **public(account),
                    "today": sales.get(account.id, {}),
                    "orders_in_work": pending.get(account.id, 0),
                    "automation": cfg,
                    "responder_enabled": bool(master.get("responder")),
                    "ticket_enabled": bool(master.get("auto_ticket")),
                    "review_master": bool(master.get("auto_thanks")),
                    "structured_replies": structured,
                    "proxy_label": f"{proxy.host}:{proxy.port}"
                    if proxy
                    else "Напрямую, без прокси",
                }
            )
    return result


@router.get("/accounts")
async def list_accounts():
    return await account_rows()


@router.get("/accounts/{ident}")
async def account_detail(ident: int):
    result = next((r for r in await account_rows() if r["id"] == ident), None)
    if not result:
        raise HTTPException(404, "Аккаунт не найден")
    return result


@router.put("/accounts/{ident}/automation")
async def save_policy(ident: int, body: rc.AccountPolicy):
    try:
        rc.validate(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with rc.lock, Session() as db:
        account = await db.get(Account, ident)
        if not account:
            raise HTTPException(404, "Аккаунт не найден")
        previous = await get_setting(f"account-automation:{ident}", None)
        previous_flag = (
            previous.get("auto_raise", False)
            if previous
            else bool(
                await db.scalar(
                    select(Lot.id).where(Lot.account_id == ident, Lot.auto_raise.is_(True)).limit(1)
                )
            )
        )
        if previous_flag != body.auto_raise:
            for lot in (await db.scalars(select(Lot).where(Lot.account_id == ident))).all():
                lot.auto_raise = body.auto_raise
            await db.commit()
        data = {
            **body.model_dump(),
            "version": 2,
            "enabled_at": (previous or {}).get("enabled_at", utcnow().isoformat()),
        }
        await save_setting(f"account-automation:{ident}", data)
        if body.review_reply:
            master = await get_setting("plugins", {})
            master["auto_thanks"] = True
            await save_setting("plugins", master)
    return data


class KeyUpdate(BaseModel):
    golden_key: str = Field(min_length=16, max_length=512)


@router.put("/accounts/{ident}/key")
async def update_key(ident: int, body: KeyUpdate):
    from .config import config
    from .services import proxy_url

    async with Session() as db:
        account = await db.get(Account, ident)
        if not account:
            raise HTTPException(404, "Аккаунт не найден")
        try:
            valid = AccountCreate(
                golden_key=body.golden_key, user_agent=account.user_agent, proxy_id=account.proxy_id
            )
        except ValidationError:
            raise HTTPException(422, "Проверьте формат golden_key") from None
        proxy = await db.get(Proxy, account.proxy_id) if account.proxy_id else None
        client = FunPayClient(
            valid.golden_key,
            account.user_agent,
            proxy_url(proxy) if proxy else None,
            config.funpay_transport,
        )
        try:
            profile = await client.sync_profile()
        except FunPayError:
            raise HTTPException(400, "Не удалось проверить ключ FunPay") from None
        finally:
            await client.close()
        if profile["funpay_id"] != account.funpay_id:
            raise HTTPException(409, "Ключ принадлежит другому аккаунту. Добавьте его отдельно")
        account.golden_key = vault.encrypt(valid.golden_key)
        for name, value in profile.items():
            setattr(account, name, value)
        account.status = "online"
        account.last_error = None
        account.last_synced_at = utcnow()
        await db.commit()
    from .main import workers

    await workers.stop_account(ident)
    if config.workers_enabled:
        await workers.reconcile()
    return {"ok": True}


class AccountIDs(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=200)


@router.post("/accounts/check")
async def check_accounts(body: AccountIDs):
    async with Session() as db:
        ids = (
            await db.scalars(
                select(Account.id).where(Account.id.in_(body.ids))
                if body.ids
                else select(Account.id)
            )
        ).all()
    semaphore = asyncio.Semaphore(3)

    async def check(ident):
        async with semaphore:
            try:
                await accounts.sync(ident)
                return {"id": ident, "ok": True}
            except Exception:
                async with Session() as db:
                    account = await db.get(Account, ident)
                    if account:
                        account.status = "error"
                        account.last_error = "Проверьте ключ, соединение и прокси"
                        await db.commit()
                return {
                    "id": ident,
                    "error": "Не удалось проверить подключение. Проверьте ключ или прокси",
                }

    return await asyncio.gather(*(check(i) for i in ids))


@router.get("/responder")
async def responder_workspace():
    from .reply_engine import configured

    master = await get_setting("plugins", {})
    async with Session() as db:
        handoffs = []
        for entry in (
            await db.scalars(select(Settings).where(Settings.key.like("reply:handoff:%")))
        ).all():
            import json

            value = json.loads(entry.value)
            if not value.get("paused"):
                continue
            chat = await db.get(Chat, int(entry.key.rsplit(":", 1)[1]))
            if chat:
                handoffs.append(
                    {
                        "chat_id": chat.id,
                        "account_id": chat.account_id,
                        "buyer": chat.interlocutor_name,
                        **value,
                    }
                )
        requests = []
        for row in (
            await db.scalars(
                select(ReplyEvent)
                .where(ReplyEvent.kind == "refund")
                .order_by(ReplyEvent.id.desc())
                .limit(100)
            )
        ).all():
            order = await db.get(Order, row.order_id) if row.order_id else None
            requests.append(
                {
                    "id": row.id,
                    "state": row.state,
                    "order_id": order.funpay_order_id if order else "",
                    "buyer": order.buyer_name if order else "",
                    "amount": str(order.sum) if order else "",
                    "currency": order.currency if order else "RUB",
                    "account_id": row.account_id,
                }
            )
        events = [
            {
                k: getattr(e, k)
                for k in (
                    "id",
                    "account_id",
                    "chat_id",
                    "order_id",
                    "kind",
                    "state",
                    "created_at",
                    "due_at",
                )
            }
            for e in (
                await db.scalars(select(ReplyEvent).order_by(ReplyEvent.id.desc()).limit(100))
            ).all()
        ]
    return {
        "settings": await rc.config(),
        "enabled": bool(master.get("responder")),
        "reviews_running": bool(master.get("auto_thanks")),
        "configured": await configured(),
        "accounts": await account_rows(),
        "handoffs": handoffs,
        "requests": requests,
        "events": events,
        "tags": rc.TAGS,
    }


@router.put("/responder")
async def save_responder(body: rc.ReplyConfig):
    try:
        rc.validate(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    async with rc.lock, Session() as db:
        if body.account_scope == "selected":
            existing = set(
                (await db.scalars(select(Account.id).where(Account.id.in_(body.account_ids)))).all()
            )
            missing = set(body.account_ids) - existing
            if missing:
                raise HTTPException(422, "Выбранный аккаунт больше не существует")
        previous = await get_setting("responder:v2", {})
        effective = await rc.config()
        data = body.model_dump()
        key = data.pop("ai_key").strip()
        data["ai_key"] = vault.encrypt(key) if key else previous.get("ai_key", "")
        if data["ai_enabled"] and (not data["ai_key"] or not data["ai_model"]):
            raise HTTPException(422, "Укажите API-ключ и модель AI")
        data.update(version=2, enabled_at=previous.get("enabled_at", utcnow().isoformat()))
        await save_setting("responder:v2", data)
        master = await get_setting("plugins", {})
        if body.reviews_enabled != effective["reviews_enabled"]:
            master["auto_thanks"] = body.reviews_enabled
            await save_setting("plugins", master)
    return await rc.config()


@router.post("/responder/handoffs/{ident}/resume")
async def resume_handoff(ident: int):
    async with Session() as db:
        if not await db.get(Chat, ident):
            raise HTTPException(404, "Диалог не найден")
    await save_setting(f"reply:handoff:{ident}", {"paused": False})
    return {"ok": True}


class Decision(BaseModel):
    action: Literal["approve", "reject"]


@router.post("/responder/refunds/{ident}")
async def refund_decision(ident: int, body: Decision):
    from .reply_requests import decide

    try:
        await decide(ident, body.action)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True}


@router.post("/responder/refunds/{ident}/reconcile")
async def refund_reconcile(ident: int):
    async with Session() as db:
        row = await db.get(ReplyEvent, ident)
        if not row or row.kind != "refund" or row.state != "uncertain":
            raise HTTPException(409, "Сверка не требуется")
        order = await db.get(Order, row.order_id)
        detail = await (await accounts.client(order.account_id)).order_details(
            order.funpay_order_id
        )
        if detail.get("status") == "refunded":
            row.state = "refunded"
            order.status = "refunded"
            await db.commit()
        else:
            raise HTTPException(
                409, "FunPay не подтверждает возврат. Повторное списание заблокировано"
            )
    return {"ok": True}


@router.get("/notifications")
async def notification_status():
    cfg = await get_setting("telegram", {})
    return {
        "telegram": {
            "enabled": bool(cfg.get("enabled")),
            "configured": bool(cfg.get("token") and cfg.get("chat_id")),
            "recipient": cfg.get("chat_id", ""),
            "events": cfg.get("events", []),
        }
    }


async def remove_account_settings(db, ident):
    """Prevent a later account/chat ID reuse from inheriting an old policy or pause."""
    import json

    chat_ids = (await db.scalars(select(Chat.id).where(Chat.account_id == ident))).all()
    keys = [f"account-automation:{ident}", *(f"reply:handoff:{cid}" for cid in chat_ids)]
    for entry in (await db.scalars(select(Settings).where(Settings.key.in_(keys)))).all():
        await db.delete(entry)
    # A reused numeric seller ID must never inherit supplier credentials/rentals.
    for entry in (await db.scalars(select(Settings).where(Settings.key.startswith(f"kosell-rent:state:{ident}:")))).all():
        await db.delete(entry)
    plugin = await db.get(Settings, "kosell-rent")
    if plugin:
        data = json.loads(plugin.value)
        if data.get("account_id") == ident:
            plugin.value = json.dumps({"enabled": False, "account_id": None})
    from . import kosellrent

    kosellrent.instances.pop(ident, None)
    entry = await db.get(Settings, "responder:v2")
    if entry:
        data = json.loads(entry.value)
        data["account_ids"] = [value for value in data.get("account_ids", []) if value != ident]
        entry.value = json.dumps(data, ensure_ascii=False)
