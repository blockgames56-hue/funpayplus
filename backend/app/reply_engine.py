"""Durable, account-aware replies. Network sends are claimed before dispatch."""

import asyncio
import contextlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import reply_config as rc
from .blacklist import is_blocked
from .db import Session
from .events import bus
from .integrated_config import packed, unpacked
from .message_templates import MessageFormatter
from .models import Account, Chat, ChatMessage, Order, ReplyEvent, Settings, utcnow
from .services import accounts, get_setting, save_setting

lock = asyncio.Lock()
observe_lock = asyncio.Lock()
send_lock = asyncio.Lock()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def log(text, account_id=None, level="info"):
    with contextlib.suppress(Exception):
        await bus.log("chats", text, account_id, level, source_plugin="responder")


async def configured():
    value = await get_setting("responder:v2", {})
    return value.get("version") == 2


def scoped(cfg, account_id):
    return cfg["account_scope"] == "all" or account_id in cfg["account_ids"]


async def context(db, chat, order=None, review=None):
    app = await get_setting("app", {})
    now = utcnow().astimezone(ZoneInfo(app.get("timezone", "Europe/Tallinn")))
    account = await db.get(Account, chat.account_id)
    if order is None and chat.current_order_id:
        order = await db.scalar(
            select(Order).where(
                Order.account_id == chat.account_id,
                Order.funpay_order_id == chat.current_order_id,
                Order.buyer_id == chat.interlocutor_id,
            )
        )
    return {
        "date": now.strftime("%d.%m.%Y %H:%M"),
        "date_only": now.strftime("%d.%m.%Y"),
        "time": now.strftime("%H:%M"),
        "buyer_name": chat.interlocutor_name,
        "buyer": chat.interlocutor_name,
        "buyer_id": chat.interlocutor_id,
        "order_id": order.funpay_order_id if order else "",
        "lot_name": order.description if order else "",
        "order_title": order.description if order else "",
        "lot_price": str(order.sum) if order else "",
        "order_price": str(order.sum) if order else "",
        "lot_amount": "",
        "order_link": f"https://funpay.com/orders/{quote(order.funpay_order_id, safe='')}/"
        if order
        else "https://funpay.com/orders/trade",
        "chat_id": chat.funpay_chat_id,
        "seller_name": account.username or account.title,
        "stars": (review or {}).get("rating", ""),
        "review_text": (review or {}).get("text", ""),
    }


async def enqueue(db, chat, kind, key, text, *, order=None, delay=0, extra=None):
    if not text.strip() or await db.scalar(
        select(ReplyEvent.id).where(ReplyEvent.event_key == key)
    ):
        return False
    row = ReplyEvent(
        account_id=chat.account_id,
        chat_id=chat.id,
        order_id=order.id if order else None,
        kind=kind,
        event_key=key,
        payload=packed({"text": text, **(extra or {})}),
        due_at=utcnow() + timedelta(seconds=delay),
    )
    db.add(row)
    await db.commit()
    return True


def matches(rule, text, *, command=False, prefix="!"):
    value = " ".join(text.split())
    if not rule.get("case_sensitive", False):
        value = value.casefold()
    for trigger in rule["triggers"]:
        trigger = " ".join(trigger.split())
        if command and trigger.startswith(prefix):
            trigger = trigger[len(prefix) :]
        if not rule.get("case_sensitive", False):
            trigger = trigger.casefold()
        if not trigger:
            continue
        if command:
            if value == trigger or (
                (rule.get("allow_arguments") or rule["match"] == "starts_with")
                and value.startswith(trigger + " ")
            ):
                return True
            continue
        if (
            value == trigger
            if rule["match"] == "exact"
            else value.startswith(trigger)
            if rule["match"] == "starts_with"
            else trigger in value
        ):
            return True
    return False


