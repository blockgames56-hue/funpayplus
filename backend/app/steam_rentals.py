"""Atomic inventory allocation and lease expiry with verified password rotation."""

import asyncio
from datetime import timedelta, timezone

from sqlalchemy import or_, select, update

from .db import Session
from .events import bus
from .funpay import FunPayError
from .integrated_config import packed, plain_config, unpacked
from .message_templates import notify, order_context, render, send
from .models import (
    Account,
    Activity,
    Chat,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalAccountBinding,
    RentalBonus,
    RentalBuyer,
    RentalLease,
    utcnow,
)
from .plugin_providers import ProviderError
from .security import vault
from .services import accounts, get_setting, save_setting
from .steam_security import guard_code, new_password, rotate_password

rotation_tasks: dict[int, asyncio.Task] = {}
inventory_lock = asyncio.Lock()
rotation_slots = asyncio.Semaphore(2)


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def issue(db, job, order, payload):
    from .integrated_jobs import finish_job, success_text

    if payload.get("is_extension"):
        async with inventory_lock:
            pairs = await buyer_leases(db, order.account_id, order.buyer_id)
            if str(payload.get("service_id", "")).isdigit():
                allowed = set(
                    (
                        await db.scalars(
                            select(RentalAccount.id).where(
                                account_matches(int(payload["service_id"]))
                            )
                        )
                    ).all()
                )
                pairs = [(lease, rented) for lease, rented in pairs if rented.id in allowed]
            leases = [lease for lease, rented in pairs]
            if len(leases) != 1:
                raise ProviderError(
                    "Для продления нужна одна однозначно определённая активная аренда покупателя"
                )
            lease = leases[0]
            lease.expires_at = aware(lease.expires_at) + timedelta(hours=job.quantity)
            lease.warned_at = None
            lease.warned_30_at = None
            buyer = await db.scalar(
                select(RentalBuyer).where(
                    RentalBuyer.account_id == order.account_id,
                    RentalBuyer.buyer_id == order.buyer_id,
                )
            )
            if not buyer:
                buyer = RentalBuyer(account_id=order.account_id, buyer_id=order.buyer_id)
                db.add(buyer)
            buyer.verified_until = utcnow() + timedelta(
                hours=payload["config"].get("verified_ttl_hours", 24)
            )
            payload["extended_lease"] = lease.id
            payload["expires"] = lease.expires_at.isoformat()
            job.payload = packed(payload)
            job.state = "message_sending"
            db.add(
                Activity(
                    channel="orders",
                    text=f"Аренда #{lease.id} продлена на {job.quantity} ч. по заказу #{order.funpay_order_id}",
                    account_id=order.account_id if order else lease.funpay_account_id,
                    source_plugin="steam-rent",
                )
            )
            await db.commit()
        rented = await db.get(RentalAccount, lease.account_id)
        creds = unpacked(rented.credentials)
        text = await render(
            "steam-rent",
            "rent_extended",
            order_context(
                order,
                login=creds["login"],
                hours=job.quantity,
                expires=display_time(lease.expires_at),
            ),
            db=db,
        )
        custom = await success_text(job, order, payload, db=db)
        await finish_job(
            db, job, order, payload, message=text + ("\n\n" + custom if custom else "")
        )
        return
    async with inventory_lock:
        if await db.scalar(select(RentalLease.id).where(RentalLease.job_id == job.id)):
            raise ProviderError(
                "Аккаунт для этого заказа уже выделен. Проверьте историю перед повторной выдачей."
            )
        held = payload.get("photo_reserved_account")
        candidate = (
            await db.scalar(
                select(RentalAccount.id).where(
                    RentalAccount.id == held,
                    RentalAccount.state == "reserved",
                    account_matches(job.binding_id),
                )
            )
            if held
            else None
        )
        reserved_state = "reserved" if candidate else "free"
        if not candidate:
            candidate = await db.scalar(
                select(RentalAccount.id)
                .where(account_matches(job.binding_id), RentalAccount.state == "free")
                .order_by(RentalAccount.id)
                .limit(1)
            )
        if not candidate:
            first = job.state != "waiting_stock"
            job.state = "waiting_stock"
            job.next_check_at = utcnow() + timedelta(seconds=60)
            await db.commit()
            if first:
                await send(
                    "steam-rent", "rent_no_accounts", payload["chat_id"], order_context(order)
                )
                await notify(
                    "steam-rent", "rent_admin_stock", order_context(order), channel="stock"
                )
            return
        reserved = await db.execute(
            update(RentalAccount)
            .where(RentalAccount.id == candidate, RentalAccount.state == reserved_state)
            .values(state="leased")
        )
        if reserved.rowcount != 1:
            raise ProviderError("Аккаунт уже выдан другому покупателю")
        lease = RentalLease(
            job_id=job.id,
            account_id=candidate,
            expires_at=utcnow() + timedelta(hours=job.quantity),
            chat_id=payload["chat_id"],
            buyer_id=order.buyer_id,
            buyer_name=order.buyer_name,
            funpay_account_id=order.account_id,
        )
        db.add(lease)
        account = await db.get(RentalAccount, candidate)
        credentials = unpacked(account.credentials)
        payload["expires"] = lease.expires_at.isoformat()
        job.payload = packed(payload)
        job.state = "message_sending"
        await db.commit()
    text = await render(
        "steam-rent",
        "rent_credentials",
        order_context(
            order,
            login=credentials["login"],
            password=credentials["password"],
            game=account.game or account.label,
            expires=display_time(lease.expires_at),
        ),
        db=db,
    )
    custom = await success_text(job, order, payload, db=db)
    await finish_job(db, job, order, payload, message=text + ("\n\n" + custom if custom else ""))


