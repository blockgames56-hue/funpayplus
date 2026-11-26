"""Owner-only Telegram control. The bot and Mini App use this installation's API/DB."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from urllib.parse import parse_qsl

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .config import config
from .db import Session
from .models import Account, Activity, Chat, Lot, Order
from .security import require_auth, require_origin, vault
from .services import get_setting, save_setting, telegram

router = APIRouter(prefix="/api/telegram")
lock = asyncio.Lock()


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:24]


async def configuration():
    settings = await get_setting("telegram", {})
    token = vault.decrypt(settings["token"]) if settings.get("token") else ""
    return settings, token


async def owner_for(settings, token):
    owner = await get_setting("telegram_owner", {})
    return (
        owner
        if settings.get("control_enabled") and token and owner.get("bot") == fingerprint(token)
        else {}
    )


def validate_init_data(raw: str, token: str, *, now=None) -> int:
    """Telegram HMAC validation; never trust initDataUnsafe or accept stale launch data."""
    try:
        if len(raw) > 16384:
            raise ValueError
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError
        received = data.pop("hash")
        check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received):
            raise ValueError
        age = (time.time() if now is None else now) - int(data["auth_date"])
        user = json.loads(data["user"])
        ident = user["id"]
        if not -30 <= age <= 900 or type(ident) is not int or ident <= 0:
            raise ValueError
        return ident
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise HTTPException(401, "Откройте Mini App заново из вашего Telegram-бота") from None


def sign_session(owner: dict) -> str:
    body = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "id": owner["id"],
                    "bot": owner["bot"],
                    "generation": owner["generation"],
                    "exp": int(time.time()) + 86400,
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        config.api_token.encode(), f"tg1.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"tg1.{body}.{signature}"


async def authorized_session(value: str | None) -> bool:
    if not value or not value.startswith("tg1.") or len(value) > 1024:
        return False
    try:
        _, body, signature = value.split(".")
        expected = hmac.new(
            config.api_token.encode(), f"tg1.{body}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return False
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        settings, token = await configuration()
        owner = await owner_for(settings, token)
        return bool(
            owner
            and data.get("id") == owner["id"]
            and data.get("bot") == owner["bot"]
            and data.get("generation") == owner["generation"]
            and time.time() < data["exp"] <= time.time() + 86401
        )
    except (ValueError, TypeError, KeyError):
        return False


async def protect_public_connect(request: Request):
    # Direct sidecar/SSH-to-backend access stays available. Reverse proxy requests
    # are remote even if a visitor forges Host or X-Forwarded-For.
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    if local:
        return
    settings, _ = await configuration()
    if (
        not config.telegram_only
        and not settings.get("mini_app_url")
        and not await get_setting("telegram_public_lock", False)
    ):
        return
    from .security import authorized

    value = request.headers.get("authorization", "").removeprefix("Bearer ")
    if (
        not local
        and not authorized(value, request.cookies.get("pulse_session"))
        and not await authorized_session(value)
    ):
        raise HTTPException(403, "Для удалённого входа откройте Mini App в вашем Telegram-боте")


class InitData(BaseModel):
    init_data: str = Field(max_length=16384)


@router.post("/session")
async def mini_session(payload: InitData, request: Request, response: Response):
    require_origin(request)
    settings, token = await configuration()
    owner = await owner_for(settings, token)
    if not owner or validate_init_data(payload.init_data, token) != owner["id"]:
        raise HTTPException(403, "Доступ разрешён только привязанному владельцу")
    response.headers["Cache-Control"] = "no-store"
    return {"session_token": sign_session(owner), "expires_in": 86400}


# Short-lived device approvals contain no configuration or bot token. Bounded in
# memory: a server restart simply requires starting the approval again.
devices: dict[str, dict] = {}


class DevicePoll(BaseModel):
    device: str = Field(max_length=80)
    secret: str = Field(max_length=100)


@router.post("/device")
async def create_device(request: Request, response: Response):
    require_origin(request)
    for key, value in list(devices.items()):
        if value["expires"] < time.time():
            devices.pop(key, None)
    settings, token = await configuration()
    owner = await owner_for(settings, token)
    info = await get_setting("telegram_bot", {})
    if not owner or not info.get("username") or info.get("bot") != fingerprint(token):
        raise HTTPException(403, "Telegram ещё не подключён")
    peer = request.client.host if request.client else "unknown"
    if len(devices) >= 100 or sum(d["peer"] == peer for d in devices.values()) >= 5:
        raise HTTPException(429, "Слишком много запросов входа. Подождите несколько минут.")
    ident, secret = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
    devices[ident] = {
        "hash": hashlib.sha256(secret.encode()).hexdigest(),
        "owner": owner["generation"],
        "expires": time.time() + 300,
        "peer": peer,
        "approved": False,
    }
    response.headers["Cache-Control"] = "no-store"
    return {
        "device": ident,
        "secret": secret,
        "url": f"https://t.me/{info['username']}?start=login_{ident}",
        "expires_in": 300,
    }


@router.post("/device/poll")
async def poll_device(payload: DevicePoll, request: Request, response: Response):
    require_origin(request)
    value = devices.get(payload.device, {})
    if (
        not value
        or value["expires"] < time.time()
        or not hmac.compare_digest(
            value["hash"], hashlib.sha256(payload.secret.encode()).hexdigest()
        )
    ):
        raise HTTPException(401, "Подтверждение входа истекло")
    settings, token = await configuration()
    owner = await owner_for(settings, token)
    if not owner or value["owner"] != owner["generation"]:
        raise HTTPException(401, "Подтверждение входа отменено")
    response.headers["Cache-Control"] = "no-store"
    if not value["approved"]:
        return {"pending": True}
    devices.pop(payload.device, None)
    return {"session_token": sign_session(owner)}


@router.get("/status", dependencies=[Depends(require_auth)])
async def status():
    settings, token = await configuration()
    owner = await owner_for(settings, token)
    info = await get_setting("telegram_bot", {})
    matching = token and info.get("bot") == fingerprint(token)
    return {
        "enabled": bool(settings.get("control_enabled")),
        "configured": bool(token),
        "paired": bool(owner),
        "owner_name": owner.get("name", ""),
        "username": info.get("username", "") if matching else "",
        "mini_app_url": settings.get("mini_app_url", ""),
        "running": bool(control.task and not control.task.done()),
        "last_poll": control.last_poll,
        "error": control.error,
    }


@router.post("/pair", dependencies=[Depends(require_auth)])
async def pair(response: Response):
    async with lock:
        settings, token = await configuration()
        if not token:
            raise HTTPException(422, "Сначала укажите токен BotFather")
        me = await telegram.request(token, "getMe", {})
        await telegram.request(token, "deleteWebhook", {"drop_pending_updates": True})
        await telegram.request(
            token,
            "setMyCommands",
            {
                "commands": [
                    {"command": name, "description": label}
                    for name, label in [
                        ("menu", "Панель управления"),
                        ("status", "Состояние магазина"),
                        ("accounts", "Аккаунты"),
                        ("lots", "Лоты"),
                        ("chats", "Переписка"),
                        ("plugins", "Плагины"),
                        ("settings", "Настройки"),
                        ("logs", "Журнал"),
                    ]
                ]
            },
        )
        nonce = secrets.token_urlsafe(24)
        await save_setting(
            "telegram_pair",
            {
                "hash": hashlib.sha256(nonce.encode()).hexdigest(),
                "bot": fingerprint(token),
                "expires": time.time() + 600,
            },
        )
        await save_setting("telegram_bot", {"bot": fingerprint(token), "username": me["username"]})
        settings["control_enabled"] = True
        await save_setting("telegram", settings)
        await update_menu(token, settings)
    response.headers["Cache-Control"] = "no-store"
    return {"url": f"https://t.me/{me['username']}?start=pair_{nonce}", "expires_in": 600}


@router.post("/disconnect", dependencies=[Depends(require_auth)])
async def disconnect():
    async with lock:
        settings, _ = await configuration()
        settings["control_enabled"] = False
        await save_setting("telegram", settings)
        await save_setting("telegram_owner", {})
        await save_setting("telegram_pair", {})
    from .events import bus

    await bus.publish("notifications", {"event": "TELEGRAM_ACCESS_CHANGED"})
    return {"ok": True}


async def update_menu(token, settings):
    url = settings.get("mini_app_url")
    button = (
        {"type": "web_app", "text": "Открыть панель", "web_app": {"url": url}}
        if url
        else {"type": "commands"}
    )
    await telegram.request(token, "setChatMenuButton", {"menu_button": button})


async def internal_api(path, method="GET", body=None):
    # Reuse validation, business logic, plugin adapters and event publication.
    from .main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers={"Authorization": f"Bearer {config.api_token}"},
    ) as client:
        response = await client.request(method, "/api" + path, json=body)
    if response.is_error:
        raise ValueError("Действие не выполнено. Проверьте настройки и журнал в приложении.")
    return response.json()


def button(text, data):
    return {"text": text, "callback_data": "fp:" + data}


class TelegramControl:
    def __init__(self):
        self.task = None
        self.last_poll = None
        self.error = ""
        self.poll_lock = asyncio.Lock()
        self.actions: dict[str, dict] = {}
        self.menu_signature = ""

    async def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run(), name="telegram-control")

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self):
        while True:
            try:
                self.error = ""
                active = await self.poll_once(timeout=25)
                if not active:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log HTTP URLs or Telegram payloads: they may contain tokens.
                self.error = "Нет связи с Telegram. Проверьте токен, сеть и запуск этого бота на другом сервере."
                await asyncio.sleep(5)

    async def poll_once(self, timeout=0):
        async with self.poll_lock:
            settings, token = await configuration()
            if not token or not (settings.get("enabled") or settings.get("control_enabled")):
                return False
            menu_signature = fingerprint(token) + ":" + settings.get("mini_app_url", "")
            if settings.get("control_enabled") and self.menu_signature != menu_signature:
                await update_menu(token, settings)
                self.menu_signature = menu_signature
            offset_key = "telegram_offset:" + fingerprint(token)
            offset = await get_setting(offset_key, 0)
            updates = await telegram.request(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            self.last_poll = int(time.time())
            for update in updates:
                ident = update.get("update_id")
                if type(ident) is not int or ident < offset:
                    continue
                # At most once for destructive actions across an ambiguous restart.
                offset = ident + 1
                await save_setting(offset_key, offset)
                current, current_token = await configuration()
                if current_token != token:
                    break
                try:
                    await self.dispatch(update, current, token)
                except (ValueError, HTTPException):
                    self.error = "Последнее действие не выполнено. Проверьте журнал приложения."
            return True

    async def send(self, token, chat, text, rows=None, **extra):
        return await telegram.request(
            token,
            "sendMessage",
            {
                "chat_id": chat,
                "text": text[:4000],
                **({"reply_markup": {"inline_keyboard": rows}} if rows else {}),
                **extra,
            },
        )

    async def dispatch(self, update, settings, token):
        message = update.get("message", {})
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message", {})
        chat = message.get("chat", {})
        actor = (callback or message).get("from", {})
        if (
            chat.get("type") != "private"
            or type(actor.get("id")) is not int
            or actor["id"] <= 0
            or actor.get("id") != chat.get("id")
        ):
            return
        text = message.get("text", "")
        if not callback and text.startswith("/start pair_"):
            await self.bind(text.split("pair_", 1)[1].strip(), actor, settings, token)
            return
        owner = await owner_for(settings, token)
        allowed = owner and actor.get("id") == owner["id"]
        # Legacy notifications can still be replied to by their private recipient.
        legacy = not settings.get("control_enabled") and str(actor.get("id")) == str(
            settings.get("chat_id")
        )
        if not (allowed or legacy):
            if callback:
                await telegram.request(
                    token,
                    "answerCallbackQuery",
                    {"callback_query_id": callback["id"], "text": "Нет доступа"},
                )
            return
        if callback:
            data = callback.get("data", "")
            if data.startswith("fp:") and allowed:
                await telegram.request(
                    token, "answerCallbackQuery", {"callback_query_id": callback["id"]}
                )
                try:
                    await self.command(token, actor["id"], data[3:], settings, owner)
                except (ValueError, KeyError, TypeError):
                    await self.send(
                        token, actor["id"], "Действие не выполнено. Проверьте данные в приложении."
                    )
            elif data.startswith("reply-refund:"):
                from .reply_requests import handle_callback

                await handle_callback(callback, {**settings, "chat_id": str(actor["id"])})
            return
        if text.startswith("/") and allowed:
            if text.startswith("/start login_"):
                ident = text.split("login_", 1)[1].strip()
                value = devices.get(ident, {})
                if (
                    value.get("owner") == owner["generation"]
                    and value.get("expires", 0) > time.time()
                ):
                    await self.send(
                        token,
                        actor["id"],
                        "Разрешить вход в приложение на новом устройстве? Подтверждайте только собственный запрос.",
                        [
                            [
                                button("✅ Разрешить вход", "device:" + ident),
                                button("Отмена", "menu"),
                            ]
                        ],
                    )
                else:
                    await self.send(
                        token,
                        actor["id"],
                        "Запрос входа истёк. Повторите подключение в приложении.",
                    )
                return
            command = text.split()[0].split("@")[0][1:]
            await self.command(token, actor["id"], command, settings, owner)
            return
        reply_id = message.get("reply_to_message", {}).get("message_id")
        if reply_id and text:
            prompt = await get_setting("telegram_prompt", {})
            if (
                allowed
                and prompt.get("message") == reply_id
                and prompt.get("owner") == owner["generation"]
                and prompt.get("expires", 0) > time.time()
            ):
                await save_setting("telegram_prompt", {})
                try:
                    await internal_api(
                        f"/chats/{prompt['chat']}/messages",
                        "POST",
                        {
                            "text": text,
                            "idempotency_key": f"tg-{fingerprint(token)}-{update['update_id']}",
                        },
                    )
                    await self.send(token, actor["id"], "✓ Сообщение отправлено покупателю")
                except ValueError:
                    await self.send(
                        token,
                        actor["id"],
                        "Отправка не подтверждена. Проверьте переписку перед повтором.",
                    )
                return
            from .models import TelegramLink
            from .services import scope_legacy_telegram_links, telegram_link_scope

            await scope_legacy_telegram_links(token)
            async with Session() as db:
                link = await db.scalar(
                    select(TelegramLink).where(
                        TelegramLink.telegram_chat_id == telegram_link_scope(token, actor["id"]),
                        TelegramLink.telegram_message_id == reply_id,
                    )
                )
                chat_id = link.chat_id if link else None
            if chat_id:
                await internal_api(
                    f"/chats/{chat_id}/messages",
                    "POST",
                    {
                        "text": text,
                        "idempotency_key": f"tg-{fingerprint(token)}-{update['update_id']}",
                    },
                )

    async def bind(self, nonce, actor, settings, token):
        async with lock:
            pending = await get_setting("telegram_pair", {})
            valid = (
                settings.get("control_enabled")
                and pending.get("bot") == fingerprint(token)
                and pending.get("expires", 0) > time.time()
            )
            if not valid or not hmac.compare_digest(
                pending.get("hash", ""), hashlib.sha256(nonce.encode()).hexdigest()
            ):
                await self.send(
                    token,
                    actor["id"],
                    "Ссылка недействительна или истекла. Создайте новую в приложении.",
                )
                return
            await save_setting("telegram_pair", {})
            await save_setting(
                "telegram_owner",
                {
                    "id": actor["id"],
                    "bot": fingerprint(token),
                    "generation": secrets.token_hex(16),
                    "name": str(actor.get("first_name", "Владелец"))[:100],
                },
            )
            current, current_token = await configuration()
            if current_token != token:
                return
            current["chat_id"] = str(actor["id"])
            await save_setting("telegram", current)
        await self.send(
            token,
            actor["id"],
            "✓ Telegram подключён. Все изменения сохраняются в том же приложении на вашем сервере.",
        )
        from .events import bus

        await bus.publish("notifications", {"event": "TELEGRAM_PAIRED"})
        await self.menu(token, actor["id"], current)

    async def menu(self, token, chat, settings):
        rows = [
            [button("📊 Обзор", "status"), button("👤 Аккаунты", "accounts:0")],
            [button("📦 Лоты", "lots:0"), button("💬 Переписка", "chats:0")],
            [button("🧩 Плагины", "plugins:0"), button("⚙️ Настройки", "settings")],
            [button("📋 Журнал", "logs")],
        ]
        if settings.get("mini_app_url"):
            rows.insert(
                0, [{"text": "🚀 Открыть Mini App", "web_app": {"url": settings["mini_app_url"]}}]
            )
        await self.send(
            token,
            chat,
            "FunPay Plus · управление магазином\nДанные общие с приложением. Бот работает, пока запущен сервер или локальное приложение.",
            rows,
        )

    async def confirm(self, token, chat, owner, text, path, method, body):
        nonce = secrets.token_hex(12)
        for key, value in list(self.actions.items()):
            if value["expires"] < time.time():
                self.actions.pop(key, None)
        if len(self.actions) >= 100:
            self.actions.pop(next(iter(self.actions)))
        self.actions[nonce] = {
            "owner": owner["generation"],
            "expires": time.time() + 120,
            "path": path,
            "method": method,
            "body": body,
        }
        await self.send(
            token,
            chat,
            text,
            [[button("✅ Подтвердить", "apply:" + nonce), button("Отмена", "menu")]],
        )

    async def command(self, token, chat, command, settings, owner):
        parts = command.split(":")
        name = parts[0]
        if name == "device":
            value = devices.get(parts[1], {})
            if value.get("owner") == owner["generation"] and value.get("expires", 0) > time.time():
                value["approved"] = True
                await self.send(token, chat, "✓ Вход разрешён. Вернитесь в приложение.")
            return
        if name == "apply":
            async with lock:
                action = self.actions.pop(parts[1], {})
                if (
                    action.get("owner") != owner["generation"]
                    or action.get("expires", 0) < time.time()
                ):
                    await self.send(token, chat, "Подтверждение истекло или уже использовано.")
                    return
            result = await internal_api(action["path"], action["method"], action["body"])
            failed = isinstance(result, list) and any(row.get("error") for row in result)
            await self.send(
                token,
                chat,
                "Есть ошибки. Проверьте результат в приложении."
                if failed
                else "✓ Действие выполнено",
                [[button("Меню", "menu")]],
            )
            return
        if name in {"start", "menu", "help"}:
            await self.menu(token, chat, settings)
        elif name == "status":
            async with Session() as db:
                ac = list((await db.scalars(select(Account))).all())
                lots = await db.scalar(select(func.count()).select_from(Lot))
                orders = await db.scalar(
                    select(func.count()).select_from(Order).where(Order.status == "paid")
                )
                unread = await db.scalar(select(func.sum(Chat.unread_count))) or 0
            await self.send(
                token,
                chat,
                f"📊 Магазин\nАккаунты: {sum(a.is_active for a in ac)} / {len(ac)} активны\nЛоты: {lots}\nОплаченные заказы: {orders}\nНепрочитанные сообщения: {unread}",
                [[button("Обновить", "status"), button("Меню", "menu")]],
            )
        elif name in {"accounts", "lots", "chats", "plugins"}:
            page = max(0, min(10000, int(parts[1]) if len(parts) > 1 else 0))
            if name == "plugins":
                data = await internal_api("/plugins/catalog")
                rows = data if isinstance(data, list) else data.get("plugins", [])
            else:
                async with Session() as db:
                    model = {"accounts": Account, "lots": Lot, "chats": Chat}[name]
                    rows = list(
                        (
                            await db.scalars(
                                select(model).order_by(model.id.desc()).offset(page * 6).limit(7)
                            )
                        ).all()
                    )
            visible = rows[page * 6 : page * 6 + 6] if name == "plugins" else rows[:6]
            keys = []
            for row in visible:
                if name == "plugins":
                    keys.append(
                        [
                            button(
                                ("🟢 " if row.get("enabled") else "⚪ ")
                                + row.get("name", row["id"]),
                                "plugin:" + row["id"],
                            )
                        ]
                    )
                else:
                    label = str(
                        getattr(row, "username", None)
                        or getattr(row, "title", None)
                        or getattr(row, "interlocutor_name", None)
                        or f"#{row.id}"
                    )[:50]
                    keys.append(
                        [
                            button(
                                label,
                                {"accounts": "account", "lots": "lot", "chats": "chat"}[name]
                                + f":{row.id}",
                            )
                        ]
                    )
            nav = []
            if page:
                nav.append(button("←", f"{name}:{page - 1}"))
            if len(rows) > (page * 6 + 6 if name == "plugins" else 6):
                nav.append(button("→", f"{name}:{page + 1}"))
            if nav:
                keys.append(nav)
            keys.append([button("Меню", "menu")])
            await self.send(
                token,
                chat,
                f"{dict(accounts='👤 Аккаунты', lots='📦 Лоты', chats='💬 Переписка', plugins='🧩 Плагины')[name]} · {page + 1}"
                + ("\nПока пусто" if not visible else ""),
                keys,
            )
        elif name == "account":
            rows = await internal_api("/accounts")
            row = next((r for r in rows if r["id"] == int(parts[1])), None)
            if not row:
                raise ValueError
            target = not row["is_active"]
            await self.send(
                token,
                chat,
                f"👤 {row['username']}\nСостояние: {row['status']}\nБаланс: {row.get('balance_rub', 0)}",
                [
                    [
                        button(
                            "Запустить" if target else "Остановить",
                            f"account-state:{row['id']}:{int(target)}",
                        )
                    ],
                    [button("← Аккаунты", "accounts:0")],
                ],
            )
        elif name == "account-state":
            await self.confirm(
                token,
                chat,
                owner,
                "Изменить состояние аккаунта?",
                f"/accounts/{int(parts[1])}",
                "PATCH",
                {"is_active": bool(int(parts[2]))},
            )
        elif name == "lot":
            async with Session() as db:
                row = await db.get(Lot, int(parts[1]))
                if not row:
                    raise ValueError
                text = f"📦 {row.title}\nЦена: {row.price}\nСтатус: {'активен' if row.is_active else 'скрыт'}"
                ident = row.id
            await self.send(
                token,
                chat,
                text,
                [
                    [
                        button("Поднять", f"lot-action:{ident}:raise"),
                        button("Активировать", f"lot-action:{ident}:activate"),
                    ],
                    [
                        button("Скрыть", f"lot-action:{ident}:deactivate"),
                        button("Удалить", f"lot-action:{ident}:delete"),
                    ],
                    [button("← Лоты", "lots:0")],
                ],
            )
        elif name == "lot-action":
            if parts[2] not in {"raise", "activate", "deactivate", "delete"}:
                raise ValueError
            await self.confirm(
                token,
                chat,
                owner,
                f"Применить «{parts[2]}» к лоту #{int(parts[1])}?",
                "/lots/bulk",
                "POST",
                {"ids": [int(parts[1])], "action": parts[2]},
            )
        elif name == "chat":
            ident = int(parts[1])
            rows = await internal_api(f"/chats/{ident}/messages")
            text = "💬 Последние сообщения\n" + "\n".join(
                f"{r['sender_name']}: {r['text'][:400]}" for r in rows[-6:]
            )
            await self.send(
                token,
                chat,
                text,
                [
                    [button("Ответить", f"reply:{ident}"), button("Обновить", f"chat:{ident}")],
                    [button("← Чаты", "chats:0")],
                ],
            )
        elif name == "reply":
            msg = await self.send(
                token,
                chat,
                "Ответьте на это сообщение: текст будет отправлен покупателю.",
                reply_markup={"force_reply": True, "selective": True},
            )
            await save_setting(
                "telegram_prompt",
                {
                    "message": msg["message_id"],
                    "chat": int(parts[1]),
                    "owner": owner["generation"],
                    "expires": time.time() + 600,
                },
            )
        elif name == "plugin":
            data = await internal_api("/plugins/catalog")
            rows = data if isinstance(data, list) else data.get("plugins", [])
            row = next((r for r in rows if r["id"] == parts[1]), None)
            if not row:
                raise ValueError
            await self.send(
                token,
                chat,
                f"🧩 {row.get('name', row['id'])}\n{row.get('description', '')[:1000]}\nРасширенные настройки доступны в Mini App.",
                [
                    [
                        button(
                            "Выключить" if row.get("enabled") else "Включить",
                            f"plugin-state:{row['id']}:{0 if row.get('enabled') else 1}",
                        )
                    ],
                    [button("← Плагины", "plugins:0")],
                ],
            )
        elif name == "plugin-state":
            await self.confirm(
                token,
                chat,
                owner,
                "Изменить состояние плагина?",
                f"/plugins/catalog/{parts[1]}",
                "PUT",
                {"is_enabled": bool(int(parts[2]))},
            )
        elif name == "settings":
            await self.send(
                token,
                chat,
                "⚙️ Настройки общие с приложением.\nВ Mini App доступны все разделы и плагины, изменения применяются автоматически.\nДля Mini App укажите HTTPS-адрес панели в приложении. Без адреса используйте кнопки бота.",
                [
                    [
                        button(
                            "Уведомления: " + ("вкл" if settings.get("enabled") else "выкл"),
                            "notifications",
                        )
                    ],
                    [button("Меню", "menu")],
                ],
            )
        elif name == "notifications":
            async with lock:
                current, _ = await configuration()
                current["enabled"] = not current.get("enabled")
                await save_setting("telegram", current)
            await self.send(
                token, chat, "✓ Уведомления " + ("включены" if current["enabled"] else "выключены")
            )
        elif name == "logs":
            async with Session() as db:
                rows = list(
                    (await db.scalars(select(Activity).order_by(Activity.id.desc()).limit(8))).all()
                )
            await self.send(
                token,
                chat,
                "📋 Последние события\n" + "\n".join(str(row.text)[:350] for row in rows),
                [[button("Обновить", "logs"), button("Меню", "menu")]],
            )
        else:
            await self.menu(token, chat, settings)


control = TelegramControl()