def rule_active(rule, now):
    """Return whether the rule's local weekly window includes ``now``.

    Rules use the application timezone.  An overnight period belongs to the
    weekday on which it started: Monday 22:00--06:00 also permits Tuesday
    01:00 when Monday is selected.
    """
    days = set(rule.get("active_days", range(7)))
    if not days:
        return False
    start, end = rule.get("active_from"), rule.get("active_until")
    if not start or not end:
        return now.weekday() in days
    minute = now.hour * 60 + now.minute
    start_minute = int(start[:2]) * 60 + int(start[3:])
    end_minute = int(end[:2]) * 60 + int(end[3:])
    if start_minute <= end_minute:
        return now.weekday() in days and start_minute <= minute <= end_minute
    if minute >= start_minute:
        return now.weekday() in days
    if minute <= end_minute:
        return (now.weekday() - 1) % 7 in days
    return False


async def cooldown_active(db, chat, kind, rule):
    """Apply a rule's optional chat, account and buyer cooldowns.

    A cancelled durable event still counts, matching the historical per-chat
    behaviour: a user action must not become a resend just because the queue
    was paused after it was accepted.
    """
    threshold = utcnow()
    rule_id = rule["id"]
    escaped_rule = rule_id.replace("_", r"\_")
    prefix = f"{kind}:%:{escaped_rule}:%"
    base = (
        select(ReplyEvent.id)
        .where(
            ReplyEvent.kind == kind,
            ReplyEvent.event_key.like(prefix, escape="\\"),
        )
        .limit(1)
    )
    for seconds, condition in (
        (
            rule["cooldown"],
            ReplyEvent.chat_id == chat.id,
        ),
        (
            rule.get("account_cooldown", 0),
            ReplyEvent.account_id == chat.account_id,
        ),
        (rule.get("buyer_cooldown", 0), None),
    ):
        if not seconds:
            continue
        statement = base.where(ReplyEvent.created_at > threshold - timedelta(seconds=seconds))
        if condition is None:
            statement = statement.join(Chat, ReplyEvent.chat_id == Chat.id).where(
                ReplyEvent.account_id == chat.account_id,
                Chat.interlocutor_id == chat.interlocutor_id,
            )
        else:
            statement = statement.where(condition)
        if await db.scalar(statement):
            return True
    return False