def account_matches(binding_id):
    return or_(
        RentalAccount.binding_id == binding_id,
        RentalAccount.id.in_(
            select(RentalAccountBinding.account_id).where(
                RentalAccountBinding.binding_id == binding_id
            )
        ),
    )


def display_time(value):
    return aware(value).strftime("%d.%m.%Y %H:%M UTC")


async def lease_context(db, lease):
    job = await db.get(PluginJob, lease.job_id) if lease.job_id else None
    order = await db.get(Order, job.order_id) if job else None
    chat_id = lease.chat_id or (unpacked(job.payload).get("chat_id") if job else None)
    return job, order, chat_id


async def buyer_leases(db, account_id, buyer_id):
    return (
        await db.execute(
            select(RentalLease, RentalAccount)
            .join(RentalAccount)
            .outerjoin(PluginJob, RentalLease.job_id == PluginJob.id)
            .outerjoin(Order, PluginJob.order_id == Order.id)
            .where(
                or_(RentalLease.funpay_account_id == account_id, Order.account_id == account_id),
                or_(RentalLease.buyer_id == buyer_id, Order.buyer_id == buyer_id),
                RentalLease.state == "active",
                RentalLease.expires_at > utcnow(),
            )
            .order_by(RentalLease.expires_at)
        )
    ).all()


