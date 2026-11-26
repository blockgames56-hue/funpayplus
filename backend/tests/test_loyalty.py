"""Loyalty accounting uses observed orders exactly once."""

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app import loyalty, promotions
from app.models import Account, Base, BonusBalance, BonusEntry, Lot, LotPromotion, Order, utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class LoyaltyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.session() as db:
            db.add(
                Account(
                    id=1,
                    title="Sample",
                    golden_key="encrypted",
                    username="Sample",
                    user_agent="Test",
                )
            )
            db.add(
                Order(
                    id=1,
                    account_id=1,
                    funpay_order_id="TEST1",
                    description="Sample",
                    sum=Decimal("250.00"),
                    currency="RUB",
                    buyer_id=42,
                    buyer_name="Buyer",
                    status="closed",
                    date_source="observed",
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_awards_once_and_skips_initial_history(self):
        with (
            patch.object(loyalty, "Session", self.session),
            patch(
                "app.services.get_setting",
                new=AsyncMock(
                    return_value={
                        "enabled": True,
                        "points_per_100_rub": 10,
                        "repeat_bonus": 5,
                        "promotion_multiplier": 1,
                    }
                ),
            ),
            patch.object(loyalty.bus, "publish", new=AsyncMock()),
        ):
            await loyalty.award(1)
            await loyalty.award(1)
            async with self.session() as db:
                balance = await db.scalar(select(BonusBalance))
                entries = (await db.scalars(select(BonusEntry))).all()
                self.assertEqual((balance.points, balance.purchases, len(entries)), (20, 1, 1))
                db.add(
                    Order(
                        id=2,
                        account_id=1,
                        funpay_order_id="TEST2",
                        description="Sample",
                        sum=Decimal("250.00"),
                        currency="RUB",
                        buyer_id=42,
                        buyer_name="Buyer",
                        status="closed",
                        date_source="history_unknown",
                    )
                )
                await db.commit()
            await loyalty.award(2)
            async with self.session() as db:
                self.assertEqual(len((await db.scalars(select(BonusEntry))).all()), 1)
            await loyalty.revoke(1)
            await loyalty.revoke(1)
            async with self.session() as db:
                balance = await db.scalar(select(BonusBalance))
                entry = await db.scalar(select(BonusEntry))
                self.assertEqual(
                    (balance.points, balance.purchases, entry.state), (0, 0, "revoked")
                )


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_price_change_blocks_automatic_restore(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session = async_sessionmaker(engine, expire_on_commit=False)
        async with session() as db:
            db.add(
                Account(
                    id=1,
                    title="Sample",
                    golden_key="encrypted",
                    username="Sample",
                    user_agent="Test",
                )
            )
            db.add(
                Lot(
                    id=1,
                    account_id=1,
                    funpay_lot_id="123",
                    node_id=42,
                    game_id=1,
                    category_title="Овощи",
                    title="Пример",
                    price=Decimal("85"),
                    amount=1,
                )
            )
            db.add(
                LotPromotion(
                    id=1,
                    account_id=1,
                    lot_id=1,
                    original_price=Decimal("100"),
                    discounted_price=Decimal("90"),
                    percent=10,
                    ends_at=utcnow(),
                    state="active",
                )
            )
            await db.commit()
        with (
            patch.object(promotions, "Session", session),
            patch.object(promotions.accounts, "sync_lots", new=AsyncMock()),
            patch.object(promotions.accounts, "client", new=AsyncMock()) as client,
        ):
            with self.assertRaises(ValueError):
                await promotions.restore(1)
            client.assert_not_awaited()
            async with session() as db:
                entry = await db.get(LotPromotion, 1)
                self.assertEqual(entry.state, "needs_review")
        await engine.dispose()
