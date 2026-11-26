"""Plugin-specific activity, with bounded cursor pagination and no global log leakage."""

import base64
import hashlib
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select

from .db import Session
from .integrated_config import packed, unpacked
from .integrated_schemas import KINDS
from .models import Activity, OfflineGuardAccount
from .plugin_catalog import IDS
from .security import authorized, require_auth
from .steam_security import guard_code

router = APIRouter(prefix="/api/plugins", dependencies=[Depends(require_auth)])


def mutation_plugins(path):
    parts = path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "api":
        return []
    if parts[1] == "plugins" and len(parts) > 2:
        if parts[2] in {"catalog", "integrated"}:
            ident = parts[3] if len(parts) > 3 else ""
        elif parts[2] == "fulfillment":
            return ["discord-boost", "steam-gift"]
        else:
            ident = parts[2]
    else:
        ident = parts[1]
    return [ident] if ident in IDS | KINDS else []


async def plugin_activity(request, call_next):
    response = await call_next(request)
    if response.status_code < 400 and request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(("/api/settings/", "/api/plugins/", "/api/workspaces/", "/api/accounts/", "/api/lots/")):
        from .events import bus

        await bus.publish("notifications", {"event": "CONFIG_CHANGED"})
    ids = (
        mutation_plugins(request.url.path)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}
        else []
    )
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    from .telegram_control import authorized_session

    if ids and (authorized(token, request.cookies.get("pulse_session")) or await authorized_session(token)):
        from .events import bus

        if request.url.path.startswith("/api/plugins/fulfillment/"):
            from .models import FulfillmentTask

            part = request.url.path.split("/")[4]
            if part in {"discord_boost", "steam_gift"}:
                ids = [part.replace("_", "-")]
            elif part.isdigit():
                async with Session() as db:
                    task = await db.get(FulfillmentTask, int(part))
                    ids = [task.kind.replace("_", "-")] if task else []

        action = next(
            (part for part in reversed(request.url.path.split("/")) if part and not part.isdigit()),
            "",
        )
        messages = {
            "settings": "Настройки сохранены",
            "connection": "Подключение сохранено",
            "test": "Проверка подключения завершена",
            "target": "Получатель обновлён",
            "confirm": "Выдача подтверждена",
            "resolve": "Ручное выполнение подтверждено",
            "bindings": "Привязки товаров обновлены",
            "inventory": "Пул аккаунтов обновлён",
            "recover": "Доступ аккаунта восстановлен",
            "suppliers": "Поставщики обновлены",
            "mappings": "Связи товаров обновлены",
            "scan": "Проверка цен выполнена",
            "verify": "Проверка выполнена",
            "send": "Обращение отправлено",
            "finish": "Завершение аренды запущено",
        }
        if response.status_code < 400:
            message = (
                "Запись удалена"
                if request.method == "DELETE"
                else messages.get(action, "Параметры плагина обновлены")
            )
            level = "info"
        else:
            message, level = (
                f"Операция не выполнена: HTTP {response.status_code}. Подробности показаны в форме.",
                "error" if response.status_code >= 500 else "warning",
            )
        for ident in ids:
            try:
                await bus.log("logs", message, level=level, source_plugin=ident)
            except Exception as error:
                # The operation has already happened; logging must not turn it
                # into a failed response that could trigger an unsafe retry.
                import logging

                logging.getLogger(__name__).error(
                    "Plugin journal write failed (%s)", type(error).__name__
                )
    return response


@router.get("/{plugin_id}/logs")
async def logs(
    plugin_id: str,
    level: Literal["", "info", "warning", "error"] = "",
    q: str = Query(default="", max_length=200),
    before: int | None = Query(default=None, gt=0),
):
    if plugin_id not in IDS | KINDS:
        raise HTTPException(404, "Плагин не найден")
    statement = select(Activity).where(Activity.source_plugin == plugin_id)
    if level:
        statement = statement.where(Activity.level == level)
    if q.strip():
        statement = statement.where(Activity.text.contains(q.strip(), autoescape=True))
    if before is not None:
        statement = statement.where(Activity.id < before)
    async with Session() as db:
        rows = (await db.scalars(statement.order_by(Activity.id.desc()).limit(101))).all()
    items = rows[:100]
    return {
        "items": [
            {key: getattr(row, key) for key in ("id", "text", "level", "account_id", "created_at")}
            for row in items
        ],
        "next_cursor": items[-1].id if len(rows) > 100 else None,
    }