async def handle_command(chat_id, account_id, message):
    import re

    match = re.fullmatch(
        r"(!код|!время|!аренда|!продлить|!аккаунты|!помощь)(?:\(([^()]+)\)|\s+(.+))?",
        message.get("text", "").strip(),
        re.I,
    )
    if not match:
        return False
    config = await plain_config("steam-rent")
    if not config.get("enabled"):
        return False
    command, argument = match[1].casefold(), (match[2] or match[3] or "").strip()
    async with Session() as db:
        chat = await db.get(Chat, chat_id)
        if (
            not chat
            or chat.account_id != account_id
            or chat.interlocutor_id != message.get("sender_id")
        ):
            return False
        from .models import BlacklistEntry

        if await db.scalar(
            select(BlacklistEntry.id).where(BlacklistEntry.user_id == chat.interlocutor_id)
        ):
            reply = await render("steam-rent", "rent_blocked", db=db)
        elif command == "!помощь":
            await notify(
                "steam-rent",
                "rent_admin_help",
                {"buyer": chat.interlocutor_name, "chat_id": chat.id},
                channel="orders",
            )
            reply = await render("steam-rent", "rent_help_sent", db=db)
        elif command == "!аренда":
            reply = await render("steam-rent", "rent_help", db=db)
        elif command == "!аккаунты":
            rows = (
                await db.scalars(
                    select(RentalAccount)
                    .join(PluginBinding, account_matches(PluginBinding.id))
                    .join(Lot)
                    .where(
                        Lot.account_id == account_id,
                        RentalAccount.state == "free",
                        PluginBinding.is_active.is_(True),
                    )
                    .limit(50)
                )
            ).all()
            listing = (
                "\n".join(f"• {r.game or r.label}" for r in rows)
                or "Сейчас всё занято. Напишите продавцу."
            )
            reply = await render("steam-rent", "rent_free_accounts", {"accounts": listing}, db=db)
        else:
            rows = await buyer_leases(db, account_id, chat.interlocutor_id)
            if argument:
                rows = [
                    (lease, account)
                    for lease, account in rows
                    if unpacked(account.credentials)["login"].casefold() == argument.casefold()
                ]
            listing = "\n".join(
                f"• {unpacked(a.credentials)['login']} · {a.game or a.label} · до {display_time(lease.expires_at)}"
                for lease, a in rows
            )
            if not rows:
                reply = await render("steam-rent", "rent_no_active", db=db)
            elif command == "!код":
                if len(rows) != 1:
                    reply = await render(
                        "steam-rent", "rent_select_login", {"accounts": listing}, db=db
                    )
                else:
                    from .integrated_config import pool

                    creds = unpacked(rows[0][1].credentials)
                    result = await pool.request(
                        "POST", "https://api.steampowered.com/ITwoFactorService/QueryTime/v0001"
                    )
                    result.raise_for_status()
                    timestamp = int(result.json()["response"]["server_time"])
                    reply = await render(
                        "steam-rent",
                        "rent_guard",
                        {
                            "login": creds["login"],
                            "code": guard_code(creds["shared_secret"], timestamp),
                        },
                        db=db,
                    )
            elif command == "!продлить":
                if len(rows) != 1:
                    reply = await render(
                        "steam-rent", "rent_select_login", {"accounts": listing}, db=db
                    )
                else:
                    rented = rows[0][1]
                    linked = [rented.binding_id] + list(
                        (
                            await db.scalars(
                                select(RentalAccountBinding.binding_id).where(
                                    RentalAccountBinding.account_id == rented.id
                                )
                            )
                        ).all()
                    )
                    extensions = (
                        await db.execute(
                            select(PluginBinding, Lot)
                            .join(Lot)
                            .where(
                                PluginBinding.kind == "steam-rent",
                                PluginBinding.is_extension.is_(True),
                                PluginBinding.is_active.is_(True),
                                Lot.account_id == account_id,
                            )
                        )
                    ).all()
                    choices = [
                        (b, lot)
                        for b, lot in extensions
                        if b.service_id.isdigit() and int(b.service_id) in linked
                    ]
                    if not choices and len(extensions) == 1 and not extensions[0][0].service_id:
                        choices = extensions
                    reply = await render(
                        "steam-rent",
                        "rent_extension_link" if len(choices) == 1 else "rent_extension_missing",
                        {
                            "login": unpacked(rented.credentials)["login"],
                            "url": f"https://funpay.com/lots/offer?id={choices[0][1].funpay_lot_id}"
                            if len(choices) == 1
                            else "",
                        },
                        db=db,
                    )
            else:
                reply = await render("steam-rent", "rent_time", {"accounts": listing}, db=db)
    await accounts.send(chat_id, reply)
    return True


async def rotate(lease_id, *, force=False):
    try:
        async with rotation_slots:
            await _rotate(lease_id, force=force)
    finally:
        rotation_tasks.pop(lease_id, None)


