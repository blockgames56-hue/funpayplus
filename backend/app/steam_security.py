"""Steam Guard and verified password rotation for accounts owned by the seller."""

import asyncio
import base64
import hashlib
import hmac
import secrets
import string
import struct
import time


def guard_code(shared_secret: str, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        base64.b64decode(shared_secret, validate=True),
        struct.pack(">Q", timestamp // 30),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    result = ""
    alphabet = "23456789BCDFGHJKMNPQRTVWXY"
    for _ in range(5):
        result += alphabet[value % len(alphabet)]
        value //= len(alphabet)
    return result


def new_password():
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))


LOGIN_HINTS = {
    5: "неверный логин или пароль",
    15: "доступ запрещён: аккаунт ограничен",
    17: "аккаунт заблокирован",
    18: "аккаунт с таким логином не найден",
    43: "аккаунт отключён",
    51: "аккаунт приостановлен",
    61: "пароль недопустим",
    63: "вход отклонён: Steam прислал письмо, подтвердите вход по ссылке",
    65: "неверный код подтверждения с почты",
    66: "вход отклонён: письмо от Steam не доставлено или истекло",
    71: "код подтверждения с почты истёк",
    72: "вход с этого IP запрещён настройками аккаунта",
    74: "вход отклонён: требуется подтверждение по email",
    84: "слишком много попыток: повторите примерно через час",
    85: "Steam Guard требует код из мобильного приложения (maFile не подошёл)",
    87: "слишком много попыток входа: подождите несколько минут",
    88: "код Steam Guard неверный или устарел",
    93: "время не синхронизировано с сервером Steam",
    101: "Steam требует капчу: попробуйте позже",
    114: "аккаунт удалён",
}


def describe_login_error(
    error: BaseException, credentials: dict | None = None
) -> str:
    """Понятная причина неудачного входа в Steam без раскрытия секретов."""
    code = getattr(error, "error_code", None)
    if code is not None and code in LOGIN_HINTS:
        return LOGIN_HINTS[code]
    text = " ".join(str(error).split())[:220] if error else ""
    if credentials:
        for key in ("login", "password", "shared_secret", "identity_secret", "device_id"):
            secret = str(credentials.get(key) or "")
            if secret and len(secret) >= 4:
                text = text.replace(secret, "***").replace(secret.casefold(), "***")
    return text or "неизвестная ошибка"


class NotFoundMobileConfirmationError(Exception):
    pass


class MobileConfirmations:
    def __init__(self, steam):
        self.steam = steam

    async def mobile_confirm_by_creator_id(self, creator_id):
        timestamp = await self.steam.get_server_time()
        params = {
            "p": self.steam.device_id,
            "a": self.steam.steamid,
            "k": self.steam.get_confirmation_hash(timestamp, "conf"),
            "t": timestamp,
            "m": "android",
            "tag": "conf",
        }
        result = await self.steam.json_request(
            "https://steamcommunity.com/mobileconf/getlist", params=params
        )
        if not result.get("success"):
            raise ValueError("Steam не вернул подтверждения")
        match = next(
            (
                row
                for row in result.get("conf", [])
                if str(row.get("creator_id")) == str(creator_id)
            ),
            None,
        )
        if match is None:
            raise NotFoundMobileConfirmationError("Нет подтверждения именно для этой смены пароля")
        params.update(
            {
                "op": "allow",
                "cid": match["id"],
                "ck": match["nonce"],
                "k": self.steam.get_confirmation_hash(timestamp, "allow"),
                "tag": "allow",
            }
        )
        result = await self.steam.json_request(
            "https://steamcommunity.com/mobileconf/ajaxop", params=params
        )
        return result.get("success") is True


async def rotate_password(credentials: dict, password: str):
    from .vendor.steampassword.chpassword import SteamPasswordChange
    from .vendor.steampassword.steam import CustomSteam

    steam = CustomSteam(
        login=credentials["login"],
        password=credentials["password"],
        shared_secret=credentials["shared_secret"],
        identity_secret=credentials["identity_secret"],
        device_id=credentials["device_id"],
        steamid=int(credentials["steamid"]),
    )
    verification = None
    try:
        async with asyncio.timeout(180):
            await SteamPasswordChange(steam).change(password)
            verification = CustomSteam(
                login=credentials["login"],
                password=password,
                shared_secret=credentials["shared_secret"],
                identity_secret=credentials["identity_secret"],
                device_id=credentials["device_id"],
                steamid=int(credentials["steamid"]),
            )
            await verification.login_to_steam()
            if not await verification.is_authorized():
                raise ValueError("Steam не подтвердил вход с новым паролем")
    finally:
        for instance in (steam, verification):
            strategy = instance._requests if instance else None
            if strategy and strategy._session:
                await strategy._session.close()


async def verify_login(credentials):
    from .vendor.steampassword.steam import CustomSteam

    steam = CustomSteam(
        login=credentials["login"],
        password=credentials["password"],
        shared_secret=credentials["shared_secret"],
        identity_secret=credentials["identity_secret"],
        device_id=credentials["device_id"],
        steamid=int(credentials["steamid"]),
    )
    try:
        async with asyncio.timeout(60):
            await steam.login_to_steam()
            if not await steam.is_authorized():
                raise ValueError("Steam не подтвердил сессию")
    finally:
        if steam._requests._session:
            await steam._requests._session.close()
