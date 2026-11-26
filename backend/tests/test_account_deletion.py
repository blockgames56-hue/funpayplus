"""Deleting a seller unlinks restricted rental records and preserves other sellers."""

import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import account_workspace, main
from app.config import config
from app.models import (
    Account,
    Base,
    Chat,
    KosellMessage,
    KosellOperation,
    KosellRental,
    Lot,
    Order,
    PluginBinding,
    PluginJob,
    RentalAccount,
    RentalAccountBinding,
    RentalAction,
    RentalLease,
    Settings,
    utcnow,
)
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class AccountDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="account-delete-")
        self.engine = create_async_engine("sqlite+aiosqlite:///" + (Path(self.temporary.name) / "test.db").as_posix())

        @event.listens_for(self.engine.sync_engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.stop = AsyncMock()
        self.publish = AsyncMock()
        self.patches = [patch.object(main.workers, "stop_account", self.stop),
                        patch.object(main.bus, "publish", self.publish),
                        patch.object(config, "workers_enabled", False)]
        for item in self.patches:
            item.start()
        self.ids = [await self.seed(i) for i in (1, 2)]

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()
        self.temporary.cleanup()

    async def seed(self, number):
        async with self.session() as db:
            seller = Account(title=f"Seller {number}", golden_key="encrypted", user_agent="offline")
            db.add(seller)
            await db.flush()
            chat = Chat(account_id=seller.id, funpay_chat_id=str(number), interlocutor_id=10 + number,
                        interlocutor_name="Buyer")
            lot = Lot(account_id=seller.id, funpay_lot_id=str(number), node_id=81,
                      category_title="Test", title="Rental", price=Decimal("100"))
            order = Order(account_id=seller.id, funpay_order_id=f"ORDER{number}", description="Rental",
                          sum=Decimal("100"), currency="RUB", buyer_id=10 + number, buyer_name="Buyer", status="closed")
            db.add_all([chat, lot, order])
            await db.flush()
            binding = PluginBinding(lot_id=lot.id, kind="steam-rent")
            db.add(binding)
            await db.flush()
            job = PluginJob(order_id=order.id, binding_id=binding.id, kind="steam-rent", state="done")
            steam = RentalAccount(binding_id=binding.id, label=f"Steam {number}", credentials="encrypted-steam",
                                  identity_hash=str(number) * 64, state="rented")
            db.add_all([job, steam])
            await db.flush()
            lease = RentalLease(job_id=job.id, account_id=steam.id, chat_id=chat.id,
                                funpay_account_id=seller.id, state="active", expires_at=utcnow() + timedelta(hours=2))
            kosell = KosellRental(order_id=order.id, chat_id=chat.id, binding_id=binding.id,
                                  buyer_id=10 + number, operation_key=f"rental{number}", payload="{}")
            db.add_all([lease, kosell, RentalAccountBinding(account_id=steam.id, binding_id=binding.id)])
            await db.flush()
            db.add_all([
                KosellMessage(rental_id=kosell.id, event_key=f"message{number}", kind="start", expires_at=utcnow()),
                KosellOperation(rental_id=kosell.id, order_id=order.id, event_key=f"operation{number}", kind="start", hours=1),
                RentalAction(lease_id=lease.id, request_key=f"action{number}", kind="manual"),
                Settings(key=f"account-automation:{seller.id}", value='{}'),
                Settings(key=f"reply:handoff:{chat.id}", value='{}'),
            ])
            await db.commit()
            return dict(seller=seller.id, chat=chat.id, lot=lot.id, order=order.id,
                        binding=binding.id, job=job.id, steam=steam.id, lease=lease.id, kosell=kosell.id)

    async def test_http_delete_with_steam_and_kosell_dependencies(self):
        chosen, other = self.ids

        async def database():
            async with self.session() as db:
                yield db

        original = main.app.dependency_overrides.get(main.get_db)
        main.app.dependency_overrides[main.get_db] = database
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver",
                                         headers={"Authorization": f"Bearer {config.api_token}"}) as client:
                response = await client.delete(f"/api/accounts/{chosen['seller']}")
            self.assertEqual(response.status_code, 200, response.text)
        finally:
            if original is None:
                main.app.dependency_overrides.pop(main.get_db, None)
            else:
                main.app.dependency_overrides[main.get_db] = original
        self.stop.assert_awaited_once_with(chosen["seller"])
        async with self.session() as db:
            for model, key in ((Account, "seller"), (Chat, "chat"), (Order, "order"), (Lot, "lot"),
                               (PluginBinding, "binding"), (PluginJob, "job"), (KosellRental, "kosell")):
                self.assertIsNone(await db.get(model, chosen[key]))
                self.assertIsNotNone(await db.get(model, other[key]))
            steam = await db.get(RentalAccount, chosen["steam"])
            self.assertEqual(steam.credentials, "encrypted-steam")
            self.assertIsNone(steam.binding_id)
            lease = await db.get(RentalLease, chosen["lease"])
            self.assertEqual(lease.state, "active")
            self.assertIsNone(lease.job_id)
            self.assertIsNone(lease.chat_id)
            self.assertIsNone(lease.funpay_account_id)
            sibling = await db.get(RentalLease, other["lease"])
            self.assertEqual(sibling.funpay_account_id, other["seller"])
            self.assertIsNotNone(await db.get(RentalAction, "action1"))
            self.assertIsNone(await db.get(Settings, f"account-automation:{chosen['seller']}"))
            self.assertIsNotNone(await db.get(Settings, f"account-automation:{other['seller']}"))
            self.assertEqual((await db.execute(text("PRAGMA foreign_key_check"))).all(), [])

    async def test_failure_rolls_back_unlinks_and_restores_enabled_state(self):
        chosen = self.ids[0]
        original = account_workspace.remove_account_settings

        async def fail_after_settings(db, ident):
            await original(db, ident)
            raise RuntimeError("simulated failure")

        async with self.session() as db:
            with patch.object(account_workspace, "remove_account_settings", fail_after_settings):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    await main.delete_account(chosen["seller"], db)
        async with self.session() as db:
            self.assertTrue((await db.get(Account, chosen["seller"])).is_active)
            self.assertEqual((await db.get(RentalLease, chosen["lease"])).job_id, chosen["job"])
            self.assertEqual((await db.get(RentalAccount, chosen["steam"])).binding_id, chosen["binding"])
            self.assertIsNotNone(await db.get(KosellRental, chosen["kosell"]))
            self.assertIsNotNone(await db.get(Settings, f"account-automation:{chosen['seller']}"))
        self.publish.assert_not_awaited()