async def _rotate(lease_id, *, force=False):
    try:
        async with Session() as db:
            async with inventory_lock:
                lease = await db.get(RentalLease, lease_id)
                if not lease or lease.state != "active":
                    return
                account = await db.get(RentalAccount, lease.account_id)
                job, order, chat_id = await lease_context(db, lease)
                if (
                    not force
                    and aware(lease.expires_at) > utcnow()
                    and (not order or order.status != "refunded")
                ):
                    return
                credentials = unpacked(account.credentials)
                password = new_password()
                account.pending_password = vault.encrypt(password)
                account.state, lease.state = "rotating", "rotating"
                await db.commit()
            try:
                await rotate_password(credentials, password)
            except Exception as error:
                reason = " ".join(str(error).split())[:300] or type(error).__name__
                for secret in (
                    credentials.get("login"),
                    credentials.get("password"),
                    credentials.get("shared_secret"),
                    credentials.get("identity_secret"),
                    password,
                ):
                    value = str(secret or "")
                    if len(value) >= 4:
                        reason = reason.replace(value, "***")
                account.state, lease.state = "quarantine", "attention"
                account.last_error = (
                    f"Смена пароля не подтверждена Steam: {reason}. "
                    "Аккаунт заблокирован для повторной выдачи."
                )
                db.add(
                    Activity(
                        channel="orders",
                        text=(
                            f"Аренда #{lease.id}: смена пароля не подтверждена, "
                            f"аккаунт в карантине. Причина: {reason}"
                        ),
                        account_id=order.account_id if order else lease.funpay_account_id,
                        level="error",
                        source_plugin="steam-rent",
                    )
                )
                await db.commit()
                await notify(
                    "steam-rent",
                    "rent_admin_rotation",
                    {"lease_id": lease.id, "label": account.label},
                )
                return
            credentials["password"] = password
            account.credentials = packed(credentials)
            account.pending_password = None
            account.state, account.last_error, lease.state = "free", None, "finished"
            lease.finished_at = utcnow()
            db.add(
                Activity(
                    channel="orders",
                    text=f"Аренда #{lease.id} завершена: пароль изменён и проверен, аккаунт возвращён в пул",
                    account_id=order.account_id if order else lease.funpay_account_id,
                    source_plugin="steam-rent",
                )
            )
            await db.commit()
            try:
                if chat_id:
                    await send(
                        "steam-rent", "rent_finished", chat_id, {"login": credentials["login"]}
                    )
            except FunPayError:
                await bus.log(
                    "orders",
                    f"Аренда #{lease.id}: проверьте отправку уведомления об окончании",
                    order.account_id if order else lease.funpay_account_id,
                    "error",
                    source_plugin="steam-rent",
                )
    finally:
        rotation_tasks.pop(lease_id, None)


async def tick():
    from . import rental_verification

    config = await plain_config("steam-rent")
    await rental_verification.tick()
    async with Session() as db:
        rows = (
            await db.scalars(
                select(RentalLease)
                .where(RentalLease.state == "active")
                .order_by(RentalLease.expires_at)
            )
        ).all()
        for lease in rows:
            job, order, chat_id = await lease_context(db, lease)
            if aware(lease.expires_at) <= utcnow() or (order and order.status == "refunded"):
                if lease.id not in rotation_tasks:
                    task = asyncio.create_task(rotate(lease.id), name=f"rental-rotate-{lease.id}")
                    rotation_tasks[lease.id] = task
                    task.add_done_callback(
                        lambda done: None if done.cancelled() else done.exception()
                    )
                continue
            rental = await db.get(RentalAccount, lease.account_id)
            values = {
                "login": unpacked(rental.credentials)["login"],
                "game": rental.game or rental.label,
            }
            if (
                config.get("warn_30_minutes", True)
                and not lease.warned_30_at
                and utcnow() + timedelta(minutes=10)
                < aware(lease.expires_at)
                <= utcnow() + timedelta(minutes=30)
            ):
                lease.warned_30_at = utcnow()
                await db.commit()
                if chat_id:
                    try:
                        await send("steam-rent", "rent_warning_30m", chat_id, values)
                    except FunPayError:
                        pass
            minutes = config.get("warn_minutes", 10)
            if (
                minutes
                and not lease.warned_at
                and aware(lease.expires_at) <= utcnow() + timedelta(minutes=minutes)
            ):
                lease.warned_at = utcnow()
                await db.commit()
                if chat_id:
                    try:
                        await send(
                            "steam-rent",
                            "rent_warning_10m" if minutes == 10 else "rent_warning",
                            chat_id,
                            {**values, "minutes": minutes},
                        )
                    except FunPayError:
                        pass
    if config.get("enabled"):
        if config.get("review_bonus_minutes"):
            await review_bonuses(config)
        if config.get("auto_hide_empty") or config.get("keep_extension_active"):
            await update_stock_visibility()


