from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from time import perf_counter
from urllib.parse import quote, urlparse

import httpx
import regex as re
from sqlalchemy import select, update

from .avatar_urls import normalize_avatar_url
from .blacklist import is_blocked
from .chat_dates import UNKNOWN_DATE
from .config import config
from .db import Session
from .events import bus
from .funpay import FunPayError, parse_messages, parse_runner_users
from .funpay_gateway import FunPayGateway as FunPayClient
from .models import (
    Account,
    AutoDeliveryRule,
    Chat,
    ChatMessage,
    DeliveryReservation,
    IncomingEvent,
    Lot,
    MessageSend,
    Order,
    Proxy,
    Settings,
    utcnow,
)
from .security import vault


async def get_setting(key: str, default=None):
    async with Session() as db:
        record = await db.get(Settings, key)
        return json.loads(record.value) if record else default


async def get_settings(defaults: dict):
    """Read a settings snapshot in one query instead of opening a session per key."""
    async with Session() as db:
        rows = (await db.scalars(select(Settings).where(Settings.key.in_(defaults)))).all()
    return {**defaults, **{row.key: json.loads(row.value) for row in rows}}


async def save_setting(key: str, value):
    async with Session() as db:
        record = await db.get(Settings, key)
        if record:
            record.value = json.dumps(value, ensure_ascii=False)
        else:
            db.add(Settings(key=key, value=json.dumps(value, ensure_ascii=False)))
        await db.commit()


def proxy_url(proxy: Proxy) -> str:
    auth = ""
    if proxy.username:
        password = vault.decrypt(proxy.password) if proxy.password else ""
        auth = f"{quote(proxy.username, safe='')}:{quote(password, safe='')}@"
    host = f"[{proxy.host}]" if ":" in proxy.host else proxy.host
    return f"{proxy.protocol}://{auth}{host}:{proxy.port}"


def parse_proxy(line: str) -> dict:
    # Accepted: scheme://user:pass@host:port, host:port, host:port:user:pass.
    from urllib.parse import unquote

    if "://" not in line:
        pieces = line.split(":")
        if len(pieces) == 4:
            line = f"http://{quote(pieces[2], safe='')}:{quote(pieces[3], safe='')}@{pieces[0]}:{pieces[1]}"
        else:
            line = "http://" + line
    parsed = urlparse(line)
    if (
        parsed.scheme not in {"http", "https", "socks5"}
        or not parsed.hostname
        or not parsed.port
        or not 1 <= parsed.port <= 65535
    ):
        raise ValueError("Неверный адрес прокси")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Адрес прокси не должен содержать путь")
    return {
        "protocol": parsed.scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "username": unquote(parsed.username) if parsed.username else None,
        "password": vault.encrypt(unquote(parsed.password)) if parsed.password else None,
    }


