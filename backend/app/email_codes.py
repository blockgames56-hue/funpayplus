"""IMAP IDLE listener for mailboxes explicitly supplied by their owner."""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import re
import secrets
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from bs4 import BeautifulSoup

from .crypto import Vault
from .events import bus
from .services import get_setting, save_setting


@dataclass
class MailboxConfig:
    ident: str
    host: str
    username: str
    password: str = field(repr=False)
    port: int = 993
    folder: str = "INBOX"
    proxy_host: str | None = None
    proxy_port: int = 1080
    proxy_type: str = "socks5"
    proxy_username: str | None = None
    proxy_password: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.ident):
            raise ValueError("Invalid mailbox ID")
        if self.proxy_type not in {"socks5", "http"}:
            raise ValueError("Proxy must be socks5 or http (CONNECT)")
        if not self.host or not 1 <= self.port <= 65535 or not 1 <= self.proxy_port <= 65535:
            raise ValueError("Invalid mail server or port")


def steam_code(raw: bytes, *, now: datetime | None = None) -> str | None:
    """Reject old, ambiguous and unrelated mail; never accept attachment contents."""
    if len(raw) > 1024 * 1024:
        return None
    message = BytesParser(policy=policy.default).parsebytes(raw)
    senders = getaddresses(message.get_all("From", []))
    if len(senders) != 1 or senders[0][1].casefold() != "noreply@steampowered.com":
        return None
    try:
        date = parsedate_to_datetime(message["Date"])
        if date.tzinfo is None:
            return None
        age = ((now or datetime.now(timezone.utc)) - date).total_seconds()
        if not -120 <= age <= 600:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    bodies = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment" or part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            continue
        if part.get_content_type() == "text/html":
            content = BeautifulSoup(content, "html.parser").get_text("\n")
        bodies.append(content)
    # Steam Guard is five alphanumeric characters, not necessarily five digits.
    codes = set(re.findall(r"(?m)^\s*([A-Z0-9]{5})\s*$", "\n".join(bodies)))
    return codes.pop() if len(codes) == 1 else None


def epic_code(raw: bytes, *, now: datetime | None = None) -> str | None:
    if len(raw) > 1024 * 1024:
        return None
    message = BytesParser(policy=policy.default).parsebytes(raw)
    senders = getaddresses(message.get_all("From", []))
    if len(senders) != 1 or senders[0][1].casefold() not in {"help@accts.epicgames.com", "noreply@accts.epicgames.com"}:
        return None
    try:
        date = parsedate_to_datetime(message["Date"])
        if date.tzinfo is None or not -120 <= ((now or datetime.now(timezone.utc)) - date).total_seconds() <= 600:
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    bodies = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment" or part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            continue
        bodies.append(BeautifulSoup(content, "html.parser").get_text("\n") if part.get_content_type() == "text/html" else content)
    codes = set(re.findall(r"\b\d{6}\b", "\n".join(bodies)))
    return codes.pop() if len(codes) == 1 else None


def connect(config: MailboxConfig):
    from imapclient import IMAPClient

    class ProxyTLS(imaplib.IMAP4_SSL):
        def _create_socket(self, timeout):
            import socks

            sock = socks.create_connection(
                (config.host, config.port), timeout=timeout,
                proxy_type=socks.SOCKS5 if config.proxy_type == "socks5" else socks.HTTP,
                proxy_addr=config.proxy_host, proxy_port=config.proxy_port,
                proxy_rdns=True, proxy_username=config.proxy_username,
                proxy_password=config.proxy_password,
            )
            try:
                return self.ssl_context.wrap_socket(sock, server_hostname=config.host)
            except BaseException:
                sock.close()
                raise

    class ProxyClient(IMAPClient):
        def _create_IMAP4(self):
            return ProxyTLS(self.host, self.port, ssl_context=self.ssl_context, timeout=20)

    cls = ProxyClient if config.proxy_host else IMAPClient
    client = cls(config.host, port=config.port, ssl=True,
                 ssl_context=ssl.create_default_context(), timeout=20)
    try:
        client.login(config.username, config.password)
        if not client.has_capability("IDLE"):
            raise RuntimeError("IMAP server does not support IDLE")
        return client
    except BaseException:
        with contextlib.suppress(Exception):
            client.shutdown()
        raise


