"""Discount a FunPay lot and restore its known original price after expiry."""

import asyncio
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from .db import Session
from .funpay import FunPayError
from .models import Lot, LotPromotion, utcnow
from .services import accounts

lock = asyncio.Lock()


def discount_price(original: Decimal, percent: int) -> Decimal:
    return (original * (Decimal(100 - percent) / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


async def create(lot_id: int, percent: int, hours: int) -> LotPromotion:
    async with lock:
        async with Session() as db:
            lot = await db.get(Lot, lot_id)
            if not lot:
                raise ValueError("Лот не найден")
            account_id = lot.account_id
        await accounts.sync_lots(account_id)
        async with Session() as db:
            lot = await db.get(Lot, lot_id)
            existing = await db.scalar(
                select(LotPromotion.id).where(
                    LotPromotion.lot_id == lot_id,
                    LotPromotion.state.in_(["applying", "active", "restoring", "needs_review"]),
                )
            )
            if existing:
                raise ValueError("Для этого лота уже действует акция или требуется проверка")
            original = lot.price
            discounted = discount_price(original, percent)
            if discounted <= 0 or discounted == original:
                raise ValueError("Скидка не изменяет цену лота")
            promotion = LotPromotion(
                lot_id=lot.id,
                account_id=lot.account_id,
                original_price=original,
                discounted_price=discounted,
                percent=percent,
                ends_at=utcnow() + timedelta(hours=hours),
                state="applying",
            )
            db.add(promotion)
            await db.commit()
            ident = promotion.id
        try:
            await (await accounts.client(account_id)).update_lot(
                lot.funpay_lot_id, price=discounted
            )
        except FunPayError:
            async with Session() as db:
                promotion = await db.get(LotPromotion, ident)
                promotion.state = "needs_review"
                await db.commit()
            raise FunPayError(
                "Результат изменения цены неизвестен. Проверьте лот на FunPay в разделе акций"
            )
        async with Session() as db:
            promotion = await db.get(LotPromotion, ident)
            lot = await db.get(Lot, lot_id)
            lot.price = discounted
            promotion.state = "active"
            await db.commit()
            return promotion


async def restore(ident: int) -> None:
    async with lock:
        async with Session() as db:
            promotion = await db.get(LotPromotion, ident)
            if not promotion or promotion.state not in {"active", "needs_review"}:
                raise ValueError("Акция не найдена или уже завершена")
            account_id, lot_id = promotion.account_id, promotion.lot_id
        await accounts.sync_lots(account_id)
        async with Session() as db:
            promotion = await db.get(LotPromotion, ident)
            lot = await db.get(Lot, lot_id)
            if not lot:
                raise ValueError("Лот удалён")
            if lot.price == promotion.original_price:
                promotion.state = "expired"
                await db.commit()
                return
            if lot.price != promotion.discounted_price:
                promotion.state = "needs_review"
                await db.commit()
                raise ValueError(
                    "Цена изменилась вручную; автоматическое восстановление остановлено"
                )
            promotion.state = "restoring"
            await db.commit()
            original, offer_id = promotion.original_price, lot.funpay_lot_id
        try:
            await (await accounts.client(account_id)).update_lot(offer_id, price=original)
        except FunPayError:
            async with Session() as db:
                promotion = await db.get(LotPromotion, ident)
                promotion.state = "needs_review"
                await db.commit()
            raise FunPayError("Результат восстановления цены неизвестен. Проверьте лот на FunPay")
        async with Session() as db:
            promotion = await db.get(LotPromotion, ident)
            lot = await db.get(Lot, lot_id)
            lot.price = original
            promotion.state = "expired"
            await db.commit()


async def recover(ident: int) -> str:
    async with Session() as db:
        promotion = await db.get(LotPromotion, ident)
        if not promotion or promotion.state != "needs_review":
            raise ValueError("Акция не требует проверки")
        account_id, lot_id = promotion.account_id, promotion.lot_id
    await accounts.sync_lots(account_id)
    async with Session() as db:
        promotion = await db.get(LotPromotion, ident)
        lot = await db.get(Lot, lot_id)
        if not lot:
            raise ValueError("Лот удалён")
        if lot.price == promotion.original_price:
            promotion.state = "expired"
        elif lot.price == promotion.discounted_price:
            promotion.state = "active"
        else:
            raise ValueError("Цена отличается от обеих цен акции. Измените лот вручную на FunPay")
        await db.commit()
        return promotion.state