class AccountService:
    def __init__(self):
        self.clients: dict[int, FunPayClient] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.ingest_locks: dict[int, asyncio.Lock] = {}
        self.avatar_attempted: dict[tuple[int, int], datetime] = {}
        self.avatar_locks: dict[int, asyncio.Lock] = {}
        self.avatar_contacts_at: dict[int, datetime] = {}
        self.avatar_missing_until: dict[tuple[str, int], datetime] = {}
        self.send_locks: dict[int, asyncio.Lock] = {}

    async def client(self, account_id: int) -> FunPayClient:
        lock = self.locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            if account_id in self.clients:
                return self.clients[account_id]
            async with Session() as db:
                account = await db.get(Account, account_id)
                if not account:
                    raise FunPayError("Аккаунт не найден")
                proxy = await db.get(Proxy, account.proxy_id) if account.proxy_id else None
                client = FunPayClient(
                    vault.decrypt(account.golden_key),
                    account.user_agent,
                    proxy_url(proxy) if proxy else None,
                    config.funpay_transport,
                )
            try:
                await client.sync_profile()
            except Exception:
                await client.close()
                raise
            self.clients[account_id] = client
            return client

    async def discard(self, account_id: int):
        client = self.clients.pop(account_id, None)
        if client:
            await client.close()

    async def close(self):
        results = await asyncio.gather(*(self.discard(ident) for ident in list(self.clients)), return_exceptions=True)
        if any(isinstance(result, Exception) for result in results):
            raise FunPayError("Не удалось закрыть все соединения FunPay")

    async def sync(self, account_id: int):
        client = await self.client(account_id)
        profile = await client.sync_profile()
        async with Session() as db:
            account = await db.get(Account, account_id)
            if not account:
                raise FunPayError("Аккаунт удалён")
            for name, value in profile.items():
                if name != "avatar_url" or value:
                    setattr(account, name, value)
            account.title = profile["username"]
            account.status = "online"
            account.last_error = None
            account.last_synced_at = utcnow()
            await db.commit()
        await bus.log("accounts", "Профиль и баланс обновлены", account_id)

    async def sync_chats(self, account_id: int):
        client = await self.client(account_id)
        rows = await client.get_chats()
        async with Session() as db:
            cached = {
                chat.funpay_chat_id: (chat.interlocutor_avatar, chat.interlocutor_id)
                for chat in (
                    await db.scalars(select(Chat).where(Chat.account_id == account_id))
                ).all()
            }
        for row in rows:
            avatar, user_id = cached.get(row["funpay_chat_id"], (None, 0))
            row["interlocutor_avatar"] = normalize_avatar_url(
                row["interlocutor_avatar"]
            ) or normalize_avatar_url(avatar)
            row["interlocutor_id"] = row["interlocutor_id"] or user_id
        # Contact list markup does not always contain the profile photo.
        # Failed profile lookups are retried at most once an hour.
        missing = [
            row
            for row in rows
            if row["interlocutor_id"]
            and not row["interlocutor_avatar"]
            and (
                utcnow()
                - self.avatar_attempted.get(
                    (account_id, row["interlocutor_id"]), utcnow() - timedelta(hours=2)
                )
            ).total_seconds()
            >= 3600
        ]
        semaphore = asyncio.Semaphore(5)

        async def enrich(row):
            async with semaphore:
                self.avatar_attempted[(account_id, row["interlocutor_id"])] = utcnow()
                try:
                    row["interlocutor_avatar"] = await client.get_user_avatar(
                        row["interlocutor_id"]
                    )
                except FunPayError:
                    pass

        await asyncio.gather(*(enrich(row) for row in missing[:10]))
        async with Session() as db:
            for data in rows:
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == account_id,
                        Chat.funpay_chat_id == data["funpay_chat_id"],
                    )
                )
                if chat:
                    for key, value in data.items():
                        if key not in {"interlocutor_avatar", "interlocutor_id"} or value:
                            setattr(chat, key, value)
                else:
                    db.add(Chat(account_id=account_id, last_message_at=UNKNOWN_DATE, **data))
            await db.commit()
        await bus.publish("chats", {"account_id": account_id})
        return len(rows)

    async def ensure_avatar(self, kind: str, ident: int, *, force=False):
        model = Account if kind == "account" else Chat
        field = "avatar_url" if kind == "account" else "interlocutor_avatar"
        async with Session() as db:
            row = await db.get(model, ident)
            if not row:
                return None
            account_id = row.id if kind == "account" else row.account_id
            current = normalize_avatar_url(getattr(row, field))
            owner = row if kind == "account" else await db.get(Account, account_id)
            if not owner or not owner.is_active:
                return current
        if current and not force:
            return current
        key = (kind, ident)
        if not force and self.avatar_missing_until.get(key, utcnow()) > utcnow():
            return None
        async with self.avatar_locks.setdefault(account_id, asyncio.Lock()):
            # Other components may have resolved the same absent photo while we waited.
            if self.avatar_missing_until.get(key, utcnow()) > utcnow():
                return None
            async with Session() as db:
                row = await db.get(model, ident)
                if not row:
                    return None
                current = normalize_avatar_url(getattr(row, field))
                if current and not force:
                    return current
                owner = row if kind == "account" else await db.get(Account, account_id)
                if not owner or not owner.is_active:
                    return current
                user_id = row.funpay_id if kind == "account" else row.interlocutor_id
            try:
                client = await self.client(account_id)
                if kind == "chat":
                    if (
                        utcnow()
                        - self.avatar_contacts_at.get(account_id, utcnow() - timedelta(minutes=5))
                    ).total_seconds() >= 60:
                        await self.sync_chats(account_id)
                        self.avatar_contacts_at[account_id] = utcnow()
                    async with Session() as db:
                        row = await db.get(Chat, ident)
                        if not row:
                            return None
                        current = normalize_avatar_url(row.interlocutor_avatar)
                        user_id = row.interlocutor_id
                    if current:
                        return current
                url = (
                    await client.get_user_avatar(user_id or client.user_id)
                    if kind == "account" or user_id
                    else None
                )
            except FunPayError:
                self.avatar_missing_until[key] = utcnow() + timedelta(seconds=30)
                return None
            url = normalize_avatar_url(url)
            async with Session() as db:
                row = await db.get(model, ident)
                if row:
                    setattr(row, field, url)
                    await db.commit()
            if not url:
                self.avatar_missing_until[key] = utcnow() + timedelta(minutes=5)
            else:
                self.avatar_missing_until.pop(key, None)
            await bus.publish(
                "accounts" if kind == "account" else "chats", {"account_id": account_id}
            )
            return url

    async def sync_runner_avatars(self, account_id: int, result: dict):
        avatars = parse_runner_users(result)
        if not avatars:
            return
        changed = False
        async with Session() as db:
            chats = (
                await db.scalars(
                    select(Chat).where(
                        Chat.account_id == account_id, Chat.interlocutor_id.in_(avatars)
                    )
                )
            ).all()
            for chat in chats:
                url = avatars[chat.interlocutor_id]
                if chat.interlocutor_avatar != url:
                    chat.interlocutor_avatar = url
                    changed = True
            if changed:
                await db.commit()
        if changed:
            await bus.publish("chats", {"account_id": account_id})

    async def refresh_chat(self, chat_id: int):
        async with Session() as db:
            chat = await db.get(Chat, chat_id)
            if not chat:
                raise FunPayError("Диалог не найден")
            account_id, node = chat.account_id, chat.funpay_chat_id
        client = await self.client(account_id)
        objects = [
            {
                "type": "chat_node",
                "id": node,
                "tag": "00000000",
                "data": {"node": node, "last_message": -1, "content": ""},
            }
        ]
        result = await client.runner(objects)
        await self.ingest(account_id, result.get("objects", []), automate=False, mark_unread=False)
        async with Session() as db:
            refreshed = await db.get(Chat, chat_id)
            user_id = refreshed.interlocutor_id if refreshed else 0
            cached_avatar = refreshed.interlocutor_avatar if refreshed else None
        if user_id and not cached_avatar:
            try:
                avatar = await client.get_user_avatar(user_id)
            except FunPayError:
                avatar = None
            if avatar:
                async with Session() as db:
                    chat = await db.get(Chat, chat_id)
                    if chat:
                        chat.interlocutor_avatar = avatar
                        await db.commit()
        await bus.publish("chats", {"account_id": account_id})

    async def refresh_contacts(self, account_id: int) -> int:
        count = await self.sync_chats(account_id)
        client = await self.client(account_id)
        if getattr(client, "native_runner", False) is True:
            # Runner establishes its own baseline. Fetch full history only when
            # the operator opens a chat, not for every contact at startup.
            return count
        async with Session() as db:
            unknown = (
                await db.scalars(
                    select(Chat)
                    .where(Chat.account_id == account_id)
                    .order_by(Chat.last_message_at.desc())
                )
            ).all()
            nodes = [chat.funpay_chat_id for chat in unknown]
        client = await self.client(account_id)
        for offset in range(0, len(nodes), 10):
            objects = [
                {
                    "type": "chat_node",
                    "id": node,
                    "tag": "00000000",
                    "data": {"node": node, "last_message": -1, "content": ""},
                }
                for node in nodes[offset : offset + 10]
            ]
            result = await client.runner(objects)
            await self.ingest(
                account_id, result.get("objects", []), automate=False, mark_unread=False
            )
        if nodes:
            await self.sync_chats(account_id)
        return count

    async def sync_lots(self, account_id: int):
        rows = await (await self.client(account_id)).get_lots()
        policy = await get_setting(f"account-automation:{account_id}", {})
        async with Session() as db:
            for data in rows:
                lot = await db.scalar(
                    select(Lot).where(
                        Lot.account_id == account_id,
                        Lot.funpay_lot_id == data["funpay_lot_id"],
                    )
                )
                if lot:
                    for key, value in data.items():
                        setattr(lot, key, value)
                else:
                    db.add(
                        Lot(
                            account_id=account_id, auto_raise=bool(policy.get("auto_raise")), **data
                        )
                    )
            await db.commit()
        await bus.log("lots", f"Синхронизировано предложений: {len(rows)}", account_id)
        return len(rows)

    async def ingest(
        self,
        account_id: int,
        objects: list[dict],
        *,
        automate: bool = False,
        mark_unread: bool = True,
        confirmed_new: bool = False,
    ):
        client = await self.client(account_id)
        incoming = []
        touched = set()
        async with self.ingest_locks.setdefault(account_id, asyncio.Lock()), Session() as db:
            for obj in objects:
                if obj.get("type") != "chat_node" or not isinstance(obj.get("data"), dict):
                    continue
                data = obj["data"]
                normalized = data.get("normalized_messages")
                confirmed_ids = {int(value) for value in data.get("confirmed_new_ids", [])
                                 if str(value).isdigit()}
                is_initial = data.get("initial") is True
                reconcile = data.get("reconcile") is True
                for parsed in (normalized if normalized is not None else parse_messages(data, client.user_id)):
                    parsed = dict(parsed)
                    node = parsed.pop("node_id")
                    sender_avatar = parsed.pop("sender_avatar", None)
                    is_arbitration = parsed.pop("is_arbitration", False)
                    chat = await db.scalar(
                        select(Chat).where(
                            Chat.account_id == account_id, Chat.funpay_chat_id == node
                        )
                    )
                    if not chat:
                        chat = Chat(
                            account_id=account_id,
                            funpay_chat_id=node,
                            interlocutor_id=parsed["sender_id"] if not parsed["is_my"] else 0,
                            interlocutor_name=parsed["sender_name"]
                            if not parsed["is_my"]
                            else node,
                            last_message_at=UNKNOWN_DATE,
                        )
                        db.add(chat)
                        await db.flush()
                    touched.add(chat.id)
                    if (
                        not parsed["is_my"]
                        and not is_arbitration
                        and parsed["sender_id"]
                        and not chat.interlocutor_id
                        and parsed["sender_name"].casefold() == chat.interlocutor_name.casefold()
                    ):
                        # Old messages also carry identity metadata. Repair it
                        # before duplicate suppression, without replaying events.
                        chat.interlocutor_id = parsed["sender_id"]
                    if (
                        sender_avatar
                        and not parsed["is_my"]
                        and not is_arbitration
                        and parsed["sender_id"] == chat.interlocutor_id
                    ):
                        chat.interlocutor_avatar = sender_avatar
                    message_cursor = chat.last_message_cursor
                    baseline_ready = chat.runner_baselined
                    message_id = int(parsed["funpay_message_id"])
                    runner_confirmed = message_id in confirmed_ids
                    reconciled_new = (
                        reconcile and not is_initial and baseline_ready
                        and message_cursor is not None and message_id > message_cursor
                    )
                    should_automate = automate and not is_initial and (
                        runner_confirmed or reconciled_new
                    )
                    exists = await db.scalar(
                        select(ChatMessage).where(
                            ChatMessage.chat_id == chat.id,
                            ChatMessage.funpay_message_id == parsed["funpay_message_id"],
                        )
                    )
                    if exists:
                        if parsed["created_at"] != UNKNOWN_DATE:
                            exists.created_at = parsed["created_at"]
                        if should_automate and not parsed["is_my"]:
                            journal = await db.scalar(select(IncomingEvent).where(
                                IncomingEvent.message_id == exists.id,
                                IncomingEvent.state == "IGNORED",
                                IncomingEvent.reason == "initial_or_history",
                            ))
                            if journal:
                                journal.state, journal.reason = "RECEIVED", None
                                incoming.append((chat.id, {**parsed, "local_id": exists.id,
                                                          "is_arbitration": is_arbitration}))
                        if message_cursor is None or message_id > message_cursor:
                            chat.last_message_cursor = message_id
                        if is_initial or reconcile:
                            chat.runner_baselined = True
                        continue
                    if parsed["created_at"] == UNKNOWN_DATE:
                        previous = await db.scalar(
                            select(ChatMessage)
                            .where(
                                ChatMessage.chat_id == chat.id,
                                ChatMessage.funpay_message_id < parsed["funpay_message_id"],
                            )
                            .order_by(ChatMessage.funpay_message_id.desc())
                            .limit(1)
                        )
                        if previous:
                            parsed["created_at"] = previous.created_at
                    stored = ChatMessage(chat_id=chat.id, **parsed)
                    db.add(stored)
                    await db.flush()
                    from .kosellrent import is_review

                    system_review = not parsed["sender_id"] and should_automate and is_review(parsed["text"])
                    reason = (
                        "own_message" if parsed["is_my"] else
                        "arbitration" if is_arbitration else
                        "missing_identity" if not parsed["sender_id"] and not system_review else
                        None if should_automate else "initial_or_history"
                    )
                    db.add(IncomingEvent(
                        event_key=f"{account_id}:{node}:{stored.funpay_message_id}:NEW_MESSAGE",
                        account_id=account_id, chat_id=chat.id, message_id=stored.id,
                        state="IGNORED" if reason else "RECEIVED", reason=reason,
                    ))
                    if parsed["is_my"] is False:
                        if mark_unread:
                            chat.unread_count = (chat.unread_count or 0) + 1
                        if not chat.interlocutor_id and parsed["sender_id"]:
                            chat.interlocutor_id = parsed["sender_id"]
                            chat.interlocutor_name = parsed["sender_name"]
                        if reason is None:
                            incoming.append(
                                (
                                    chat.id,
                                    {**parsed, "local_id": stored.id, "is_arbitration": is_arbitration},
                                )
                            )
                    if message_cursor is None or message_id > message_cursor:
                        chat.last_message_cursor = message_id
                    if is_initial or reconcile:
                        chat.runner_baselined = True
                if data.get("history_checked") is True:
                    node = str(obj.get("id", ""))
                    checked_chat = await db.scalar(
                        select(Chat).where(
                            Chat.account_id == account_id, Chat.funpay_chat_id == node
                        )
                    )
                    if checked_chat:
                        checked_chat.runner_checked_at = utcnow()
                        checked_chat.runner_baselined = True
                        if checked_chat.last_message_cursor is None:
                            checked_chat.last_message_cursor = 0
            await db.flush()
            # Message IDs express server order. Loading older history must never
            # move a conversation backwards or replace its latest preview.
            for chat in (
                await db.scalars(
                    select(Chat).where(Chat.account_id == account_id, Chat.id.in_(touched))
                )
            ).all():
                latest = await db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.chat_id == chat.id)
                    .order_by(ChatMessage.funpay_message_id.desc(), ChatMessage.id.desc())
                    .limit(1)
                )
                if latest:
                    chat.last_message_text = latest.text or "Изображение"
                    chat.last_message_at = latest.created_at
            await db.commit()
        if objects:
            await bus.publish("chats", {"account_id": account_id})
        if automate:
            from .event_pipeline import dispatch

            if incoming:
                await dispatch()
            for chat_id, message in incoming:
                async with Session() as db:
                    chat = await db.get(Chat, chat_id)
                    blocked = not chat or await is_blocked(
                        db, account_id=account_id, user_id=message["sender_id"], chat_id=chat_id
                    )
                if blocked:
                    continue
                await bus.publish(
                    "chats", {"account_id": account_id, "event": "NEW_MESSAGE", "chat_id": chat_id}
                )
                await telegram.notify(
                    "chats",
                    f"{message['sender_name']}: {message['text']}",
                    chat_id=chat_id,
                )
                # Core replies have already been durably dispatched above. Legacy
                # plugin hooks must not intercept or produce a second answer.

    async def send(self, chat_id: int, text: str, *, source="manual", idempotency_key=None):
        if not text.strip() or len(text) > 20000:
            raise FunPayError("Пустое или слишком длинное сообщение")
        key = idempotency_key or str(uuid.uuid4())
        digest = hashlib.sha256(text.encode()).hexdigest()
        async with self.send_locks.setdefault(chat_id, asyncio.Lock()), Session() as db:
            chat = await db.get(Chat, chat_id)
            if not chat:
                raise FunPayError("Диалог не найден")
            account = await db.get(Account, chat.account_id)
            if not account or not account.is_active:
                raise FunPayError("Аккаунт отключён")
            prior = await db.scalar(select(MessageSend).where(MessageSend.idempotency_key == key))
            if prior:
                if prior.chat_id != chat_id or prior.payload_hash != digest:
                    raise FunPayError("Ключ отправки уже используется для другого сообщения")
                if prior.state != "SENT":
                    raise FunPayError("UNKNOWN: результат предыдущей отправки требует проверки")
                return {"delivery_status": "SENT", "message_id": prior.message_id}
            client = await self.client(chat.account_id)
            attempt = MessageSend(idempotency_key=key, chat_id=chat_id,
                                  source=source, payload_hash=digest, state="UNKNOWN")
            db.add(attempt)
            await db.commit()  # Crash or timeout must never trigger a blind retry.
            result = await client.send_message(chat.funpay_chat_id, text)
            attempt.state = "SENT"
            attempt.message_id = result.get("message_id")
            await db.commit()
            account_id = chat.account_id
        # Only remote-confirmed messages are persisted; no optimistic fake success.
        await self.ingest(account_id, result.get("objects", []))
        await bus.log("chats", "Сообщение отправлено", account_id)
        return {"delivery_status": "SENT", "message_id": attempt.message_id}

    async def raise_group(self, account_id: int, node_id: int, game_id: int, *, dry_run=False):
        client = await self.client(account_id)
        result = (await client.raise_lots(node_id, game_id, dry_run=True) if dry_run
                  else await client.raise_lots(node_id, game_id))
        if dry_run:
            await bus.log("lots", result["message"], account_id)
            return result
        settings = await get_setting("app", {})
        policy = await get_setting(f"account-automation:{account_id}", {})
        next_time = utcnow() + timedelta(
            seconds=result["cooldown"]
            or policy.get("raise_interval", settings.get("auto_raise_interval", 3600))
        )
        async with Session() as db:
            lots = (
                await db.scalars(
                    select(Lot).where(Lot.account_id == account_id, Lot.node_id == node_id)
                )
            ).all()
            for lot in lots:
                lot.next_raise_at = next_time
                if result["raised"]:
                    lot.last_raised_at = utcnow()
            await db.commit()
        await bus.log("lots", result["message"] or "Предложения подняты", account_id)
        await telegram.notify("lots", result["message"] or "Предложения подняты")
        return result

    async def check_proxy(self, proxy_id: int):
        async with Session() as db:
            proxy = await db.get(Proxy, proxy_id)
            if not proxy:
                raise FunPayError("Прокси удалён")
            start = perf_counter()
            try:
                async with httpx.AsyncClient(proxy=proxy_url(proxy), timeout=15) as http:
                    response = await http.get("https://funpay.com/")
                    response.raise_for_status()
                proxy.ping_ms = int((perf_counter() - start) * 1000)
                proxy.status = "active"
            except (httpx.HTTPError, OSError):
                proxy.ping_ms = None
                proxy.status = "dead"
            proxy.last_checked_at = utcnow()
            await db.commit()
        await bus.publish("accounts", {"proxy_id": proxy_id})