async def on_message(chat_id, message):
    if message.get("is_my") or not message.get("sender_id"):
        return
    cfg = await rc.config()
    master = await get_setting("plugins", {})
    if not master.get("responder"):
        return
    async with lock, Session() as db:
        chat = await db.get(Chat, chat_id)
        account = await db.get(Account, chat.account_id) if chat else None
        if (
            not chat
            or not account
            or not account.is_active
            or chat.interlocutor_id != message["sender_id"]
            or not scoped(cfg, chat.account_id)
        ):
            return
        if await is_blocked(
            db, account_id=chat.account_id, user_id=chat.interlocutor_id, chat_id=chat.id
        ):
            return
        handoff = await get_setting(f"reply:handoff:{chat_id}", {})
        if handoff.get("paused"):
            return
        values = await context(db, chat)
        app = await get_setting("app", {})
        local_now = utcnow().astimezone(ZoneInfo(app.get("timezone", "Europe/Tallinn")))
        text = message.get("text", "")[:20000]
        local_id = message.get("local_id")
        event_id = local_id or message.get("funpay_message_id") or message.get("id")
        if event_id is None:
            return
        if (
            cfg["commands_enabled"]
            and cfg["refunds_enabled"]
            and re.fullmatch(
                re.escape(cfg["prefix"]) + r"возврат(?:\s+#[A-Za-z0-9]+)?", text.strip(), re.I
            )
        ):
            from .reply_requests import create_request

            notice = await create_request(db, chat, text)
            await enqueue(
                db,
                chat,
                "command",
                f"refund-notice:{chat.id}:{event_id}",
                notice,
                extra={"trigger": local_id},
            )
            return
        groups = []
        if cfg["commands_enabled"] and text.strip().startswith(cfg["prefix"]):
            clean = text.strip()[len(cfg["prefix"]) :]
            groups.append(("command", cfg["commands"], clean))
        if cfg["keywords_enabled"]:
            groups.append(("keyword", cfg["keywords"], text))
        for kind, rules, value in groups:
            for rule in sorted(rules, key=lambda r: -r.get("priority", 0)):
                if (
                    not rule["enabled"]
                    or not rule_active(rule, local_now)
                    or not matches(rule, value, command=kind == "command", prefix=cfg["prefix"])
                ):
                    continue
                if await cooldown_active(db, chat, kind, rule):
                    return
                values.update(
                    message=text,
                    command=text.strip().split()[0] if text.strip() else "",
                    args=value.split(maxsplit=1)[1] if len(value.split(maxsplit=1)) > 1 else "",
                    buyer_username=values["buyer_name"],
                    seller_username=values["seller_name"],
                )
                reply = MessageFormatter.format(rc.variants(rule["reply"]), values)
                await enqueue(
                    db,
                    chat,
                    kind,
                    f"{kind}:{chat.id}:{rule['id']}:{event_id}",
                    reply,
                    delay=0,
                    extra={"trigger": local_id},
                )
                return
        if cfg["greeting_enabled"] and cfg["greeting_text"].strip():
            key = f"greeting:{chat.account_id}:{chat.interlocutor_id}"
            if cfg["greeting_mode"] == "daily":
                recent = await db.scalar(
                    select(ReplyEvent.id)
                    .where(
                        ReplyEvent.account_id == chat.account_id,
                        ReplyEvent.kind == "greeting",
                        ReplyEvent.chat_id == chat.id,
                        ReplyEvent.created_at > utcnow() - timedelta(hours=24),
                    )
                    .limit(1)
                )
                key += f":{int(utcnow().timestamp()) // 86400}"
            else:
                recent = await db.scalar(select(ReplyEvent.id).where(ReplyEvent.event_key == key))
            if not recent:
                reply = MessageFormatter.format(rc.variants(cfg["greeting_text"]), values)
                await enqueue(
                    db,
                    chat,
                    "greeting",
                    key,
                    reply,
                    delay=cfg["greeting_delay"],
                    extra={"trigger": local_id},
                )
                return
        if cfg["ai_enabled"]:
            # The AI task is processed in the queue, never inside the poller's event handler.
            await enqueue(
                db,
                chat,
                "ai",
                f"ai:{chat.id}:{event_id}",
                "AI",
                extra={"question": text[:2000], "trigger": local_id},
            )


async def active_since(account_id):
    values = []
    for key in ("responder:v2", f"account-automation:{account_id}"):
        value = await get_setting(key, {})
        if value.get("enabled_at"):
            values.append(aware(datetime.fromisoformat(value["enabled_at"])))
    return min(values) if values else None


