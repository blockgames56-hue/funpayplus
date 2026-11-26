"""Durable delivery dispatcher shared by SMM, Steam Points, Stars and rentals."""

import asyncio
import re
from datetime import timedelta

import httpx
from sqlalchemy import select

from . import rental_verification
from .app_route import detail_lot_id
from .db import Session
from .events import bus
from .funpay import FunPayError
from .integrated_config import packed, plain_config, stored_config, unpacked
from .integrated_schemas import KINDS
from .message_templates import order_context, render
from .message_templates import send as send_template
from .models import (
    Account,
    Activity,
    BlacklistEntry,
    Chat,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    utcnow,
)
from .plugin_providers import (
    ProviderError,
    broadcast_stars,
    parse_target,
    points_request,
    prepare_stars,
    smm_request,
    stars_status,
)
from .security import vault
from .services import accounts, telegram

claim_lock = asyncio.Lock()
stars_lock = asyncio.Lock()
running: dict[int, asyncio.Task] = {}
job_locks: dict[int, asyncio.Lock] = {}


def start_job(ident):
    if ident in running and not running[ident].done():
        return
    task = asyncio.create_task(process_job(ident), name=f"plugin-job-{ident}")
    running[ident] = task

    def done(completed):
        running.pop(ident, None)
        job_locks.pop(ident, None)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(done)


async def stop():
    tasks = list(running.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def ensure_for_order(order_id):
    async with claim_lock:
        async with Session() as db:
            existing = await db.scalar(select(PluginJob).where(PluginJob.order_id == order_id))
            if existing:
                return True
            order = await db.get(Order, order_id)
            if (
                not order
                or order.status != "paid"
                or order.delivery_state != "pending"
                or not order.buyer_id
            ):
                return False
            if await db.scalar(
                select(BlacklistEntry.id).where(BlacklistEntry.user_id == order.buyer_id)
            ):
                order.delivery_state = "blocked"
                await db.commit()
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
                    )
                )
                if chat and (await plain_config("steam-rent")).get("enabled"):
                    await send_template("steam-rent", "rent_blocked", chat.id, order_context(order))
                return True
            if not await db.scalar(
                select(PluginBinding.id)
                .join(Lot)
                .where(
                    Lot.account_id == order.account_id,
                    PluginBinding.is_active.is_(True),
                    PluginBinding.kind.in_(KINDS),
                )
                .limit(1)
            ):
                return False
            detail = await (await accounts.client(order.account_id)).order_details(
                order.funpay_order_id
            )
            lot_id = detail_lot_id(detail) or order.funpay_lot_id
            if not lot_id:
                return False
            binding = await db.scalar(
                select(PluginBinding)
                .join(Lot)
                .where(
                    Lot.account_id == order.account_id,
                    Lot.funpay_lot_id == lot_id,
                    PluginBinding.is_active.is_(True),
                    PluginBinding.kind.in_(KINDS),
                )
            )
            if not binding:
                return False
            config = await plain_config(binding.kind)
            if not config.get("enabled"):
                return False
            if (
                detail.get("status") != "paid"
                or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
            ):
                return True
            chat = await db.scalar(
                select(Chat).where(
                    Chat.account_id == order.account_id, Chat.interlocutor_id == order.buyer_id
                )
            )
            if not chat:
                return True
            units = detail.get("quantity", detail.get("amount", 1))
            if isinstance(units, bool) or not str(units).isdigit() or not 1 <= int(units) <= 10000:
                await bus.log(
                    "orders",
                    f"Заказ #{order.funpay_order_id}: не удалось определить количество",
                    order.account_id,
                    "error",
                    source_plugin=binding.kind,
                )
                return True
            quantity = binding.quantity * int(units)
            if quantity > 1000000:
                return True
            provider = next(
                (row for row in config.get("providers", []) if row["id"] == binding.provider_id),
                None,
            )
            if binding.kind == "auto-smm" and (not provider or not binding.service_id):
                return True
            payload = {
                "config": config,
                "provider": provider,
                "service_id": binding.service_id,
                "is_extension": binding.is_extension,
                "chat_id": chat.id,
                "units": int(units),
                "points_per_unit": binding.quantity,
            }
            state = (
                "waiting_review"
                if binding.kind == "steam-rent"
                and await rental_verification.needs_review(config, binding, order, db)
                else "ready"
                if binding.kind == "steam-rent"
                else "waiting_target"
            )
            job = PluginJob(
                order_id=order.id,
                binding_id=binding.id,
                kind=binding.kind,
                quantity=quantity,
                state=state,
                payload=packed(payload),
            )
            db.add(job)
            if state == "waiting_review":
                from .rental_verification import begin

                await begin(job, payload, config, db)
            order.funpay_lot_id = lot_id
            order.delivery_state = "integrated_pending"
            await db.commit()
            ident, kind = job.id, job.kind
            if kind == "steam-rent" and state == "waiting_review":
                from .rental_verification import context

                text = await render(kind, "pc_request", context(order, config, payload), db=db)
            elif kind != "steam-rent":
                from .integrated_schemas import CONFIG_MODELS

                custom = (
                    config["start_message"]
                    if config["start_message"] != CONFIG_MODELS[kind]().start_message
                    else None
                )
                text = await render(
                    kind,
                    "delivery_start",
                    order_context(
                        order,
                        quantity=quantity,
                        target="",
                        expires="",
                        units=int(units),
                        points_per_unit=binding.quantity,
                    ),
                    db=db,
                    legacy=custom,
                )
            else:
                text = ""
    if text:
        try:
            await accounts.send(chat.id, text)
        except FunPayError:
            await bus.log(
                "orders",
                f"Заказ #{order.funpay_order_id}: проверьте отправку начального сообщения",
                order.account_id,
                "error",
                source_plugin=kind,
            )
    if state == "ready":
        start_job(ident)
    await bus.publish("orders", {"event": "PLUGIN_JOB", "order_id": order_id})
    return True


