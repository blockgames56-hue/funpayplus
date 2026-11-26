"""Local loyalty points. FunPay does not process or redeem these balances."""

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from .db import Session
from .events import bus
from .funpay import FunPayError
from .models import BlacklistEntry, BonusBalance, BonusEntry, Chat, Order

lock = asyncio.Lock()


async def award(order_id: int) -> None:
    from .services import accounts, get_setting

    settings = await get_setting("loyalty", {})
    if not settings.get("enabled"):
        return
    async with Session() as db:
        observed = await db.get(Order, order_id)
        lookup = (
            (observed.account_id, observed.funpay_order_id)
            if observed
            and observed.status == "closed"
            and observed.date_source == "observed"
            and not observed.buyer_id
            else None
        )
    if lookup:
        detail = await (await accounts.client(lookup[0])).order_details(lookup[1])
        buyer = detail.get("buyer") or {}
        buyer_id = int(buyer.get("user_id") or 0)
        if buyer_id:
            async with Session() as db:
                observed = await db.get(Order, order_id)
                observed.buyer_id = buyer_id
                observed.buyer_name = buyer.get("username") or observed.buyer_name
                await db.commit()
    async with lock, Session() as db:
        order = await db.get(Order, order_id)
        if (
            not order
            or order.status != "closed"
            or order.date_source != "observed"
            or order.currency != "RUB"
            or not order.buyer_id
        ):
            return
        if await db.scalar(select(BonusEntry.id).where(BonusEntry.order_id == order.id)):
            return
        if await db.scalar(
            select(BlacklistEntry.id).where(BlacklistEntry.user_id == order.buyer_id)
        ):
            return
        balance = await db.scalar(
            select(BonusBalance)
            .where(
                BonusBalance.account_id == order.account_id, BonusBalance.buyer_id == order.buyer_id
            )
            .with_for_update()
        )
        if not balance:
            balance = BonusBalance(
                account_id=order.account_id,
                buyer_id=order.buyer_id,
                buyer_name=order.buyer_name,
                points=0,
                purchases=0,
            )
            db.add(balance)
        multiplier = settings.get("promotion_multiplier", 1)
        until = settings.get("promotion_until")
        if not until or date.today() > date.fromisoformat(until):
            multiplier = 1
        points = (
            int(order.sum // Decimal(100)) * settings.get("points_per_100_rub", 10) * multiplier
        )
        if balance.purchases:
            points += settings.get("repeat_bonus", 0)
        balance.points += points
        balance.purchases += 1
        balance.buyer_name = order.buyer_name
        db.add(
            BonusEntry(
                account_id=order.account_id,
                buyer_id=order.buyer_id,
                order_id=order.id,
                points=points,
                kind="purchase",
                state="sent",
            )
        )
        await db.commit()
    await bus.publish("loyalty", {"account_id": order.account_id})


async def balance_for(account_id: int, buyer_id: int) -> int:
    async with Session() as db:
        row = await db.scalar(
            select(BonusBalance).where(
                BonusBalance.account_id == account_id, BonusBalance.buyer_id == buyer_id
            )
        )
        return row.points if row else 0


async def revoke(order_id: int) -> None:
    """Reverse points when an awarded order is refunded."""
    async with lock, Session() as db:
        entry = await db.scalar(
            select(BonusEntry).where(BonusEntry.order_id == order_id).with_for_update()
        )
        if not entry or entry.state != "sent" or entry.kind != "purchase":
            return
        balance = await db.scalar(
            select(BonusBalance)
            .where(
                BonusBalance.account_id == entry.account_id, BonusBalance.buyer_id == entry.buyer_id
            )
            .with_for_update()
        )
        if balance:
            balance.points -= entry.points
            balance.purchases = max(0, balance.purchases - 1)
        entry.state = "revoked"
        await db.commit()


async def redeem(chat_id: int, sender_id: int) -> str:
    from .services import accounts, get_setting

    settings = await get_setting("loyalty", {})
    if not settings.get("enabled"):
        return "Бонусная программа сейчас выключена."
    cost = settings.get("reward_cost", 100)
    async with lock, Session() as db:
        chat = await db.get(Chat, chat_id)
        if not chat or not chat.interlocutor_id or chat.interlocutor_id != sender_id:
            return "Покупатель не определён."
        if await db.scalar(
            select(BlacklistEntry.id).where(BlacklistEntry.user_id == chat.interlocutor_id)
        ):
            return "Бонусы недоступны."
        balance = await db.scalar(
            select(BonusBalance)
            .where(
                BonusBalance.account_id == chat.account_id,
                BonusBalance.buyer_id == chat.interlocutor_id,
            )
            .with_for_update()
        )
        if not balance or balance.points < cost:
            return (
                f"Для награды нужно {cost} баллов. Сейчас у вас {balance.points if balance else 0}."
            )
        pending = await db.scalar(
            select(BonusEntry.id).where(
                BonusEntry.account_id == chat.account_id,
                BonusEntry.buyer_id == chat.interlocutor_id,
                BonusEntry.kind == "reward",
                BonusEntry.state.in_(["sending", "uncertain"]),
            )
        )
        if pending:
            return "Предыдущая награда ожидает проверки продавцом."
        balance.points -= cost
        entry = BonusEntry(
            account_id=chat.account_id,
            buyer_id=chat.interlocutor_id,
            order_id=None,
            points=-cost,
            kind="reward",
            state="sending",
        )
        db.add(entry)
        await db.commit()
        entry_id = entry.id
        buyer = balance.buyer_name
    message = settings.get("reward_text", "Ваш бонус").replace("{buyer}", buyer)
    try:
        await accounts.send(chat_id, message)
    except FunPayError:
        async with Session() as db:
            entry = await db.get(BonusEntry, entry_id)
            entry.state = "uncertain"
            await db.commit()
        return "Результат отправки награды неизвестен. Продавец проверит чат."
    async with Session() as db:
        entry = await db.get(BonusEntry, entry_id)
        entry.state = "sent"
        await db.commit()
    return ""


async def resolve(entry_id: int, *, delivered: bool) -> None:
    async with lock, Session() as db:
        entry = await db.get(BonusEntry, entry_id)
        if not entry or entry.kind != "reward" or entry.state != "uncertain":
            raise ValueError("Награда уже обработана или не требует проверки")
        if not delivered:
            balance = await db.scalar(
                select(BonusBalance)
                .where(
                    BonusBalance.account_id == entry.account_id,
                    BonusBalance.buyer_id == entry.buyer_id,
                )
                .with_for_update()
            )
            balance.points -= entry.points
        entry.state = "sent" if delivered else "refunded"
        await db.commit()
