from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .db import Session
from .models import (
    Account,
    AutoDeliveryRule,
    DeliveryReservation,
    IncomingEvent,
    MessageSend,
    Order,
)
from .security import require_auth

router = APIRouter(prefix="/api/automation", dependencies=[Depends(require_auth)])


@router.get("/status")
async def status():
    from .reply_config import config as responder_config
    from .services import get_setting
    from .workers import workers

    master = await get_setting("plugins", {})
    responder = await responder_config()
    async with Session() as db:
        accounts = (await db.scalars(select(Account))).all()
        counts = (
            await db.execute(
                select(IncomingEvent.state, func.count()).group_by(IncomingEvent.state)
            )
        ).all()
        unknown = await db.scalar(
            select(func.count()).select_from(MessageSend).where(MessageSend.state == "UNKNOWN")
        )
        recent = (
            await db.scalars(select(IncomingEvent).order_by(IncomingEvent.id.desc()).limit(50))
        ).all()
    return {
        "accounts": [
            {
                "id": a.id,
                "username": a.username,
                **workers.health.get(a.id, {"state": "STOPPED", "last_poll": None}),
            }
            for a in accounts
        ],
        "events": dict(counts),
        "responder": {
            "enabled": bool(master.get("responder")),
            "dry_run": bool(responder["dry_run"]),
            "account_scope": responder["account_scope"],
            "account_ids": responder["account_ids"],
            "greeting_enabled": bool(responder["greeting_enabled"]),
            "commands": len(responder["commands"]),
            "keywords": len(responder["keywords"]),
        },
        "unknown_sends": unknown,
        "recent": [
            {
                "id": e.id,
                "event_key": e.event_key,
                "state": e.state,
                "reason": e.reason,
                "attempts": e.attempts,
            }
            for e in recent
        ],
    }


@router.post("/events/{ident}/retry")
async def retry(ident: int):
    from .event_pipeline import retry_failed

    try:
        await retry_failed(ident)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return {"ok": True}


@router.get("/deliveries")
async def deliveries():
    async with Session() as db:
        rows = (await db.scalars(select(DeliveryReservation).order_by(
            DeliveryReservation.id.desc()).limit(100))).all()
    return [{"id": row.id, "order_id": row.order_id, "rule_id": row.rule_id,
             "state": row.state, "created_at": row.created_at, "updated_at": row.updated_at}
            for row in rows]


class DeliveryResolution(BaseModel):
    action: Literal["confirm_sent", "release"]
    confirmed: Literal[True]
    note: str = Field(min_length=10, max_length=1000)


@router.post("/deliveries/{ident}/resolve")
async def resolve_delivery(ident: int, payload: DeliveryResolution):
    from .inventory import ensure_keys_available
    from .security import vault
    from .services import automation

    if len(payload.note.strip()) < 10:
        raise HTTPException(422, "Опишите результат проверки чата")
    async with automation.delivery_lock, Session() as db:
        row = await db.get(DeliveryReservation, ident)
        if not row:
            raise HTTPException(404, "Резерв не найден")
        if row.state != "uncertain":
            raise HTTPException(409, "Резерв уже обработан или отправка ещё выполняется")
        order = await db.get(Order, row.order_id)
        if not order or order.delivery_state != "uncertain":
            raise HTTPException(409, "Состояния заказа и резерва требуют проверки")
        if payload.action == "confirm_sent":
            row.state, order.delivery_state, order.auto_delivered = "sent", "sent", True
        else:
            if row.item:
                rule = await db.get(AutoDeliveryRule, row.rule_id) if row.rule_id else None
                if not rule or rule.account_id != order.account_id or rule.delivery_type != "pool_keys":
                    raise HTTPException(409, "Исходный пул недоступен; ключ остаётся в резерве")
                item = vault.decrypt(row.item)
                await ensure_keys_available(db, [item], exclude_reservation=row.id)
                pool = vault.decrypt(rule.keys_pool).splitlines() if rule.keys_pool else []
                rule.keys_pool = vault.encrypt("\n".join([*pool, item]))
            row.state, order.delivery_state, order.auto_delivered = "released", "cancelled", False
        row.resolution_note = vault.encrypt(payload.note.strip())
        attempt = await db.scalar(select(MessageSend).where(
            MessageSend.idempotency_key == f"delivery:{order.account_id}:{order.funpay_order_id}"))
        if attempt and attempt.state == "UNKNOWN":
            attempt.state = "MANUAL_CONFIRMED" if payload.action == "confirm_sent" else "MANUAL_RELEASED"
        await db.commit()
        return {"ok": True, "state": row.state, "order_id": order.id}