class IMAPClient:
    """One dedicated thread owns the IMAP connection; callbacks run on the app loop."""

    def __init__(self, config: MailboxConfig, *, connector=connect):
        self.config = config
        self.connector = connector
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop = None
        self.connected = False

    async def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"imap-{self.config.ident}", daemon=True)
        self._thread.start()

    async def stop(self):
        self._stop.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join)
            self._thread = None

    def _call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    def _run(self):
        key = f"imap_cursor_{self.config.ident}"
        failures = 0
        while not self._stop.is_set():
            client = None
            try:
                client = self.connector(self.config)
                info = client.select_folder(self.config.folder, readonly=True)
                self.connected = True
                self._call(bus.log("notifications", "IMAP IDLE подключён", source_plugin="email-code"))
                validity = int(info[b"UIDVALIDITY"])
                cursor = self._call(get_setting(key, None))
                if not cursor or cursor["validity"] != validity:
                    unseen = client.search(["UNSEEN"])
                    newest = max(unseen, default=int(info[b"UIDNEXT"]))
                    cursor = {"validity": validity, "uid": newest - 1}
                    self._call(save_setting(key, cursor))
                while not self._stop.is_set():
                    # UID search also runs after reconnect to recover mail received offline.
                    uids = client.search(["UNSEEN", "UID", f"{cursor['uid'] + 1}:*"])
                    for uid in sorted(u for u in uids if u > cursor["uid"]):
                        if self._stop.is_set():
                            break
                        meta = client.fetch([uid], ["RFC822.SIZE"])[uid]
                        raw = b""
                        if meta[b"RFC822.SIZE"] <= 1024 * 1024:
                            parts = client.fetch([uid], ["BODY.PEEK[HEADER]", "BODY.PEEK[TEXT]"])[uid]
                            raw = parts.get(b"BODY[HEADER]", b"") + b"\r\n" + parts.get(b"BODY[TEXT]", b"")
                        steam = steam_code(raw) if raw else None
                        service = "steam" if steam else "epic"
                        code = steam or (epic_code(raw) if raw else None)
                        if code:
                            self._call(bus.emit("new_2fa_code", {
                                "mailbox_id": self.config.ident, "service": service,
                                "code": code, "uid": uid, "uidvalidity": validity,
                                "timestamp": int(time.time() * 1000),
                            }))
                        cursor["uid"] = uid
                        self._call(save_setting(key, cursor))
                    failures = 0
                    if self._stop.is_set():
                        break
                    client.idle()
                    try:
                        deadline = time.monotonic() + 25 * 60
                        while not self._stop.is_set() and time.monotonic() < deadline:
                            if client.idle_check(timeout=2):
                                break
                    finally:
                        client.idle_done()
            except Exception:
                # Do not expose server responses, credentials or codes in logs.
                failures += 1
                with contextlib.suppress(Exception):
                    self._call(bus.log("notifications", "Почтовое соединение прервано; переподключение", level="warning", source_plugin='email-code'))
            finally:
                self.connected = False
                if client:
                    with contextlib.suppress(Exception):
                        client.shutdown()
            if not self._stop.is_set():
                self._stop.wait(5)


EmailCodeFetcher = IMAPClient


def mail_vault() -> Vault:
    """Keep the mail encryption key in the OS credential store."""
    from .config import config

    if config.mailbox_key_store == "master":
        # Headless servers have no logged-in desktop credential store.
        return Vault(config.secret_key)
    if config.mailbox_key_store != "system":
        raise ValueError("MAILBOX_KEY_STORE must be system or master")
    import keyring

    service = "FunPay Plus Email Code Fetcher"
    key = keyring.get_password(service, "mailbox-encryption-key")
    if not key:
        key = secrets.token_urlsafe(48)
        keyring.set_password(service, "mailbox-encryption-key", key)
        if keyring.get_password(service, "mailbox-encryption-key") != key:
            raise RuntimeError("Системное хранилище ключей недоступно")
    return Vault(key)


async def save_mailbox(config: MailboxConfig):
    from dataclasses import asdict

    data = asdict(config)
    cipher = mail_vault()
    for name in ("password", "proxy_password"):
        if data[name]:
            data[name] = cipher.encrypt(data[name])
    await save_setting(f"imap_mailbox_{config.ident}", data)


async def load_mailbox(ident: str) -> MailboxConfig:
    data = await get_setting(f"imap_mailbox_{ident}")
    if not data:
        raise ValueError("Mailbox not configured")
    cipher = mail_vault()
    for name in ("password", "proxy_password"):
        if data[name]:
            data[name] = cipher.decrypt(data[name])
    return MailboxConfig(**data)


_fetcher: IMAPClient | None = None


async def start_saved_mailbox() -> None:
    global _fetcher
    if not (await get_setting("plugin_controls", {})).get("email-code", True):
        return
    if _fetcher:
        return
    if not await get_setting("imap_mailbox_default", None):
        return
    try:
        config = await load_mailbox("default")
    except Exception:
        await bus.log("notifications", "Не удалось открыть почтовые учётные данные", level="error", source_plugin='email-code')
        return
    _fetcher = IMAPClient(config)
    await _fetcher.start()


async def stop_mailbox() -> None:
    global _fetcher
    if _fetcher:
        await _fetcher.stop()
        _fetcher = None


async def restart_mailbox(config: MailboxConfig) -> None:
    await stop_mailbox()
    await save_mailbox(config)
    await start_saved_mailbox()


def mailbox_connected() -> bool:
    return bool(_fetcher and _fetcher.connected)
