"""Host adapter for the supplied KOSellRent 1.1.52 Cardinal plugin.

Supplier calls and rental algorithms live exclusively in the unmodified vendor file.
All synchronous entry points run off the ASGI loop. FunPay calls cross back through
the application's serialized gateway; JSON state is stored encrypted in Settings.
"""

import asyncio
import copy
import html
import json
import re
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from .config import config
from .db import Session
from .events import bus
from .funpay import FunPayError
from .kosellrent_source import load_source
from .models import Account, AppRouteMapping, Chat, Lot, Order, PluginBinding
from .security import vault
from .services import accounts, get_setting, save_setting, telegram

ID = "kosell-rent"
lock = asyncio.Lock()
hosts_lock = asyncio.Lock()
instances = {}
poll_task = None
stopping = False


def is_review(text):
    return bool(
        re.search(
            r"(?:Покупатель|The buyer).+(?:оставил отзыв|написал отзыв|изменил отзыв|has given feedback|has changed feedback)",
            text or "",
            re.I,
        )
    )


def clean_html(text):
    return html.unescape(re.sub(r"<[^>]+>", "", str(text)))


class AccountBridge:
    def __init__(self, host):
        self.host = host

    async def _client(self):
        async with Session() as db:
            account = await db.get(Account, self.host.account_id)
            if not account or not account.is_active:
                raise FunPayError("Аккаунт KOSell Rent отключён или удалён")
        return await accounts.client(self.host.account_id)

    def __getattr__(self, name):
        async def access():
            client = await self._client()
            if name == "csrf_token":
                return client.csrf or client.api_account.csrf_token
            return getattr(client.api_account, name)

        value = self.host.wait(access())
        if not callable(value):
            return value

        def invoke(*args, **kwargs):
            async def call():
                client = await self._client()
                return await client.call(getattr(client.api_account, name), *args, **kwargs)

            return self.host.wait(call())

        return invoke

    def get_lot_fields(self, lot_id):
        async def load():
            from FunPayAPI.types import LotFields

            from .lot_editor import load_form

            client = await self._client()
            _, fields = await load_form(client, offer_id=str(lot_id))
            return LotFields(int(lot_id), fields)

        return self.host.wait(load())

    def save_lot(self, lot):
        async def save():
            client = await self._client()
            if int(lot.lot_id) > 0:
                await client.save_lot_fields(lot.renew_fields().fields)
            else:
                await client.call(client.api_account.save_lot, lot)

        return self.host.wait(save())

    def delete_lot(self, lot_id):
        async def delete():
            from .lot_editor import load_form

            client = await self._client()
            _, fields = await load_form(client, offer_id=str(lot_id))
            await client.save_lot_fields({**fields, "deleted": "on"})

        return self.host.wait(delete())


