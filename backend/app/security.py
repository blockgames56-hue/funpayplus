from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from .config import config
from .crypto import Vault

vault = Vault(config.secret_key)


def session_cookie() -> str:
    expiry = str(int(time.time()) + 86400)
    signature = hmac.new(config.api_token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def allowed_origins() -> list[str]:
    """Origin'ы, которым разрешено обращаться к API.

    Origin'ы десктоп-приложения (Tauri) добавляются всегда: их невозможно
    подделать из обычного браузера, а переопределение ALLOWED_ORIGINS на
    сервере не должно отключать подключение десктоп-клиента к серверу.
    """
    origins = [item.strip() for item in config.allowed_origins.split(",") if item.strip()]
    origins += ["tauri://localhost", "http://tauri.localhost"]
    if config.public_origin:
        origins.append(config.public_origin)
    return list(dict.fromkeys(origins))


def authorized(token: str | None, cookie: str | None) -> bool:
    if token and token.isascii() and secrets.compare_digest(token, config.api_token):
        return True
    for session in (token, cookie):
        if not session or not session.isascii() or "." not in session:
            continue
        expiry, signature = session.split(".", 1)
        expected = hmac.new(config.api_token.encode(), expiry.encode(), hashlib.sha256).hexdigest()
        if (
            expiry.isascii()
            and expiry.isdigit()
            and len(expiry) <= 12
            and int(expiry) > time.time()
            and secrets.compare_digest(signature, expected)
        ):
            return True
    return False


async def require_auth(request: Request) -> None:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    from .telegram_control import authorized_session

    if not authorized(token, request.cookies.get("pulse_session")) and not await authorized_session(token):
        raise HTTPException(401, "Требуется вход")
    require_origin(request)


def require_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = allowed_origins()
    if origin and origin not in allowed and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Источник запроса запрещён")