async def update_target(ident, target):
    async with job_locks.setdefault(ident, asyncio.Lock()):
        async with Session() as db:
            job = await db.get(PluginJob, ident)
            if not job or job.state not in {"waiting_target", "confirm_target", "error"}:
                raise ValueError("Заказ уже отправлен или не принимает новые данные")
            if job.remote_id:
                raise ValueError("Получатель уже передан поставщику и не может быть изменён")
            payload = unpacked(job.payload)
            normalized = parse_target(job.kind, target, payload["config"])
            if not normalized:
                raise ValueError("Неверный адрес получателя")
            job.target = normalized
            job.state = (
                "confirm_target" if payload["config"].get("confirm_target", True) else "ready"
            )
            job.last_error = None
            await db.commit()
            state = job.state
    if state == "ready":
        start_job(ident)
    return state


async def confirm_target(ident):
    async with job_locks.setdefault(ident, asyncio.Lock()):
        async with Session() as db:
            job = await db.get(PluginJob, ident)
            if not job or job.state != "confirm_target" or not job.target:
                raise ValueError("Сначала укажите получателя")
            job.state = "ready"
            await db.commit()
    start_job(ident)


async def handle_message(chat_id, account_id, message):
    if message.get("is_my"):
        return False
    from .steam_rentals import handle_command

    if await handle_command(chat_id, account_id, message):
        return True
    command = re.fullmatch(r"(чек|рефилл)\s+(\d+)", message.get("text", "").strip(), re.I)
    if command:
        if not (await stored_config("auto-smm")).get("enabled"):
            return False
        async with Session() as db:
            ident = await db.scalar(
                select(PluginJob.id)
                .join(Order)
                .where(
                    Order.account_id == account_id,
                    Order.buyer_id == message.get("sender_id"),
                    PluginJob.kind == "auto-smm",
                    PluginJob.remote_id == command[2],
                )
            )
        if not ident:
            return False
        async with job_locks.setdefault(ident, asyncio.Lock()):
            return await smm_command(ident, chat_id, command)
    async with Session() as db:
        chat = await db.get(Chat, chat_id)
        if not chat or not chat.current_order_id or chat.account_id != account_id:
            return False
        job = await db.scalar(
            select(PluginJob)
            .join(Order)
            .where(
                Order.account_id == account_id,
                Order.funpay_order_id == chat.current_order_id,
                Order.buyer_id == message.get("sender_id"),
                PluginJob.state.in_(["waiting_target", "confirm_target", "waiting_review"]),
            )
        )
        if not job:
            return False
        if not (await stored_config(job.kind)).get("enabled"):
            return False
        ident, state = job.id, job.state
        review_job = ident if state == "waiting_review" else None
    if review_job:
        from .rental_verification import on_message

        await on_message(review_job, message)
        return True
    text = message.get("text", "").strip()
    if state == "confirm_target" and text == "+":
        await confirm_target(ident)
        return True
    if state == "confirm_target" and text == "-":
        async with Session() as db:
            job = await db.get(PluginJob, ident)
            job.state, job.target = "waiting_target", None
            await db.commit()
        await send_template(job.kind, "delivery_invalid", chat_id)
        return True
    try:
        state = await update_target(ident, text)
        if state == "confirm_target":
            async with Session() as db:
                job = await db.get(PluginJob, ident)
                target = job.target
                target_order = await db.get(Order, job.order_id)
            await send_template(
                job.kind,
                "delivery_confirm",
                chat_id,
                order_context(target_order, target=target, quantity=job.quantity),
            )
    except ValueError:
        await send_template(job.kind, "delivery_invalid", chat_id)
    return True