async def observe_orders(*, account_id=None, buyer_id=None):
    cfg = await rc.config()
    master = await get_setting("plugins", {})
    async with Session() as db:
        active = (await db.scalars(select(Account.id).where(Account.is_active.is_(True)))).all()
    for aid in active:
        if account_id is not None and aid != account_id:
            continue
        review_reads = 0
        since = await active_since(aid)
        if not since:
            continue
        policy = await rc.policy(aid)
        async with Session() as db:
            rows = (
                await db.scalars(
                    select(Order)
                    .where(
                        Order.account_id == aid,
                        Order.date_source == "observed",
                        Order.created_at >= since,
                        Order.status.in_(["paid", "closed"]),
                        *([Order.buyer_id == buyer_id] if buyer_id is not None else []),
                    )
                    .order_by(Order.id.desc())
                    .limit(5 if buyer_id is not None else 200)
                )
            ).all()
            for order in rows:
                chat = await db.scalar(
                    select(Chat).where(
                        Chat.account_id == aid, Chat.interlocutor_id == order.buyer_id
                    )
                )
                if not chat:
                    continue
                if await is_blocked(db, account_id=aid, user_id=order.buyer_id, chat_id=chat.id):
                    continue
                targeted_detail = None
                if buyer_id is not None:
                    try:
                        targeted_detail = await (await accounts.client(aid)).order_details(
                            order.funpay_order_id
                        )
                    except Exception:
                        continue
                    if (
                        targeted_detail.get("status") != "closed"
                        or int(targeted_detail.get("buyer", {}).get("user_id", 0)) != order.buyer_id
                    ):
                        continue
                    order.status = "closed"
                    await db.commit()
                values = await context(db, chat, order)
                jobs = []
                if order.status == "paid":
                    if cfg["paid_enabled"] and master.get("responder") and scoped(cfg, aid):
                        jobs.append(
                            ("paid", cfg["paid_text"], cfg["paid_delay"], f"paid:{order.id}")
                        )
                    if policy["confirmation_reminder"]:
                        # Rentals and gift queues have their own timing and must not be rushed.
                        from .models import KosellRental, PluginJob

                        excluded = await db.scalar(
                            select(PluginJob.id).where(PluginJob.order_id == order.id)
                        ) or await db.scalar(
                            select(KosellRental.id).where(KosellRental.order_id == order.id)
                        )
                        from .models import FulfillmentTask

                        excluded = excluded or await db.scalar(
                            select(FulfillmentTask.id).where(FulfillmentTask.order_id == order.id)
                        )
                        if not excluded:
                            for n in range(policy["reminder_count"]):
                                due = aware(order.created_at) + timedelta(
                                    minutes=policy["reminder_minutes"] * (n + 1)
                                )
                                if utcnow() - timedelta(minutes=15) <= due <= utcnow():
                                    jobs.append(
                                        (
                                            "confirmation_reminder",
                                            policy["reminder_text"],
                                            0,
                                            f"confirm-reminder:{order.id}:{n}",
                                        )
                                    )
                elif order.status == "closed":
                    if policy["confirmation"]:
                        await enqueue(
                            db,
                            chat,
                            "confirmation",
                            f"confirmation:{order.id}",
                            MessageFormatter.format(
                                rc.variants(policy["confirmation_text"]), values
                            ),
                            order=order,
                            delay=0,
                        )
                    if policy["review_reply"] or policy["review_chat"] or policy["review_reminder"]:
                        inspected = await db.scalar(
                            select(ReplyEvent).where(
                                ReplyEvent.event_key == f"review-inspect:{order.id}"
                            )
                        )
                        if (
                            buyer_id is None
                            and inspected
                            and aware(inspected.created_at) > utcnow() - timedelta(minutes=2)
                        ):
                            continue
                        if review_reads >= 10:
                            continue
                        review_reads += 1
                        try:
                            detail = targeted_detail or await (
                                await accounts.client(aid)
                            ).order_details(order.funpay_order_id)
                        except Exception:
                            continue
                        review = detail.get("review") or {}
                        if detail.get("status") != "closed":
                            continue
                        if review.get("rating") in range(1, 6) and not review.get("hidden"):
                            values = await context(db, chat, order, review)
                            if (
                                master.get("auto_thanks")
                                and policy["review_reply"]
                                and scoped(cfg, aid)
                                and (
                                    not cfg["skip_empty_reviews"]
                                    or str(review.get("text") or "").strip()
                                )
                            ):
                                template = cfg["review_texts"].get(str(review["rating"]), "")
                                if template.strip() and not review.get("reply"):
                                    jobs.append(
                                        (
                                            "review"
                                            if cfg["review_target"] == "review"
                                            else "review_chat",
                                            template,
                                            cfg["review_delay"],
                                            f"review:{order.id}",
                                        )
                                    )
                            if policy["review_chat"]:
                                jobs.append(
                                    (
                                        "review_chat",
                                        policy["review_chat_text"],
                                        0,
                                        f"review-chat:{order.id}",
                                    )
                                )
                        elif (
                            policy["review_reminder"]
                            and not review.get("rating")
                            and aware(order.created_at) + timedelta(hours=1) <= utcnow()
                        ):
                            jobs.append(
                                (
                                    "review_reminder",
                                    policy["review_reminder_text"],
                                    0,
                                    f"review-reminder:{order.id}",
                                )
                            )
                        if inspected:
                            inspected.created_at = utcnow()
                        else:
                            db.add(
                                ReplyEvent(
                                    account_id=aid,
                                    chat_id=chat.id,
                                    order_id=order.id,
                                    event_key=f"review-inspect:{order.id}",
                                    kind="review_inspect",
                                    state="observed",
                                    payload=packed({}),
                                    due_at=utcnow(),
                                )
                            )
                        await db.commit()
                for kind, text, delay, key in jobs:
                    if text.strip():
                        chosen = rc.variants(text)
                        await enqueue(
                            db,
                            chat,
                            kind,
                            key,
                            MessageFormatter.format(chosen, values),
                            order=order,
                            delay=delay,
                            extra={"template": chosen},
                        )