class Host:
    def __init__(self, account_id):
        self.account_id = account_id
        self.loop = asyncio.get_running_loop()
        self.module = load_source()
        self.state = {}
        self.followups = set()
        self.followup_uids = set()
        self.account = AccountBridge(self)
        self.telegram = SimpleNamespace(authorized_users=[0])
        self.profile = SimpleNamespace(get_lots=self.profile_lots)
        self.module.cardinal_instance = self
        self.module.bot = SimpleNamespace(send_message=self.admin_message)
        self.module._load_json = self.load
        self.module._save_json = self.save
        # Cardinal's private directory chmod/migration is replaced by encrypted DB storage.
        self.module._harden_private_paths = lambda: None
        self.module._FP_CMD_DEDUP_FILE = str(
            config.storage_dir / ID / str(account_id) / "command-dedup.json"
        )
        # Host lifecycle owns password followups, including shutdown and mode switches.
        self.module._schedule_password_followup = self.schedule_followup
        original_append = self.module._append_mapping_for_lot

        def append_mapping(lot_id, item):
            if not self.wait(self.can_bind(lot_id)):
                self.module.notify_admins(
                    "Лот уже используется другим плагином; автопривязка пропущена",
                    extra=f"lot_id={lot_id}",
                )
                return False
            return original_append(lot_id, item)

        self.module._append_mapping_for_lot = append_mapping

    async def can_bind(self, lot_id):
        async with Session() as db:
            route = await db.scalar(
                select(AppRouteMapping.id).where(
                    AppRouteMapping.account_id == self.account_id,
                    AppRouteMapping.funpay_item_id == str(lot_id),
                )
            )
            binding = await db.scalar(
                select(PluginBinding)
                .join(Lot)
                .where(
                    Lot.account_id == self.account_id,
                    Lot.funpay_lot_id == str(lot_id),
                )
            )
        return not route and (not binding or binding.kind == ID)

    def wait(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    def key(self, path):
        return f"{ID}:state:{self.account_id}:{Path(path).name}"

    def load(self, path, default):
        return copy.deepcopy(self.state.get(Path(path).name, default))

    def save(self, path, data):
        snapshot = copy.deepcopy(data)
        self.wait(
            save_setting(
                self.key(path),
                {"encrypted": vault.encrypt(json.dumps(snapshot, ensure_ascii=False))},
            )
        )
        self.state[Path(path).name] = snapshot

    async def hydrate(self):
        for name in (
            "settings.json",
            "mappings.json",
            "rentals.json",
            "hidden.json",
            "handled_orders.json",
            "pending_qty_orders.json",
            "stats.json",
            "funpay_map.json",
            "funpay_map_overrides.json",
            "misc_funpay_games.json",
            "name_overrides.json",
        ):
            stored = await get_setting(self.key(name), {})
            if stored.get("encrypted"):
                self.state[name] = json.loads(vault.decrypt(stored["encrypted"]))
        await asyncio.to_thread(self.module.load_all)

    def profile_lots(self):
        return self.account.get_user(self.account.id).get_lots()

    def send_message(self, chat_id, text, **kwargs):
        async def send():
            from .message_service import rate_limiter

            async with Session() as db:
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == self.account_id,
                        Chat.funpay_chat_id == str(chat_id),
                    )
                )
            if not chat:
                await accounts.sync_chats(self.account_id)
                async with Session() as db:
                    chat = await db.scalar(
                        select(Chat).where(
                            Chat.account_id == self.account_id,
                            Chat.funpay_chat_id == str(chat_id),
                        )
                    )
            if not chat:
                raise FunPayError("Диалог покупателя не найден")
            await rate_limiter.send(self.account_id, chat.id, text)

        self.wait(send())

    def admin_message(self, _admin, text, **kwargs):
        async def notify():
            text_plain = clean_html(text)
            warning = "⚠" in text_plain
            event = (
                "errors"
                if warning
                else "lots"
                if any(word in text_plain.lower() for word in ("лотов", "привязок"))
                else "orders"
            )
            await bus.log(
                "plugins",
                text_plain,
                self.account_id,
                "warning" if warning else "info",
                source_plugin=ID,
            )
            await telegram.notify(event, text_plain)

        self.wait(notify())

    def schedule_followup(self, uid, login, chat_id, order_id, notify_fail=True):
        # Same original worker/arguments; interruptible sleep lets shutdown drain it.
        def schedule():
            if uid in self.followup_uids or stopping:
                return
            self.followup_uids.add(uid)
            task = asyncio.create_task(self.followup(uid, login, chat_id, order_id, notify_fail))
            self.followups.add(task)
            task.add_done_callback(self.followups.discard)
            task.add_done_callback(lambda _: self.followup_uids.discard(uid))

        self.loop.call_soon_threadsafe(schedule)

    async def followup(self, uid, login, chat_id, order_id, notify_fail):
        # Cardinal's daemon worker is translated into a managed task with the same
        # five-second polling, ten-minute deadline, password fields and delivery.
        module = self.module
        for _ in range(120):
            await asyncio.sleep(5)
            if stopping or not module.SETTINGS.get("enabled"):
                return
            async with lock:
                api = module._get_api()
                if not api:
                    return
                status = await run_sync(api.rotate_password_status, uid)
                password = (status or {}).get("new_password") or (status or {}).get("password")
                if password:
                    await run_sync(module._password_followup_deliver, uid, login, chat_id, password)
                    return
        if notify_fail:
            await run_sync(
                module.notify_admins,
                "Не удалось получить пароль после смены за 10 минут — проверьте статус ротации.",
                extra=f"order_id={order_id}",
            )


async def run_sync(function, *args, **kwargs):
    # Cancellation must not leave a supplier purchase running after clients close.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise


async def host_for(account_id):
    async with hosts_lock:
        if account_id not in instances:
            host = Host(account_id)
            await host.hydrate()
            instances[account_id] = host
        return instances[account_id]


async def selected():
    control = await get_setting(ID, {})
    ident = control.get("account_id")
    if not ident:
        return control, None
    async with Session() as db:
        account = await db.get(Account, ident)
    if not account or not account.is_active:
        return control, None
    return control, await host_for(ident)


async def toggle(enabled):
    from .kosellrent_api import idle

    idle()
    async with lock:
        idle()
        control, host = await selected()
        if enabled and (
            not host or not host.module.SETTINGS.get("api_key") or not host.module.MAPPINGS
        ):
            raise ValueError("Выберите активный аккаунт, сохраните API-ключ и привяжите лоты")
        control["enabled"] = enabled
        await save_setting(ID, control)
        if host:
            host.module.SETTINGS["enabled"] = enabled
            await run_sync(host.module.save_settings)
    return {
        "id": ID,
        "enabled": enabled,
        "available": True,
        "configured": bool(host and host.module.SETTINGS.get("api_key") and host.module.MAPPINGS),
    }


