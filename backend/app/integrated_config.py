"""Configuration and shared resource lifecycle for imported plugin adapters."""

import asyncio
import copy
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .db import Session
from .integrated_schemas import CONFIG_MODELS
from .models import Account, PluginBinding, RentalAccount, RentalAccountBinding
from .security import vault
from .services import get_setting, save_setting

SECRET_FIELDS = {
    "photo_ai_api_key",
    "photo_vision_api_key",
    "api_key",
    "fragment_cookie",
    "fragment_hash",
    "ton_api_key",
    "mnemonic",
    "phpsessid",
}
config_lock = asyncio.Lock()


async def stored_config(kind):
    if kind not in CONFIG_MODELS:
        raise ValueError("Неизвестный плагин")
    return {
        **CONFIG_MODELS[kind]().model_dump(mode="json"),
        **(await get_setting(f"integrated:{kind}", {})),
    }


async def plain_config(kind):
    data = await stored_config(kind)
    for key in SECRET_FIELDS:
        if data.get(key):
            data[key] = vault.decrypt(data[key])
    for provider in data.get("providers", []):
        if provider.get("api_key"):
            provider["api_key"] = vault.decrypt(provider["api_key"])
    return data


async def public_config(kind):
    data = await stored_config(kind)
    for key in SECRET_FIELDS & data.keys():
        data[f"has_{key}"] = bool(data[key])
        data[key] = ""
    for provider in data.get("providers", []):
        provider["has_api_key"] = bool(provider.get("api_key"))
        provider["api_key"] = ""
    return data


def credentials_ready(kind, data):
    if kind == "auto-smm":
        credentials = bool(data.get("providers")) and all(
            row.get("api_key") for row in data["providers"]
        )
    elif kind == "steam-points":
        credentials = bool(data.get("api_key"))
    elif kind == "auto-stars":
        credentials = all(
            data.get(key)
            for key in (
                "fragment_cookie",
                "fragment_hash",
                "ton_api_key",
                "mnemonic",
                "wallet_address",
            )
        )
    elif kind == "support-tickets":
        return bool(data.get("account_id"))
    else:
        credentials = True
    return credentials


async def configured(kind, data=None, *, db=None):
    data = data if data is not None else await stored_config(kind)
    if kind == "support-tickets":
        return credentials_ready(kind, data)
    if not credentials_ready(kind, data):
        return False

    async def has_binding(session):
        binding = await session.scalar(
            select(PluginBinding.id)
            .where(PluginBinding.kind == kind, PluginBinding.is_active.is_(True))
            .limit(1)
        )
        if kind == "steam-rent":
            if not binding:
                return False
            linked = await session.scalar(
                select(RentalAccount.id).where(
                    (RentalAccount.binding_id == binding)
                    | (
                        RentalAccount.id.in_(
                            select(RentalAccountBinding.account_id).where(
                                RentalAccountBinding.binding_id == binding
                            )
                        )
                    )
                )
            )
            return bool(linked)
        return bool(binding)

    if db is not None:
        return await has_binding(db)
    async with Session() as session:
        return await has_binding(session)


async def save_config(kind, raw):
    model = CONFIG_MODELS[kind].model_validate(raw)
    data = model.model_dump(mode="json")
    async with config_lock:
        old = await stored_config(kind)
        data["enabled"] = bool(old.get("enabled"))
        async with Session() as db:
            if (
                kind == "support-tickets"
                and data.get("account_id")
                and not await db.get(Account, data["account_id"])
            ):
                raise ValueError("Выбранный аккаунт FunPay не найден")
            if kind == "auto-smm":
                provider_ids = {row["id"] for row in data["providers"]}
                referenced = (
                    await db.scalars(
                        select(PluginBinding.provider_id).where(PluginBinding.kind == kind)
                    )
                ).all()
                if any(ident not in provider_ids for ident in referenced):
                    raise ValueError("Сначала измените привязки лотов удаляемого поставщика")
        for key in SECRET_FIELDS & data.keys():
            data[key] = vault.encrypt(data[key]) if data[key] else old.get(key, "")
        old_providers = {row["id"]: row for row in old.get("providers", [])}
        for row in data.get("providers", []):
            row["api_key"] = (
                vault.encrypt(row["api_key"])
                if row["api_key"]
                else old_providers.get(row["id"], {}).get("api_key", "")
            )
            if not row["api_key"]:
                raise ValueError(f"Укажите API ключ поставщика «{row['name']}»")
        if data.get("enabled") and not await configured(kind, data):
            raise ValueError("Заполните настройки и привяжите лоты перед включением")
        await save_setting(f"integrated:{kind}", data)
    return await public_config(kind)


class ProviderPool:
    """One connection pool and bounded network concurrency for all adapters."""

    def __init__(self):
        self.client = None
        self.semaphore = asyncio.Semaphore(5)
        self.allowed_hosts = {}

    async def validate_host(self, url):
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Разрешён только публичный HTTPS API")
        now = asyncio.get_running_loop().time()
        if self.allowed_hosts.get(host, 0) > now:
            return
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise ValueError("Локальные и служебные адреса API запрещены")
        self.allowed_hosts[host] = now + 60

    async def request(self, method, url, **kwargs):
        await self.validate_host(url)
        async with self.semaphore:
            if self.client is None or self.client.is_closed:
                self.client = httpx.AsyncClient(
                    timeout=httpx.Timeout(20, connect=10),
                    follow_redirects=False,
                    trust_env=False,
                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                )
            return await self.client.request(method, url, **kwargs)

    async def close(self):
        if self.client:
            await self.client.aclose()
            self.client = None


pool = ProviderPool()


def packed(data):
    import json

    return vault.encrypt(json.dumps(data, ensure_ascii=False))


def unpacked(value):
    import json

    return json.loads(vault.decrypt(value)) if value else {}


def public_snapshot(data):
    result = copy.deepcopy(data)
    for key in SECRET_FIELDS:
        result.pop(key, None)
    return result