async def smm_command(ident, chat_id, command):
    async with Session() as db:
        job = await db.get(PluginJob, ident)
        if not job:
            return False
        payload = unpacked(job.payload)
        if payload.get("chat_id") != chat_id:
            return False
        if command[1].casefold() == "рефилл":
            if payload.get("refill_requested"):
                await send_template("auto-smm", "smm_refill_pending", chat_id)
                return True
            payload["refill_requested"] = True
            job.payload = packed(payload)
            await db.commit()
        try:
            action = "refill" if command[1].casefold() == "рефилл" else "status"
            result = await smm_request(payload["provider"], action, order=job.remote_id)
            key = "smm_refill" if result.get("refill") else "smm_status"
            await send_template(
                "auto-smm",
                key,
                chat_id,
                {
                    "status": result.get("status", "обрабатывается"),
                    "remains": result.get("remains", "—"),
                },
            )
        except (ValueError, httpx.HTTPError):
            await send_template("auto-smm", "smm_provider_error", chat_id)
    return True


async def success_text(job, order, payload, db=None):
    from .integrated_schemas import CONFIG_MODELS

    configured = payload["config"]["success_message"]
    custom = configured if configured != CONFIG_MODELS[job.kind]().success_message else None
    if job.kind == "steam-rent":
        return (
            custom.format(
                order_id=order.funpay_order_id,
                quantity=job.quantity,
                buyer=order.buyer_name,
                target=job.target or "",
                expires=payload.get("expires", ""),
            )
            if custom
            else ""
        )
    return await render(
        job.kind,
        "delivery_success",
        order_context(
            order,
            quantity=job.quantity,
            target=job.target or "",
            expires=payload.get("expires", ""),
        ),
        db=db,
        legacy=custom,
    )


async def finish_job(db, job, order, payload, *, message=None):
    if order.status not in {"paid", "closed"}:
        job.state = "attention"
        job.last_error = "Статус FunPay изменился после отправки поставщику"
        await db.commit()
        return
    text = message or await success_text(job, order, payload, db=db)
    job.state = "message_sending"
    payload["delivery_attempted"] = True
    job.payload = packed(payload)
    order.delivery_text = vault.encrypt(text)
    await db.commit()
    await accounts.send(payload["chat_id"], text)
    job.state = "completed"
    order.auto_delivered, order.delivery_state = True, "sent"
    job.next_check_at = None
    db.add(
        Activity(
            channel="orders",
            text=f"Заказ #{order.funpay_order_id} выполнен плагином",
            account_id=order.account_id,
            source_plugin=job.kind,
        )
    )
    await db.commit()
    await bus.publish("orders", {"event": "PLUGIN_COMPLETED", "order_id": order.id})