async def ensure_for_order(order_id):
    if stopping:
        return False
    async with lock:
        if stopping:
            return False
        control, host = await selected()
        if not control.get("enabled") or not host:
            return False
        async with Session() as db:
            order = await db.get(Order, order_id)
            if (
                not order
                or order.account_id != host.account_id
                or order.status != "paid"
                or order.delivery_state != "pending"
            ):
                return False
            if (
                order.funpay_lot_id
                and not any(
                    str(m.get("lot_id")) == order.funpay_lot_id for m in host.module.MAPPINGS
                )
                and not host.module._extract_rent_codes(order.description)
            ):
                return False
            # Obtain original SDK order: preserves amount/price/subcategory/html
            # instead of guessing quantity or treating the order total as unit price.
            client = await accounts.client(order.account_id)
            original = await client.call(client.api_account.get_order, order.funpay_order_id)
            if original.seller_id != client.user_id or original.status.name.lower() != "paid":
                return False
            lot_id = order.funpay_lot_id
            if not lot_id:
                # Cardinal's on_new_order performs this before matching. The
                # host must do the same when deciding which engine claims it.
                found = re.search(
                    r"/(?:lots|chips)/offer\?id=(\d+)", getattr(original, "html", "") or ""
                )
                lot_id = found.group(1) if found else None
            mapping = await run_sync(host.module._match_mapping, original, lot_id)
            if not mapping or not await host.can_bind(mapping.get("lot_id")):
                return False
            order.delivery_state = ID
            await db.commit()  # Claim before any supplier write; never let another engine retry.
        host.module.SETTINGS["enabled"] = True
        await run_sync(
            host.module.on_new_order, host, SimpleNamespace(order=original, lot_id=lot_id)
        )
        await bus.log(
            "plugins", f"Заказ #{original.id} принят KOSell Rent", host.account_id, source_plugin=ID
        )
        return True


async def handle_message(chat_id, account_id, payload, *, system=False):
    if stopping:
        return False
    control = await get_setting(ID, {})
    if not control.get("enabled") or control.get("account_id") != account_id:
        return False
    async with lock:
        if stopping:
            return False
        _, host = await selected()
        if not host:
            return False
        async with Session() as db:
            chat = await db.get(Chat, chat_id)
        if not chat or chat.account_id != account_id or payload.get("is_my"):
            return False
        module = host.module
        text = payload.get("text", "")
        buyer_key = module._buyer_key(chat.funpay_chat_id)
        if system:
            if not module._RX_REVIEW_GIVEN.search(text) and "★" not in text:
                return False
            await run_sync(
                module._handle_review,
                SimpleNamespace(
                    chat_id=chat.funpay_chat_id,
                    id=payload.get("funpay_message_id"),
                    text=text,
                ),
            )
            return True
        # Avoid intercepting other plugins' commands for customers with no KOSell rental.
        relevant = bool(
            module.RENTALS.get(buyer_key)
            or module._pending_qty_for_buyer(buyer_key)
            or text.strip().lower().split()[0:1] in [["!друг"], ["!friend"]]
        )
        if not relevant:
            return False
        module.SETTINGS["enabled"] = True
        await run_sync(module._handle_buyer_text, chat.funpay_chat_id, text, chat.interlocutor_name)
        return text.strip().lower().split(" ")[0] in {
            "!friend",
            "!друг",
            "!кодстим",
            "!code",
            "!продлить",
            "!prod",
            "!extend",
        } or bool(module._qty_answer(text) and relevant)


async def tick():
    async with lock:
        if stopping:
            return
        control, host = await selected()
        if not control.get("enabled") or not host:
            return
        module = host.module
        module.SETTINGS["enabled"] = True
        for records in list(module.RENTALS.values()):
            for rental in list(records):
                if rental.get("password_pending") and rental.get("expires_ts", 0) > module._now():
                    host.schedule_followup(
                        rental["uid"],
                        rental.get("login", ""),
                        rental.get("chat_id"),
                        rental.get("order_id"),
                    )
        for function in (
            module._notify_expirations,
            module._expire_pending_qty_orders,
            module._poll_delivery_pending,
            module._cleanup_rentals,
        ):
            await run_sync(function)
        if module.SETTINGS.get("auto_hide_no_stock") or module.SETTINGS.get(
            "auto_hide_zero_balance"
        ):
            await run_sync(module._poll_cycle)


async def poll():
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await bus.log(
                "plugins",
                f"Фоновая проверка KOSell Rent: {type(error).__name__}",
                source_plugin=ID,
                level="error",
            )
        control = await get_setting(ID, {})
        host = instances.get(control.get("account_id"))
        await asyncio.sleep(max(15, host.module.SETTINGS.get("poll_sec", 60)) if host else 60)


async def start():
    global poll_task, stopping
    stopping = False
    if not poll_task or poll_task.done():
        poll_task = asyncio.create_task(poll(), name=ID)


async def stop():
    global poll_task, stopping
    stopping = True
    tasks = ([poll_task] if poll_task else []) + [
        t for host in instances.values() for t in host.followups
    ]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    # Drain foreground source calls before the application's FunPay clients close.
    async with lock:
        pass
    poll_task = None


async def stop_account(ident):
    """Drain a loaded plugin before its seller's gateway is discarded."""
    host = instances.get(ident)
    if not host:
        return
    from . import kosellrent_api

    await kosellrent_api.stop_operation()
    for task in list(host.followups):
        task.cancel()
    await asyncio.gather(*host.followups, return_exceptions=True)
    async with lock:
        host.module.SETTINGS["enabled"] = False