async def flush():
    async with send_lock, Session() as db:
        cfg = await rc.config()
        master = await get_setting("plugins", {})
        rows = (
            await db.scalars(
                select(ReplyEvent)
                .join(Account)
                .where(
                    ReplyEvent.state == "pending",
                    ReplyEvent.due_at <= utcnow(),
                    Account.is_active.is_(True),
                )
                .order_by(ReplyEvent.id)
                .limit(10)
            )
        ).all()
        for event in rows:
            cfg = await rc.config()
            master = await get_setting("plugins", {})
            chat = await db.get(Chat, event.chat_id) if event.chat_id else None
            if (
                not chat
                or chat.account_id != event.account_id
                or await is_blocked(
                    db, account_id=chat.account_id, user_id=chat.interlocutor_id, chat_id=chat.id
                )
            ):
                event.state = "cancelled"
                await db.commit()
                continue
            data = unpacked(event.payload)
            message_kind = event.kind in {"greeting", "command", "keyword", "ai", "paid"}
            if message_kind and (not master.get("responder") or not scoped(cfg, event.account_id)):
                event.state = "cancelled"
                await db.commit()
                continue
            if event.kind in {"ai", "greeting", "command", "keyword"}:
                if (await get_setting(f"reply:handoff:{chat.id}", {})).get("paused"):
                    event.state = "cancelled"
                    await db.commit()
                    continue
                if data.get("trigger") and await db.scalar(
                    select(ChatMessage.id)
                    .where(
                        ChatMessage.chat_id == chat.id,
                        ChatMessage.is_my.is_(True),
                        ChatMessage.id > data["trigger"],
                    )
                    .limit(1)
                ):
                    event.state = "cancelled"
                    await db.commit()
                    continue
            feature = {
                "greeting": "greeting_enabled",
                "paid": "paid_enabled",
                "keyword": "keywords_enabled",
                "command": "commands_enabled",
                "ai": "ai_enabled",
            }.get(event.kind)
            if feature and (
                not cfg[feature] or aware(event.created_at) < utcnow() - timedelta(minutes=15)
            ):
                event.state = "cancelled"
                await db.commit()
                continue
            policy = await rc.policy(event.account_id)
            if event.kind in {"review", "review_chat"}:
                allowed = (
                    policy["review_chat"]
                    if event.event_key.startswith("review-chat:")
                    else (master.get("auto_thanks") and policy["review_reply"])
                )
                if not allowed:
                    event.state = "cancelled"
                    await db.commit()
                    continue
            order = await db.get(Order, event.order_id) if event.order_id else None
            if order:
                try:
                    live = await (await accounts.client(event.account_id)).order_details(
                        order.funpay_order_id
                    )
                except Exception:
                    event.due_at = utcnow() + timedelta(seconds=30)
                    await db.commit()
                    continue
                required = "paid" if event.kind in {"paid", "confirmation_reminder"} else "closed"
                if (
                    live.get("status") != required
                    or int(live.get("buyer", {}).get("user_id", 0)) != chat.interlocutor_id
                ):
                    event.state = "cancelled"
                    await db.commit()
                    continue
                if event.kind == "review" and (
                    live.get("review", {}).get("reply") or live.get("review", {}).get("hidden")
                ):
                    event.state = "cancelled"
                    await db.commit()
                    continue
                if event.kind == "review_reminder" and live.get("review", {}).get("rating"):
                    event.state = "cancelled"
                    await db.commit()
                    continue
                if data.get("template"):
                    fresh_values = await context(db, chat, order, live.get("review"))
                    fresh_values["lot_amount"] = str(live.get("quantity") or "")
                    data["text"] = MessageFormatter.format(data["template"], fresh_values)
                    event.payload = packed(data)
            if event.kind == "ai":
                if not cfg["ai_enabled"]:
                    event.state = "cancelled"
                    await db.commit()
                    continue
                from .reply_ai import answer

                result = await answer(db, chat, data["question"], cfg)
                data["text"] = result["text"]
                event.payload = packed(data)
                if result["handoff"]:
                    await save_setting(
                        f"reply:handoff:{chat.id}",
                        {"paused": True, "reason": result["reason"], "at": utcnow().isoformat()},
                    )
                    await log(f"Диалог #{chat.id} передан продавцу", event.account_id, "warning")
            if event.kind == "review" and not master.get("auto_thanks"):
                continue
            policy = await rc.policy(event.account_id)
            flag = {
                "confirmation": "confirmation",
                "confirmation_reminder": "confirmation_reminder",
                "review_reminder": "review_reminder",
            }.get(event.kind)
            if flag and not policy[flag]:
                event.state = "cancelled"
                await db.commit()
                continue
            if message_kind and not (await get_setting("plugins", {})).get("responder"):
                event.state = "cancelled"
                await db.commit()
                continue
            if (
                event.kind == "ai"
                and data.get("trigger")
                and await db.scalar(
                    select(ChatMessage.id)
                    .where(
                        ChatMessage.chat_id == chat.id,
                        ChatMessage.is_my.is_(True),
                        ChatMessage.id > data["trigger"],
                    )
                    .limit(1)
                )
            ):
                event.state = "cancelled"
                await db.commit()
                continue
            event.state = "sending"
            if cfg.get("dry_run"):
                event.state = "dry_run"
                await db.commit()
                await log(
                    f"Dry-run: {event.kind}, ответ #{event.id}, без отправки", event.account_id
                )
                continue
            await db.commit()
            try:
                if event.kind == "review":
                    await (await accounts.client(event.account_id)).reply_review(
                        order.funpay_order_id, data["text"]
                    )
                else:
                    from .message_service import rate_limiter

                    await rate_limiter.send(
                        event.account_id,
                        chat.id,
                        data["text"],
                        idempotency_key=f"reply:{event.event_key}",
                    )
            except Exception:
                event.state = "uncertain"
                await db.commit()
                await log(
                    f"Отправка #{event.id}: проверьте результат на FunPay перед повтором",
                    event.account_id,
                    "error",
                )
            else:
                event.state = "sent"
                await db.commit()
                await log(f"{event.kind}: ответ #{event.id} отправлен", event.account_id)


async def tick():
    # Enabling account settings is independent of opening the plugin editor.
    async with Session() as db:
        enabled = await db.scalar(
            select(Settings.key).where(Settings.key.like("account-automation:%")).limit(1)
        )
    if not enabled and not await configured():
        return
    async with observe_lock:
        await observe_orders()
    await flush()


async def recover():
    if not await configured():
        initial = await rc.config()
        initial.pop("has_ai_key", None)
        initial.update(version=2, enabled_at=utcnow().isoformat())
        await save_setting("responder:v2", initial)
    async with Session() as db:
        for row in (
            await db.scalars(select(ReplyEvent).where(ReplyEvent.state == "sending"))
        ).all():
            row.state = "uncertain"
        await db.commit()