async def review_bonuses(config, *, account_id=None, buyer_id=None, details=None):
    # A rotating cursor prevents repeatedly checking only the oldest reviews.
    cursor = await get_setting("rent:review_cursor", 0)
    async with Session() as db:
        query = (
            select(Order)
            .join(PluginJob, PluginJob.order_id == Order.id)
            .join(Account, Order.account_id == Account.id)
            .where(
                PluginJob.kind == "steam-rent",
                PluginJob.state == "completed",
                Order.status == "closed",
                Account.is_active.is_(True),
                ~Order.id.in_(select(RentalBonus.order_id)),
            )
        )
        targeted = account_id is not None and buyer_id is not None
        if targeted:
            query = query.where(Order.account_id == account_id, Order.buyer_id == buyer_id)
        else:
            query = query.where(Order.id > cursor)
        orders = (
            await db.scalars(query.order_by(Order.id.desc() if targeted else Order.id).limit(5))
        ).all()
        if not orders:
            if not targeted:
                await save_setting("rent:review_cursor", 0)
            return
        for order in orders:
            try:
                detail = (details or {}).get(order.id)
                if detail is None:
                    detail = await (await accounts.client(order.account_id)).order_details(
                        order.funpay_order_id
                    )
                review = detail.get("review") or {}
                if review.get("rating") != 5 or review.get("hidden"):
                    continue
                async with inventory_lock:
                    rows = await buyer_leases(db, order.account_id, order.buyer_id)
                    if not rows or await db.scalar(
                        select(RentalBonus.id).where(RentalBonus.order_id == order.id)
                    ):
                        continue
                    lease, rented = rows[0]
                    # Existing installations may already have awarded this order.
                    job = await db.scalar(select(PluginJob).where(PluginJob.order_id == order.id))
                    old = await db.scalar(select(RentalLease).where(RentalLease.job_id == job.id))
                    if old and old.bonus_awarded:
                        db.add(RentalBonus(order_id=order.id, lease_id=old.id, minutes=0))
                        await db.commit()
                        continue
                    lease.expires_at = aware(lease.expires_at) + timedelta(
                        minutes=config["review_bonus_minutes"]
                    )
                    lease.warned_at = lease.warned_30_at = None
                    if old:
                        old.bonus_awarded = True
                    db.add(
                        RentalBonus(
                            order_id=order.id,
                            lease_id=lease.id,
                            minutes=config["review_bonus_minutes"],
                        )
                    )
                    buyer = await db.scalar(
                        select(RentalBuyer).where(
                            RentalBuyer.account_id == order.account_id,
                            RentalBuyer.buyer_id == order.buyer_id,
                        )
                    )
                    if not buyer:
                        buyer = RentalBuyer(account_id=order.account_id, buyer_id=order.buyer_id)
                        db.add(buyer)
                    buyer.verified_until = utcnow() + timedelta(
                        hours=config.get("verified_ttl_hours", 24)
                    )
                    await db.commit()
                _, _, chat_id = await lease_context(db, lease)
                if chat_id:
                    await send(
                        "steam-rent",
                        "rent_review_bonus",
                        chat_id,
                        order_context(
                            order,
                            login=unpacked(rented.credentials)["login"],
                            minutes=config["review_bonus_minutes"],
                            expires=display_time(lease.expires_at),
                        ),
                    )
            except FunPayError:
                pass
        if not targeted:
            await save_setting("rent:review_cursor", orders[-1].id)