accounts = AccountService()


telegram_links_lock = asyncio.Lock()


def telegram_link_scope(token: str, chat_id) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:24] + ":" + str(chat_id)


async def scope_legacy_telegram_links(token: str):
    # Original rows did not distinguish bots. Tag them with the old token before
    # replacement so message #1 in a new bot cannot reply to an old buyer's chat.
    async with telegram_links_lock:
        if await get_setting("telegram_links_scoped", False):
            return
        from .models import TelegramLink

        async with Session() as db:
            await db.execute(update(TelegramLink).where(~TelegramLink.telegram_chat_id.contains(":"))
                .values(telegram_chat_id=telegram_link_scope(token, "") + TelegramLink.telegram_chat_id))
            await db.commit()
        await save_setting("telegram_links_scoped", True)


class TelegramService:
    def __init__(self):
        self.offset = 0
        self.rate_lock = asyncio.Lock()
        self.next_send_at = 0.0

    async def request(self, token: str, method: str, payload: dict):
        try:
            if method == "getUpdates":
                # Long polling has its own connection and must not hold the send lock.
                async with httpx.AsyncClient(timeout=40) as http:
                    response = await http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
                    data = response.json()
                    if not data.get("ok"):
                        raise FunPayError("Telegram: проверьте токен и отсутствие второго запуска бота")
                    return data["result"]
            async with self.rate_lock:
                delay = self.next_send_at - perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
                async with httpx.AsyncClient(timeout=15) as http:
                    for attempt in range(3):
                        response = await http.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
                        self.next_send_at = perf_counter() + (1 if method == "sendMessage" else 0.2)
                        data = response.json()
                        if response.status_code == 429 and attempt < 2:
                            retry = min(60, max(1, int(data.get("parameters", {}).get("retry_after", 5))))
                            await asyncio.sleep(retry)
                            continue
                        if not data.get("ok"):
                            raise FunPayError("Telegram отклонил запрос. Проверьте токен и Chat ID.")
                        return data["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise FunPayError("Telegram недоступен") from None

    async def notify(
        self, event: str, text: str, *, chat_id: int | None = None, force: bool = False
    ):
        settings = await get_setting("telegram", {})
        if not settings.get("token") or not settings.get("chat_id"):
            if force:
                raise FunPayError("Сначала сохраните токен и Chat ID")
            return
        if not force and (not settings.get("enabled") or event not in settings.get("events", [])):
            return
        try:
            message = await self.request(
                vault.decrypt(settings["token"]),
                "sendMessage",
                {"chat_id": settings["chat_id"], "text": text[:4000]},
            )
            if chat_id:
                from .models import TelegramLink

                async with Session() as db:
                    db.add(
                        TelegramLink(
                            telegram_chat_id=telegram_link_scope(vault.decrypt(settings["token"]), settings["chat_id"]),
                            telegram_message_id=message["message_id"],
                            chat_id=chat_id,
                        )
                    )
                    await db.commit()
        except FunPayError:
            if force:
                raise
            await bus.log(
                "notifications",
                "Не удалось отправить уведомление Telegram",
                level="error",
            )

    async def poll_replies(self):
        from .telegram_control import control

        await control.poll_once()


telegram = TelegramService()


def command_reply(commands: list[dict], text: str) -> str:
    """Compatibility conversion for legacy settings; uses the core matcher."""
    from .reply_engine import matches

    for command in commands:
        rule = {"triggers": [command.get("trigger", "")], "match": command.get("match", "exact")}
        if command.get("is_active", True) and matches(rule, text):
            return command.get("reply", "")
    return ""


class AutomationService:
    def __init__(self):
        self.delivery_lock = asyncio.Lock()

    async def recover_deliveries(self):
        async with self.delivery_lock, Session() as db:
            rows = (await db.scalars(select(DeliveryReservation).where(
                DeliveryReservation.state == "reserved"))).all()
            for reservation in rows:
                reservation.state = "uncertain"
                order = await db.get(Order, reservation.order_id)
                if order and order.delivery_state == "sending":
                    order.delivery_state = "uncertain"
            # Includes attempts made before the reservation ledger existed.
            for order in (await db.scalars(select(Order).where(Order.delivery_state == "sending"))).all():
                order.delivery_state = "uncertain"
            await db.commit()

    async def respond(self, chat_id: int, message: dict):
        from .reply_engine import on_message

        await on_message(chat_id, message)

    async def deliver(self, order_id: int):
        # One process, one delivery dispatcher. A durable 'sending' state prevents blind retries.
        async with self.delivery_lock:
            async with Session() as db:
                order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
                if (
                    not order
                    or order.status != "paid"
                    or order.auto_delivered
                    or order.delivery_state != "pending"
                ):
                    return
                if not order.buyer_id:
                    return
                if await db.scalar(select(DeliveryReservation.id).where(
                    DeliveryReservation.order_id == order.id)):
                    return
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == order.account_id,
                        Chat.interlocutor_id == order.buyer_id,
                    )
                )
                if not chat:
                    return
                if await is_blocked(
                    db, account_id=order.account_id, user_id=order.buyer_id, chat_id=chat.id
                ):
                    return
                client = await accounts.client(order.account_id)
                detail = await client.order_details(order.funpay_order_id)
                if (
                    detail.get("status") != "paid"
                    or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
                ):
                    return
                rules = (
                    await db.scalars(
                        select(AutoDeliveryRule)
                        .where(
                            AutoDeliveryRule.account_id == order.account_id,
                            AutoDeliveryRule.is_active.is_(True),
                        )
                        .with_for_update()
                    )
                ).all()
                for rule in rules:
                    matches = (
                        re.search(
                            rule.lot_title_pattern,
                            order.description[:20000],
                            timeout=0.05,
                        )
                        if rule.match_regex
                        else rule.lot_title_pattern == order.description
                    )
                    if not matches:
                        continue
                    item = ""
                    if rule.delivery_type == "pool_keys":
                        from .inventory import ensure_keys_available
                        pool = vault.decrypt(rule.keys_pool).splitlines() if rule.keys_pool else []
                        if not pool:
                            await telegram.notify("stock", f"Пул выдачи пуст: правило #{rule.id}")
                            return
                        try:
                            await ensure_keys_available(db, pool, exclude_rule=rule.id)
                        except FunPayError:
                            await bus.log("orders", "Выдача остановлена: конфликт складских ключей",
                                          order.account_id, "error")
                            return
                        item, *remaining = pool
                        rule.keys_pool = vault.encrypt("\n".join(remaining))
                    text = rule.message_template.format(
                        item=item,
                        order_id=order.funpay_order_id,
                        buyer=order.buyer_name,
                        date=utcnow().date().isoformat(),
                    )
                    order.delivery_text = vault.encrypt(text)
                    order.delivery_state = "sending"
                    reservation = DeliveryReservation(order_id=order.id, rule_id=rule.id,
                                                      item=vault.encrypt(item) if item else None)
                    db.add(reservation)
                    await db.commit()
                    try:
                        await accounts.send(chat.id, text, source="delivery",
                                            idempotency_key=f"delivery:{order.account_id}:{order.funpay_order_id}")
                    except Exception:
                        order.delivery_state = "uncertain"
                        reservation.state = "uncertain"
                        await db.commit()
                        await bus.log(
                            "orders",
                            f"Выдача #{order.funpay_order_id}: результат неизвестен. Проверьте чат перед повтором.",
                            order.account_id,
                            "error",
                        )
                        return
                    order.auto_delivered = True
                    order.delivery_state = "sent"
                    reservation.state = "sent"
                    await db.commit()
                    if rule.delivery_type == "pool_keys" and len(remaining) < 3:
                        await telegram.notify(
                            "stock",
                            f"В правиле #{rule.id} осталось ключей: {len(remaining)}",
                        )
                    await bus.log(
                        "orders",
                        f"Заказ #{order.funpay_order_id} выдан",
                        order.account_id,
                    )
                    return

    async def thank(self, order_id: int, settings: dict):
        async with self.delivery_lock:
            async with Session() as db:
                order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
                if not order or order.status != "closed" or order.thanks_state != "pending":
                    return
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == order.account_id,
                        Chat.interlocutor_id == order.buyer_id,
                    )
                )
                if not chat or await is_blocked(
                    db, account_id=order.account_id, user_id=order.buyer_id, chat_id=chat.id
                ):
                    return
                client = await accounts.client(order.account_id)
                detail = await client.order_details(order.funpay_order_id)
                review = detail.get("review") or {}
                rating = review.get("rating")
                if (
                    detail.get("status") != "closed"
                    or rating not in range(1, 6)
                    or review.get("hidden")
                ):
                    return
                text = settings.get("review_replies", {}).get(str(rating), "")
                if not text and rating == 5:
                    text = settings.get("thanks_text", "")
                if not text.strip():
                    return
                if review.get("reply"):
                    order.thanks_state = "existing_reply"
                    await db.commit()
                    return
                order.thanks_state = "sending"
                await db.commit()
                try:
                    await client.reply_review(order.funpay_order_id, text)
                except FunPayError:
                    order.thanks_state = "uncertain"
                    await db.commit()
                    await bus.log(
                        "orders",
                        f"Ответ на отзыв #{order.funpay_order_id}: проверьте FunPay перед повтором",
                        order.account_id,
                        "error",
                        source_plugin="reviews",
                    )
                    return
                order.thanks_state = "sent"
                await db.commit()
                await bus.log(
                    "orders",
                    f"Отправлен ответ на отзыв #{order.funpay_order_id} ({rating} ★)",
                    order.account_id,
                    source_plugin="reviews",
                )


automation = AutomationService()
