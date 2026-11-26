"""Scope matching and API validation for global/account/chat blacklist records."""

import unittest

from app.blacklist import is_blocked, scope
from app.main import add_blacklist, import_blacklist
from app.models import Account, Base, BlacklistEntry, Chat
from app.schemas import BlacklistCreate, BlacklistImport
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class BlacklistScopesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.Session() as db:
            db.add_all([
                Account(id=1, title="One", golden_key="cipher", username="One", user_agent="test"),
                Account(id=2, title="Two", golden_key="cipher", username="Two", user_agent="test"),
                Chat(id=10, account_id=1, funpay_chat_id="10", interlocutor_id=42,
                     interlocutor_name="Buyer", last_message_cursor=None, runner_baselined=False),
                Chat(id=20, account_id=2, funpay_chat_id="20", interlocutor_id=42,
                     interlocutor_name="Buyer", last_message_cursor=None, runner_baselined=False),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_global_account_and_chat_scope_do_not_leak(self):
        async with self.Session() as db:
            db.add_all([
                BlacklistEntry(user_id=1, username="Global"),
                BlacklistEntry(user_id=2, username="Account", account_id=1),
                BlacklistEntry(user_id=3, username="Chat", account_id=1, chat_id=10),
            ])
            await db.commit()
            self.assertTrue(await is_blocked(db, account_id=2, user_id=1, chat_id=20))
            self.assertTrue(await is_blocked(db, account_id=1, user_id=2, chat_id=10))
            self.assertFalse(await is_blocked(db, account_id=2, user_id=2, chat_id=20))
            self.assertTrue(await is_blocked(db, account_id=1, user_id=3, chat_id=10))
            self.assertFalse(await is_blocked(db, account_id=1, user_id=3, chat_id=999))
            self.assertFalse(await is_blocked(db, account_id=2, user_id=3, chat_id=20))

    async def test_chat_creation_derives_account_and_rejects_foreign_buyer(self):
        async with self.Session() as db:
            payload = BlacklistCreate(user_id=42, username="Buyer", scope="chat", chat_id=10)
            row = await add_blacklist(payload, db)
            self.assertEqual((row["scope"], row["account_id"], row["chat_id"]), ("chat", 1, 10))
            with self.assertRaises(HTTPException) as error:
                await add_blacklist(
                    BlacklistCreate(user_id=99, username="Other", scope="chat", chat_id=10), db
                )
            self.assertEqual(error.exception.status_code, 422)

    async def test_duplicate_scope_is_rejected_but_other_scope_is_allowed(self):
        async with self.Session() as db:
            global_row = BlacklistCreate(user_id=42, username="Buyer")
            await add_blacklist(global_row, db)
            with self.assertRaises(HTTPException) as error:
                await add_blacklist(global_row, db)
            self.assertEqual(error.exception.status_code, 409)
            account_row = await add_blacklist(
                BlacklistCreate(user_id=42, username="Buyer", scope="account", account_id=1), db
            )
            self.assertEqual(account_row["scope"], "account")

    async def test_import_can_create_account_scoped_records(self):
        async with self.Session() as db:
            result = await import_blacklist(
                BlacklistImport(account_id=1, chat_ids=[10], scope="account"), db
            )
            self.assertEqual(result, {"added": 1})
            self.assertTrue(await is_blocked(db, account_id=1, user_id=42, chat_id=10))
            self.assertFalse(await is_blocked(db, account_id=2, user_id=42, chat_id=20))

    def test_scope_label(self):
        self.assertEqual(scope(BlacklistEntry(user_id=1, username="A")), "global")
        self.assertEqual(scope(BlacklistEntry(user_id=1, username="A", account_id=1)), "account")
        self.assertEqual(scope(BlacklistEntry(user_id=1, username="A", chat_id=1)), "chat")