async def on_review_message(chat_id, account_id):
    """Verify only this buyer's orders immediately when FunPay announces a review."""
    config = await plain_config("steam-rent")
    if not config.get("enabled") or not config.get("review_bonus_minutes"):
        return
    details = {}
    async with Session() as db:
        chat = await db.get(Chat, chat_id)
        if not chat or chat.account_id != account_id:
            return
        orders = (
            await db.scalars(
                select(Order)
                .join(PluginJob, PluginJob.order_id == Order.id)
                .where(
                    Order.account_id == account_id,
                    Order.buyer_id == chat.interlocutor_id,
                    Order.status.in_(["paid", "closed"]),
                    PluginJob.kind == "steam-rent",
                    PluginJob.state == "completed",
                    ~Order.id.in_(select(RentalBonus.order_id)),
                )
                .order_by(Order.id.desc())
                .limit(5)
            )
        ).all()
        for order in orders:
            detail = await (await accounts.client(account_id)).order_details(order.funpay_order_id)
            if (
                detail.get("status") == "closed"
                and int(detail.get("buyer", {}).get("user_id", 0)) == chat.interlocutor_id
            ):
                order.status = "closed"
                details[order.id] = detail
        await db.commit()
        buyer_id = chat.interlocutor_id
    if details:
        await review_bonuses(config, account_id=account_id, buyer_id=buyer_id, details=details)


async def update_stock_visibility():
    config = await plain_config("steam-rent")
    key = "integrated:hidden:steam-rent"
    hidden = set(await get_setting(key, []))
    async with Session() as db:
        bindings = (
            await db.execute(
                select(PluginBinding, Lot)
                .join(Lot)
                .join(Account, Lot.account_id == Account.id)
                .where(
                    PluginBinding.kind == "steam-rent",
                    PluginBinding.is_active.is_(True),
                    Account.is_active.is_(True),
                )
            )
        ).all()
        for binding, lot in bindings:
            if binding.is_extension:
                if config.get("keep_extension_active") and not lot.is_active:
                    try:
                        await (await accounts.client(lot.account_id)).update_lot(
                            lot.funpay_lot_id, active=True
                        )
                        lot.is_active = True
                        await db.commit()
                    except FunPayError:
                        pass
                continue
            if not config.get("auto_hide_empty"):
                continue
            available = bool(
                await db.scalar(
                    select(RentalAccount.id)
                    .where(account_matches(binding.id), RentalAccount.state == "free")
                    .limit(1)
                )
            )
            if (not available and lot.is_active) or (
                available and not lot.is_active and lot.id in hidden
            ):
                try:
                    await (await accounts.client(lot.account_id)).update_lot(
                        lot.funpay_lot_id, active=available
                    )
                    lot.is_active = available
                    await db.commit()
                    hidden.discard(lot.id) if available else hidden.add(lot.id)
                    await save_setting(key, sorted(hidden))
                except FunPayError:
                    await bus.log(
                        "lots",
                        f"Аренда: не удалось обновить доступность лота #{lot.funpay_lot_id}",
                        lot.account_id,
                        "error",
                        source_plugin="steam-rent",
                    )


async def recover():
    async with Session() as db:
        rows = (
            await db.execute(
                select(RentalLease, RentalAccount)
                .join(RentalAccount)
                .where(RentalLease.state == "rotating")
            )
        ).all()
        for lease, account in rows:
            account.state, lease.state = "quarantine", "attention"
            account.last_error = (
                "Перезапуск во время смены пароля. Проверьте новый пароль перед возвратом в пул."
            )
        verifying = (
            await db.scalars(
                select(RentalAccount).where(RentalAccount.state.in_(["verifying", "rotating"]))
            )
        ).all()
        for account in verifying:
            account.state = "quarantine"
            account.last_error = (
                "Перезапуск во время проверки данных. Проверьте актуальный пароль перед выдачей."
            )
        await db.commit()


async def stop():
    from . import rental_verification

    await rental_verification.stop()
    tasks = list(rotation_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