async def process_job(ident):
    async with job_locks.setdefault(ident, asyncio.Lock()):
        async with Session() as db:
            job = await db.get(PluginJob, ident)
            if not job or job.state not in {"ready", "submitted", "waiting_stock"}:
                return
            config = await plain_config(job.kind)
            if not config.get("enabled"):
                return
            order = await db.get(Order, job.order_id)
            if not order or (
                order.status != "paid"
                and not (job.state == "submitted" and order.status == "closed")
            ):
                job.state = "cancelled"
                await rental_verification.release_reserved(db, unpacked(job.payload))
                await db.commit()
                return
            account = await db.get(Account, order.account_id)
            if not account or not account.is_active:
                return
            payload = unpacked(job.payload)
            if job.state != "submitted" and await db.scalar(
                select(BlacklistEntry.id).where(BlacklistEntry.user_id == order.buyer_id)
            ):
                job.state, job.last_error = "attention", "Покупатель добавлен в чёрный список"
                await rental_verification.release_reserved(db, payload)
                job.payload = packed(payload)
                await db.commit()
                return
            if job.state != "submitted" and (
                job.remote_id or payload.get("delivery_attempted") or payload.get("extended_lease")
            ):
                job.state, job.last_error = (
                    "attention",
                    "Операция уже отправлялась. Проверьте результат перед ручным завершением.",
                )
                await db.commit()
                return
            config = payload["config"]
            try:
                if job.state == "submitted":
                    await check_status(db, job, order, payload)
                    return
                detail = await (await accounts.client(order.account_id)).order_details(
                    order.funpay_order_id
                )
                if (
                    detail.get("status") != "paid"
                    or int(detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
                ):
                    job.state = "cancelled"
                    await db.commit()
                    return
                if job.kind == "steam-rent":
                    from .steam_rentals import issue

                    await issue(db, job, order, payload)
                    return
                if not job.target:
                    return
                if job.kind == "auto-stars":
                    # Wallet V4R2 seqno is shared: never sign a second payment
                    # while the preceding payment still needs reconciliation.
                    async with stars_lock:
                        active = await db.scalar(
                            select(PluginJob.id)
                            .where(
                                PluginJob.kind == "auto-stars",
                                PluginJob.id != ident,
                                (PluginJob.state.in_(["preparing", "submitting", "submitted"]))
                                | (
                                    (PluginJob.remote_id.is_not(None))
                                    & PluginJob.state.in_(["uncertain", "attention"])
                                ),
                            )
                            .limit(1)
                        )
                        if active:
                            job.next_check_at = utcnow() + timedelta(seconds=30)
                            await db.commit()
                            return
                        job.state = "preparing"
                        await db.commit()
                    payload["signed"] = await prepare_stars(config, job.target, job.quantity)
                    job.payload = packed(payload)
                    job.remote_id = payload["signed"]["hash"]
                job.state = "submitting"
                await db.commit()
                if job.kind == "auto-smm":
                    response = await smm_request(
                        payload["provider"],
                        "add",
                        service=payload["service_id"],
                        link=job.target,
                        quantity=job.quantity,
                    )
                    if not str(response.get("order", "")).isdigit():
                        raise ProviderError("SMM API не вернул ID заказа")
                    job.remote_id = str(response["order"])
                elif job.kind == "steam-points":
                    response = await points_request(
                        "buy", config, steam_link=job.target, puan=job.quantity
                    )
                    job.remote_id = str(
                        response.get("order_id", response.get("steam64", "confirmed"))
                    )[:200]
                    await finish_job(db, job, order, payload)
                    return
                elif job.kind == "auto-stars":
                    await broadcast_stars(config, payload["signed"])
                job.state = "submitted"
                job.next_check_at = utcnow() + timedelta(seconds=config.get("check_interval", 30))
                await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                polling = job.state == "submitted"
                financial = job.state in {"submitting", "message_sending"}
                job.state = "submitted" if polling else "uncertain" if financial else "error"
                job.last_error = (
                    str(error)[:500]
                    if isinstance(error, (ProviderError, FunPayError))
                    else "Ошибка соединения или формата ответа; проверьте журнал и состояние заказа у поставщика"
                )
                job.next_check_at = utcnow() + timedelta(seconds=120) if polling else None
                await db.commit()
                await bus.log(
                    "orders",
                    f"{job.kind}: заказ #{order.funpay_order_id}: {job.last_error}",
                    order.account_id,
                    "error",
                    source_plugin=job.kind,
                )
                from .message_templates import notify

                if job.kind != "steam-rent":
                    await notify(
                        job.kind,
                        "delivery_admin_error",
                        order_context(order, reason=job.last_error),
                    )
                    if (
                        not payload.get("delivery_attempted")
                        and not polling
                        and not payload.get("error_message_sent")
                    ):
                        payload["error_message_sent"] = True
                        job.payload = packed(payload)
                        await db.commit()
                        await send_template(
                            job.kind,
                            "delivery_error",
                            payload["chat_id"],
                            order_context(order, reason=job.last_error),
                        )


async def check_status(db, job, order, payload):
    if job.kind == "auto-smm":
        response = await smm_request(payload["provider"], "status", order=job.remote_id)
        status = str(response.get("status", "")).casefold()
        payload["provider_status"] = status
        payload["remains"] = response.get("remains")
        job.payload = packed(payload)
        if status in {"completed", "done", "success"} and str(response.get("remains", "0")) == "0":
            await finish_job(db, job, order, payload)
            return
        if status in {"partial", "canceled", "cancelled", "failed", "error"}:
            job.state = "attention"
            job.last_error = "Поставщик выполнил заказ частично или отклонил его. Нужна проверка перед возвратом."
            job.next_check_at = None
            await db.commit()
            await telegram.notify("errors", f"SMM заказ #{order.funpay_order_id}: {status}")
            return
    elif job.kind == "auto-stars":
        if await stars_status(payload["config"], job.remote_id):
            await finish_job(
                db,
                job,
                order,
                payload,
                message=f"{await success_text(job, order, payload, db=db)}\n\nПлатёж за {job.quantity} Stars для @{job.target} подтверждён сетью TON. Ожидайте зачисления в Telegram.",
            )
            return
        created = (
            job.created_at.replace(tzinfo=utcnow().tzinfo)
            if not job.created_at.tzinfo
            else job.created_at
        )
        if utcnow() - created > timedelta(minutes=30):
            job.state = "attention"
            job.last_error = (
                "TON не подтвердил транзакцию за 30 минут. Повторная оплата не отправлялась."
            )
            await db.commit()
            return
    job.next_check_at = utcnow() + timedelta(seconds=payload["config"].get("check_interval", 30))
    await db.commit()


async def tick():
    from .integrated_schemas import KINDS

    enabled = [kind for kind in KINDS if (await stored_config(kind)).get("enabled")]
    if not enabled:
        return
    async with Session() as db:
        ids = (
            await db.scalars(
                select(PluginJob.id)
                .join(Order)
                .join(Account, Order.account_id == Account.id)
                .where(
                    Account.is_active.is_(True),
                    PluginJob.kind.in_(enabled),
                    (PluginJob.state == "ready")
                    | (
                        (PluginJob.state.in_(["submitted", "waiting_stock"]))
                        & (
                            (PluginJob.next_check_at <= utcnow())
                            | PluginJob.next_check_at.is_(None)
                        )
                    ),
                )
                .order_by(PluginJob.id)
                .limit(20)
            )
        ).all()
    for ident in ids:
        start_job(ident)


async def recover():
    async with Session() as db:
        jobs = (
            await db.scalars(
                select(PluginJob).where(
                    PluginJob.state.in_(["preparing", "submitting", "message_sending"])
                )
            )
        ).all()
        for job in jobs:
            job.state = (
                "submitted"
                if job.kind == "auto-stars" and job.remote_id and job.state == "submitting"
                else "error"
                if job.state == "preparing"
                else "uncertain"
            )
            job.last_error = "Приложение завершилось во время операции. Проверьте поставщика и чат перед повтором."
        await db.commit()
