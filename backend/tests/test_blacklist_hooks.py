"""Blacklisted buyers retain chat history without triggering automation hooks."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import services
from app.models import Account, Base, BlacklistEntry, ChatMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class BlacklistHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_blacklisted_message_is_stored_without_notifications(self):
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
            db.add(BlacklistEntry(user_id=42, username="Blocked", reason="Test"))
            await db.commit()
        account_service = services.AccountService()
        object_data = [
            {
                "type": "chat_node",
                "id": "users-1-42",
                "data": {
                    "node": {"id": "users-1-42"},
                    "messages": [
                        {
                            "id": 123,
                            "html": '<div class="chat-msg-item"><a class="chat-msg-author-link" href="/users/42/">Blocked</a><div class="chat-msg-text">Hi</div></div>',
                        }
                    ],
                },
            }
        ]
        with (
            patch.object(services, "Session", session),
            patch.object(
                account_service, "client", new=AsyncMock(return_value=SimpleNamespace(user_id=1))
            ),
            patch.object(services.telegram, "notify", new=AsyncMock()) as notify,
            patch.object(services.automation, "respond", new=AsyncMock()) as respond,
            patch.object(services.bus, "publish", new=AsyncMock()),
        ):
            await account_service.ingest(1, object_data, automate=True)
            notify.assert_not_awaited()
            respond.assert_not_awaited()
        async with session() as db:
            self.assertEqual(len((await db.scalars(select(ChatMessage))).all()), 1)
        await engine.dispose()
