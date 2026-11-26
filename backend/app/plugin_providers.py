"""Typed provider adapters. Purchases are never retried on an ambiguous result."""

import asyncio
import base64
import json
import re
import time
from decimal import Decimal
from urllib.parse import urlparse

from .integrated_config import pool


class ProviderError(ValueError):
    pass


def result_json(response):
    if response.status_code == 429:
        raise ProviderError("Лимит API поставщика; повторная покупка не отправлена")
    if not response.is_success:
        raise ProviderError(f"API поставщика вернул HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise ProviderError("API поставщика вернул неверный JSON") from None
    if not isinstance(data, dict):
        raise ProviderError("Неожиданный ответ API поставщика")
    if data.get("error"):
        raise ProviderError("Поставщик отклонил запрос; проверьте ключ, баланс и параметры услуги")
    return data


def parse_target(kind, text, config):
    if kind == "auto-stars":
        match = re.fullmatch(r"(?:https://t\.me/|@)?([A-Za-z][A-Za-z0-9_]{3,31})/?", text.strip())
        return match[1] if match else None
    match = re.search(r"https?://[^\s<>]+", text)
    if not match:
        return None
    url = urlparse(match[0].rstrip(".,)!"))
    try:
        port = url.port
    except ValueError:
        return None
    if url.username or url.password or port not in (None, 443) or url.scheme != "https":
        return None
    host = (url.hostname or "").lower()
    if kind == "steam-points":
        if host != "steamcommunity.com" or not re.fullmatch(
            r"/(?:id/[A-Za-z0-9_-]{2,64}|profiles/7656119\d{10})/?", url.path
        ):
            return None
        return f"https://steamcommunity.com{url.path.rstrip('/')}"
    domains = config.get("allowed_domains", [])
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        return None
    return url.geturl()


async def smm_request(provider, action, **fields):
    response = await pool.request(
        "POST", provider["url"], data={"key": provider["api_key"], "action": action, **fields}
    )
    return result_json(response)


async def smm_services(provider):
    response = await pool.request(
        "POST", provider["url"], data={"key": provider["api_key"], "action": "services"}
    )
    if not response.is_success:
        raise ProviderError("Не удалось получить список услуг SMM")
    data = response.json()
    if not isinstance(data, list):
        raise ProviderError("SMM API не вернул список услуг")
    return [
        {
            key: row.get(key)
            for key in ("service", "name", "category", "rate", "min", "max", "refill")
        }
        for row in data[:10000]
        if isinstance(row, dict)
    ]


async def points_request(action, config, **fields):
    response = await pool.request(
        "POST",
        f"https://api.buysteampoints.com/api/{action}",
        json={"api_key": config["api_key"], **fields},
    )
    data = result_json(response)
    if data.get("success") is not True:
        raise ProviderError("Сервис Steam Points не подтвердил операцию")
    return data


async def fragment_request(config, method, **fields):
    response = await pool.request(
        "POST",
        "https://fragment.com/api",
        params={"hash": config["fragment_hash"]},
        headers={
            "Cookie": config["fragment_cookie"],
            "Origin": "https://fragment.com",
            "Referer": "https://fragment.com/stars/buy",
            "X-Requested-With": "XMLHttpRequest",
        },
        data={"method": method, **fields},
    )
    return result_json(response)


async def prepare_stars(config, username, quantity):
    from ton_core import Address, Cell, NetworkGlobalID
    from tonutils.clients import TonapiClient
    from tonutils.contracts import WalletV4R2
    from tonutils.contracts.wallet.messages import TONTransferBuilder

    if not config["min_stars"] <= quantity <= config["max_stars"]:
        raise ProviderError("Количество Stars вне установленных лимитов")
    client = TonapiClient(
        network=NetworkGlobalID.MAINNET, api_key=config["ton_api_key"], timeout=15
    )
    try:
        wallet, _, _, _ = await asyncio.to_thread(
            WalletV4R2.from_mnemonic, client, config["mnemonic"]
        )
        if wallet.address != Address(config["wallet_address"]):
            raise ProviderError("Seed-фраза не соответствует указанному адресу кошелька")
        found = await fragment_request(
            config, "searchStarsRecipient", query=username, quantity=quantity
        )
        recipient = found.get("found", {}).get("recipient")
        if not recipient:
            raise ProviderError("Fragment не нашёл получателя")
        init = await fragment_request(
            config, "initBuyStarsRequest", recipient=recipient, quantity=quantity
        )
        if not init.get("req_id"):
            raise ProviderError("Fragment не вернул ID запроса покупки")
        account = {
            "address": wallet.address.to_str(is_user_friendly=False),
            "chain": "-239",
            "walletStateInit": base64.b64encode(wallet.state_init.serialize().to_boc()).decode(),
        }
        link = await fragment_request(
            config,
            "getBuyStarsLink",
            account=json.dumps(account),
            device=json.dumps(
                {
                    "platform": "windows",
                    "appName": "FunPay Plus",
                    "appVersion": "0.1.0",
                    "maxProtocolVersion": 2,
                    "features": ["SendTransaction"],
                }
            ),
            transaction="1",
            id=init["req_id"],
            show_sender="1" if config.get("show_sender") else "0",
        )
        transaction = link.get("transaction", {})
        messages = transaction.get("messages", [])
        if len(messages) != 1:
            raise ProviderError("Fragment вернул неоднозначную транзакцию")
        message = messages[0]
        amount = int(message.get("amount", "0"))
        if amount <= 0 or Decimal(amount) / Decimal(10**9) > Decimal(
            str(config["max_ton_per_order"])
        ):
            raise ProviderError("Стоимость Stars превышает лимит TON на заказ")
        if not message.get("address") or not message.get("payload") or message.get("stateInit"):
            raise ProviderError("Fragment не вернул однозначные реквизиты платежа")
        if int(transaction.get("validUntil", int(time.time()) + 60)) <= time.time():
            raise ProviderError("Срок действия транзакции Fragment истёк")
        body = Cell.from_boc(base64.b64decode(message["payload"]))
        if len(body) != 1:
            raise ProviderError("Неверный BOC платежа Fragment")
        async with asyncio.timeout(30):
            await client.connect()
            await wallet.refresh()
            if wallet.balance < amount + 50_000_000:
                raise ProviderError("Недостаточно TON с учётом комиссии сети")
            signed = await wallet.build_external_message(
                [TONTransferBuilder(destination=message["address"], amount=amount, body=body[0])]
            )
        return {
            "boc": signed.as_hex,
            "hash": signed.normalized_hash,
            "fragment_request_id": init["req_id"],
            "destination": message["address"],
            "amount_nano": amount,
            "expires_at": int(transaction.get("validUntil", time.time() + 60)),
        }
    finally:
        await client.close()


async def broadcast_stars(config, signed):
    response = await pool.request(
        "POST",
        "https://tonapi.io/v2/blockchain/message",
        headers={"Authorization": f"Bearer {config['ton_api_key']}"},
        json={"boc": signed["boc"]},
    )
    if not response.is_success:
        raise ProviderError("TON API не подтвердил отправку: проверьте сохранённый hash")


def trace_complete(trace):
    if trace.get("emulated"):
        return False
    tx = trace.get("transaction", {})
    if tx.get("success") is not True:
        return False
    internal = [row for row in tx.get("out_msgs", []) if row.get("msg_type") == "int_msg"]
    children = trace.get("children", [])
    if len(children) < len(internal):
        return False
    return all(trace_complete(child) for child in children)


async def stars_status(config, tx_hash):
    if not re.fullmatch(r"[a-fA-F0-9]{64}", tx_hash):
        raise ProviderError("Неверный hash TON")
    response = await pool.request(
        "GET",
        f"https://tonapi.io/v2/traces/{tx_hash}",
        headers={"Authorization": f"Bearer {config['ton_api_key']}"},
    )
    if response.status_code == 404:
        return False
    data = result_json(response)
    return trace_complete(data)
