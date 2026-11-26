from __future__ import annotations

import asyncio
import logging
import random

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .db import Session
from .events import bus
from .funpay import FunPayError, UnauthorizedException
from .loyalty import award, revoke
from .models import Account, Chat, Lot, LotPromotion, Order, Proxy, utcnow
from .promotions import restore as restore_promotion
from .services import accounts, automation, get_setting, telegram


class WorkerManager:
    def __init__(self):
        self.tasks: dict[int, asyncio.Task] = {}
        self.health: dict[int, dict] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._stopping = False
        self.scheduler = AsyncIOScheduler(
            timezone="UTC",
            job_defaults={
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 30,
            },
        )

    async def start(self):
        from . import event_pipeline, reply_engine

        async with self._lifecycle_lock:
            if self.scheduler.running and not self._stopping:
                return
            self._stopping = False
            await reply_engine.recover()
            await automation.recover_deliveries()
            await event_pipeline.recover()
            self.scheduler.add_job(event_pipeline.dispatch, "interval", seconds=2, id="core-events", replace_existing=True)
            self.scheduler.add_job(reply_engine.flush, "interval", seconds=1, id="reply-sends", replace_existing=True)
            self.scheduler.add_job(reply_engine.tick, "interval", seconds=10, id="account-replies", replace_existing=True)
            self.scheduler.add_job(self.reconcile, "interval", seconds=10, id="accounts", replace_existing=True)
            self.scheduler.add_job(self.auto_raise, "interval", seconds=30, id="raise", replace_existing=True)
            self.scheduler.add_job(self.proxies, "interval", minutes=5, id="proxies", replace_existing=True)
            self.scheduler.start()
        await self.reconcile()

    def begin_shutdown(self):
        self._stopping = True
        if self.scheduler.running:
            self.scheduler.pause()

    async def stop_account(self, ident: int):
        async with self._lifecycle_lock:
            await self._stop_account(ident)

    async def _stop_account(self, ident: int):
        self.health.setdefault(ident, {})["state"] = "STOPPING"
        task = self.tasks.pop(ident, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                logging.getLogger("uvicorn.error").warning(
                    "Stopped failed account worker: %s", type(error).__name__
                )
        from . import kosellrent

        await kosellrent.stop_account(ident)
        await accounts.discard(ident)
        self.health[ident]["state"] = "STOPPED"

    async def stop(self):
        self.begin_shutdown()
        async with self._lifecycle_lock:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                # AsyncIOScheduler schedules shutdown on the event loop.
                await asyncio.sleep(0)
            errors = []
            for ident in list(self.tasks):
                try:
                    await self._stop_account(ident)
                except Exception as error:
                    errors.append(error)
            try:
                await accounts.close()
            except Exception as error:
                errors.append(error)
            if errors:
                raise RuntimeError("Не все соединения аккаунтов подтвердили остановку")

    async def reconcile(self):
        async with self._lifecycle_lock:
            if self._stopping:
                return
            async with Session() as db:
                active = set(
                    (await db.scalars(select(Account.id).where(Account.is_active.is_(True)))).all()
                )
            if self._stopping:
                return
            for ident in set(self.tasks) - active:
                await self._stop_account(ident)
            for ident in active:
                if ident not in self.tasks or self.tasks[ident].done():
                    self.tasks[ident] = asyncio.create_task(self.run(ident), name=f"funpay-{ident}")

    async def run(self, ident: int):
        self.health[ident] = {"state": "STARTING", "last_poll": None, "last_error": None}
        failures = 0
        ready = False
        cycles = 0
        while True:
            try:
                client = await accounts.client(ident)
                if not ready:
                    await accounts.sync(ident)
                    await accounts.refresh_contacts(ident)
                    ready = True
                async with Session() as db:
                    chats = list(
                        (
                            await db.scalars(
                                select(Chat)
                                .where(Chat.account_id == ident)
                                .order_by(Chat.runner_baselined.asc(), Chat.runner_checked_at.asc(), Chat.id.asc())
                                .limit(10)
                            )
                        ).all()
                    )
                    ids = [chat.funpay_chat_id for chat in chats]
                    cursors = {chat.funpay_chat_id: chat.last_message_cursor for chat in chats}
                if getattr(client, "native_runner", False) is True:
                    result = await client.poll(ids, cursors)
                else:
                    result = await client.poll(ids)
                self.health[ident].update(state="RUNNING", last_poll=utcnow().isoformat(), last_error=None)
                await accounts.sync_runner_avatars(ident, result)
                # First poll imports history without firing autoresponses or Telegram floods.
                await accounts.ingest(ident, result.get("objects", []),
                                      automate=bool(getattr(client, "native_runner", False)) or cycles > 0,
                                      confirmed_new=getattr(client, "native_runner", False) is True)
                kinds = {item.get("type") for item in result.get("objects", [])}
                if cycles == 0 or "chat_bookmarks" in kinds:
                    await accounts.sync_chats(ident)
                if "orders_counters" in kinds or cycles % 30 == 0:
                    await self.sync_orders(ident, initial=cycles == 0)
                failures = 0
                cycles += 1
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                self.health[ident].update(
                    state="AUTH_ERROR" if isinstance(error, UnauthorizedException) else "DEGRADED",
                    last_error=type(error).__name__,
                )
                await accounts.discard(ident)
                ready = False
                text = (
                    str(error)[:500]
                    if isinstance(error, FunPayError)
                    else "Внутренняя ошибка синхронизации; проверьте формат данных FunPay"
                )
                async with Session() as db:
                    account = await db.get(Account, ident)
                    if not account:
                        return
                    account.status = "error"
                    account.last_error = text
                    await db.commit()
                await bus.log("accounts", text, ident, "error")
                if failures == 1:
                    await telegram.notify("errors", f"Аккаунт #{ident}: {text}")
                await asyncio.sleep(
                    300
                    if isinstance(error, UnauthorizedException)
                    else min(300, 2 ** min(failures, 8)) + random.random()
                )

    async def sync_orders(self, ident: int, *, initial: bool = False):
        rows = await (await accounts.client(ident)).get_orders()
        deliver = []
        bonuses = []
        refunds = []
        notifications = []
        async with Session() as db:
            for data in rows:
                order = await db.scalar(
                    select(Order).where(Order.funpay_order_id == data["funpay_order_id"])
                )
                if order:
                    order.status = data["status"]
                    if data.get("funpay_lot_id"):
                        order.funpay_lot_id = data["funpay_lot_id"]
                else:
                    order = Order(
                        account_id=ident,
                        **data,
                        delivery_state="historical" if initial else "pending",
                        thanks_state="historical" if initial else "pending",
                        date_source="history_unknown" if initial else "observed",
                    )
                    db.add(order)
                    await db.flush()
                    if not initial:
                        notifications.append(
                            (
                                order.id,
                                order.funpay_order_id,
                                order.description,
                                order.sum,
                                order.currency,
                            )
                        )
                chat = (
                    await db.scalar(
                        select(Chat).where(
                            Chat.account_id == ident,
                            (Chat.interlocutor_id == order.buyer_id)
                            | (Chat.interlocutor_name == order.buyer_name),
                        )
                    )
                    if order.buyer_id
                    else None
                )
                if chat:
                    chat.interlocutor_id = order.buyer_id
                    if order.status == "paid":
                        chat.current_order_id = order.funpay_order_id
                    elif chat.current_order_id == order.funpay_order_id:
                        chat.current_order_id = None
                if order.delivery_state == "pending":
                    deliver.append(order.id)
                if order.status == "closed" and order.date_source == "observed":
                    bonuses.append(order.id)
                if order.status == "refunded":
                    refunds.append(order.id)
            await db.commit()
        for internal_id, uid, description, amount, currency in notifications:
            await bus.publish(
                "orders", {"account_id": ident, "event": "ORDER_PAID", "order_id": uid}
            )
            await bus.log("orders", f"Новый заказ #{uid}", ident)
            await telegram.notify("orders", f"Заказ #{uid}: {description}, {amount} {currency}")
        for order_id in deliver:
            from . import kosellrent

            if await kosellrent.ensure_for_order(order_id):
                continue
            await automation.deliver(order_id)
            await self.plugin_delivery(order_id)
        for order_id in bonuses:
            try:
                await award(order_id)
            except FunPayError as error:
                await bus.log("orders", f"Бонус за заказ: {error}", ident, "error")
        for order_id in refunds:
            await revoke(order_id)

    async def plugin_delivery(self, order_id: int):
        """Цепочка движков доставки плагинов: пока заказ не принят движком
        (delivery_state остаётся "pending"), пробуем следующий."""
        from . import app_route, fulfillment, integrated_jobs, kosell, kosellrent

        async with Session() as db:
            order = await db.get(Order, order_id)
            if (
                not order
                or order.status != "paid"
                or order.delivery_state != "pending"
            ):
                return
        for engine, args in (
            (kosellrent.ensure_for_order, ()),
            (fulfillment.ensure_for_order, ()),
            (kosell.ensure_for_order, ()),
            (integrated_jobs.ensure_for_order, ()),
            (app_route.deliver, ()),
        ):
            try:
                claimed = await engine(order_id, *args)
            except Exception as error:

                text = str(error)[:300]
                await bus.log(
                    "orders",
                    f"Движок доставки {getattr(engine, '__module__', '')}: {text}",
                    None,
                    "error",
                )
                continue
            if claimed:
                return

    async def heartbeat(self):
        if not (await get_setting("plugin_controls", {})).get("offline", True):
            return
        settings = await get_setting("offline", {})
        if not settings.get("heartbeat"):
            return
        async with Session() as db:
            ids = (await db.scalars(select(Account.id).where(Account.is_active.is_(True)))).all()
        for ident in ids:
            try:
                await (await accounts.client(ident)).request("GET", "/", safe=True)
            except FunPayError as error:
                await bus.log(
                    "accounts", f"Фоновый запрос: {error}", ident, "error", source_plugin="offline"
                )

    async def auto_raise(self):
        async with Session() as db:
            lots = (
                await db.scalars(
                    select(Lot)
                    .join(Account)
                    .where(
                        Account.is_active.is_(True),
                        Lot.auto_raise.is_(True),
                        Lot.is_active.is_(True),
                    )
                )
            ).all()
        groups = {
            (lot.account_id, lot.node_id, lot.game_id)
            for lot in lots
            if lot.next_raise_at is None
            or lot.next_raise_at.replace(tzinfo=utcnow().tzinfo) <= utcnow()
        }
        for ident, node, game in groups:
            from . import kosellrent

            host = kosellrent.instances.get(ident)
            if host and host.module._ac_running.get("on"):
                continue
            try:
                await accounts.raise_group(ident, node, game)
            except FunPayError as error:
                await bus.log("lots", str(error), ident, "error")

    async def proxies(self):
        async with Session() as db:
            ids = (await db.scalars(select(Proxy.id))).all()
        semaphore = asyncio.Semaphore(10)

        async def check(ident):
            async with semaphore:
                await accounts.check_proxy(ident)

        await asyncio.gather(*(check(ident) for ident in ids))

    async def telegram(self):
        try:
            await telegram.poll_replies()
        except FunPayError as error:
            await bus.log("notifications", str(error), level="error")

    async def reviews(self):
        settings = await get_setting("plugins", {})
        if not settings.get("auto_thanks"):
            return
        async with Session() as db:
            orders = (
                await db.scalars(
                    select(Order)
                    .join(Account)
                    .where(
                        Order.status == "closed",
                        Order.thanks_state == "pending",
                        Account.is_active.is_(True),
                    )
                    .order_by(Order.created_at.desc())
                    .limit(20)
                )
            ).all()
        for order in orders:
            from .reply_engine import configured

            if (
                await configured()
                or (await get_setting(f"account-automation:{order.account_id}", {})).get("version")
                == 2
            ):
                continue
            try:
                await automation.thank(order.id, settings)
            except FunPayError as error:
                await bus.log(
                    "orders", str(error), order.account_id, "error", source_plugin="reviews"
                )

    async def promotions(self):
        async with Session() as db:
            due = (
                await db.scalars(
                    select(LotPromotion)
                    .where(LotPromotion.state == "active", LotPromotion.ends_at <= utcnow())
                    .limit(50)
                )
            ).all()
        for promotion in due:
            try:
                await restore_promotion(promotion.id)
            except (FunPayError, ValueError) as error:
                await bus.log(
                    "lots", f"Акция #{promotion.id}: {error}", promotion.account_id, "error"
                )


workers = WorkerManager()