@router.delete("/{plugin_id}/logs")
async def clear_logs(plugin_id: str):
    if plugin_id not in IDS | KINDS:
        raise HTTPException(404, "Плагин не найден")
    async with Session() as db:
        await db.execute(delete(Activity).where(Activity.source_plugin == plugin_id))
        await db.commit()
    return {"ok": True}


def guard_values(payload: dict) -> dict:
    """Разбирает maFile: коды Guard считаются локально, вход в Steam не нужен."""
    login = str(payload.get("login") or "").strip()
    mafile = str(payload.get("mafile") or "").strip()
    if not login:
        raise HTTPException(422, "Укажите логин Steam")
    try:
        data = json.loads(mafile)
    except ValueError:
        raise HTTPException(422, "maFile — некорректный JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(422, "maFile должен быть JSON-объектом")
    session = data.get("Session") or data.get("session") or {}
    shared = str(data.get("shared_secret") or "").strip()
    identity = str(data.get("identity_secret") or "").strip()
    device_id = str(data.get("device_id") or "").strip()
    steamid = str(
        session.get("SteamID")
        or session.get("SteamId")
        or session.get("steamid")
        or data.get("steamid")
        or ""
    ).strip()
    for name, secret in (("shared_secret", shared), ("identity_secret", identity)):
        try:
            if not base64.b64decode(secret, validate=True):
                raise ValueError()
        except (ValueError, base64.binascii.Error):
            raise HTTPException(
                422, f"maFile: неверное поле {name} — нужен maFile из SDA"
            ) from None
    if not device_id:
        raise HTTPException(422, "maFile: отсутствует device_id")
    if not steamid.isdigit() or not steamid.startswith("7656119"):
        raise HTTPException(422, "maFile: не найден корректный SteamID")
    values = {
        "login": login,
        "password": str(payload.get("password") or ""),
        "shared_secret": shared,
        "identity_secret": identity,
        "device_id": device_id,
        "steamid": steamid,
    }
    return values


@router.get("/offline/guard/accounts")
async def guard_accounts():
    async with Session() as db:
        rows = (await db.scalars(select(OfflineGuardAccount).order_by(OfflineGuardAccount.id))).all()
    return [
        {
            "id": row.id,
            "label": row.label,
            "login": row.login,
            "steamid": unpacked(row.credentials).get("steamid", ""),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/offline/guard/accounts", status_code=201)
async def add_guard_account(payload: dict):
    label = str(payload.get("label") or "").strip()
    if not label:
        raise HTTPException(422, "Укажите название аккаунта")
    if len(label) > 150:
        raise HTTPException(422, "Название длиннее 150 символов")
    values = guard_values(payload)
    identity = hashlib.sha256(values["steamid"].encode()).hexdigest()
    async with Session() as db:
        if await db.scalar(
            select(OfflineGuardAccount.id).where(OfflineGuardAccount.identity_hash == identity)
        ):
            raise HTTPException(409, "Этот аккаунт уже добавлен")
        row = OfflineGuardAccount(
            label=label,
            login=values["login"],
            credentials=packed(values),
            identity_hash=identity,
        )
        db.add(row)
        await db.commit()
        return {"id": row.id}


@router.delete("/offline/guard/accounts/{ident}")
async def delete_guard_account(ident: int):
    async with Session() as db:
        row = await db.get(OfflineGuardAccount, ident)
        if not row:
            raise HTTPException(404, "Аккаунт не найден")
        await db.delete(row)
        await db.commit()
    return {"ok": True}


@router.get("/offline/guard/accounts/{ident}/code")
async def guard_account_code(ident: int):
    async with Session() as db:
        row = await db.get(OfflineGuardAccount, ident)
    if not row:
        raise HTTPException(404, "Аккаунт не найден")
    shared = unpacked(row.credentials).get("shared_secret", "")
    if not shared:
        raise HTTPException(409, "У аккаунта нет maFile с shared_secret")
    now = int(time.time())
    return {"code": guard_code(shared, now), "seconds": 30 - (now % 30)}


@router.get("/offline/guard/accounts/{ident}/credentials")
async def guard_account_credentials(ident: int):
    async with Session() as db:
        row = await db.get(OfflineGuardAccount, ident)
    if not row:
        raise HTTPException(404, "Аккаунт не найден")
    values = unpacked(row.credentials)
    return {
        "label": row.label,
        "login": values.get("login", ""),
        "password": values.get("password", ""),
        "steamid": values.get("steamid", ""),
    }
