"""Dates are remote message dates, never the time a chat was imported."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app import main, services
from app.chat_dates import UNKNOWN_DATE, order_date
from app.funpay import parse_messages, soup_of
from app.models import Account, Base, Chat, ChatMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def message(ident, date="", text="Message", **metadata):
    return {
        "id": ident,
        "author": 2,
        "html": f'<div class="chat-msg-item"><a href="/users/2/">Buyer</a>{date}<div class="chat-msg-text">{text}</div></div>',
        **metadata,
    }


def batch(*items):
    return {"node": {"id": "chat"}, "messages": list(items)}


class ParserTests(unittest.TestCase):
    def test_order_today_uses_funpay_day_at_host_midnight_boundary(self):
        now = datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(order_date(datetime(2026, 12, 31, 1, 0),
            soup_of('<div class="tc-date-time">сегодня, 01:00</div>'), now=now),
            datetime(2026, 12, 31, 22, 0, tzinfo=timezone.utc))

    def test_order_yearless_december_rolls_back_from_january(self):
        now = datetime(2027, 1, 2, 12, tzinfo=timezone.utc)
        self.assertEqual(order_date(datetime(2027, 12, 31, 23, 50),
            soup_of('<div class="tc-date-time">31 декабря, 23:50</div>'), now=now),
            datetime(2026, 12, 31, 20, 50, tzinfo=timezone.utc))

    def test_html_full_date_with_seconds_and_explicit_year(self):
        data = parse_messages(
            batch(
                message(1, '<div class="chat-msg-date" title="15 ноября, 11:39:13">15.11.26</div>')
            ),
            1,
        )
        self.assertEqual(
            data[0]["created_at"], datetime(2026, 11, 15, 8, 39, 13, tzinfo=timezone.utc)
        )

    def test_grouped_messages_inherit_header_in_server_id_order(self):
        data = parse_messages(
            batch(
                message(3),
                message(
                    2, '<div class="chat-msg-date" title="31 декабря, 23:59:00">31.12.25</div>'
                ),
            ),
            1,
        )
        self.assertEqual([m["funpay_message_id"] for m in data], [2, 3])
        self.assertEqual(data[0]["created_at"], data[1]["created_at"])
        self.assertEqual(data[0]["created_at"].year, 2025)

    def test_unix_timestamp_has_priority_and_milliseconds_work(self):
        for raw in (1730000000, 1730000000000):
            row = parse_messages(batch({**message(1), "date": raw}), 1)[0]
            self.assertEqual(row["created_at"], datetime.fromtimestamp(1730000000, timezone.utc))

    def test_unknown_or_invalid_date_is_not_today(self):
        for html in ("", '<div class="chat-msg-date" title="broken">99.99.26</div>'):
            self.assertEqual(
                parse_messages(batch(message(1, html)), 1)[0]["created_at"], UNKNOWN_DATE
            )


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.service = services.AccountService()
        client = AsyncMock()
        client.user_id = 1
        self.service.client = AsyncMock(return_value=client)
        self.patches = [
            patch.object(services, "Session", self.session),
            patch.object(services.bus, "publish", AsyncMock()),
            patch.object(services.bus, "emit", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
        async with self.session() as db:
            db.add(Account(id=1, title="Shop", golden_key="test", user_agent="test"))
            db.add(
                Chat(
                    id=1,
                    account_id=1,
                    funpay_chat_id="chat",
                    interlocutor_id=2,
                    interlocutor_name="Buyer",
                    unread_count=3,
                    last_message_at=datetime(2026, 11, 15, 19, 15),
                    last_message_text="Newest",
                )
            )
            db.add(
                ChatMessage(
                    chat_id=1,
                    funpay_message_id=10,
                    sender_id=2,
                    sender_name="Buyer",
                    text="Newest",
                    created_at=datetime(2026, 11, 15, 19, 15),
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await self.engine.dispose()

    async def test_duplicate_repairs_timestamp_without_events_or_unread_change(self):
        await self.service.ingest(
            1,
            [
                {
                    "type": "chat_node",
                    "data": batch(
                        message(
                            10,
                            '<div class="chat-msg-date" title="10 ноября, 10:00:00">10.11.26</div>',
                            "Newest",
                        )
                    ),
                }
            ],
            automate=True,
        )
        async with self.session() as db:
            chat = await db.get(Chat, 1)
            self.assertEqual(chat.last_message_at, datetime(2026, 11, 10, 7))
            self.assertEqual(chat.unread_count, 3)
        services.bus.emit.assert_not_awaited()

    async def test_older_history_does_not_replace_latest_preview_or_date(self):
        await self.service.ingest(
            1,
            [
                {
                    "type": "chat_node",
                    "data": batch(
                        message(
                            5,
                            '<div class="chat-msg-date" title="1 января, 10:00:00">01.01.25</div>',
                            "Old",
                        )
                    ),
                }
            ],
            mark_unread=False,
        )
        async with self.session() as db:
            chat = await db.get(Chat, 1)
            self.assertEqual(chat.last_message_text, "Newest")
            self.assertEqual(chat.last_message_at, datetime(2026, 11, 15, 19, 15))

    async def test_incremental_group_without_header_uses_previous_message(self):
        await self.service.ingest(
            1, [{"type": "chat_node", "data": batch(message(11, text="New"))}], mark_unread=False
        )
        async with self.session() as db:
            row = await db.scalar(select(ChatMessage).where(ChatMessage.funpay_message_id == 11))
            self.assertEqual(row.created_at, datetime(2026, 11, 15, 19, 15))

    async def test_api_sorts_dates_and_filters_period_with_unknown_last(self):
        async with self.session() as db:
            db.add(
                Chat(
                    id=2,
                    account_id=1,
                    funpay_chat_id="older",
                    interlocutor_id=3,
                    interlocutor_name="Older",
                    is_pinned=True,
                    last_message_at=datetime(2025, 1, 1),
                )
            )
            db.add(
                Chat(
                    id=3,
                    account_id=1,
                    funpay_chat_id="unknown",
                    interlocutor_id=4,
                    interlocutor_name="Unknown",
                    last_message_at=UNKNOWN_DATE,
                )
            )
            await db.commit()
            newest = await main.list_chats(db=db)
            oldest = await main.list_chats(db=db, sort="oldest")
            recent = await main.list_chats(db=db, after=datetime(2026, 1, 1))
            self.assertEqual([r["id"] for r in newest], [1, 2, 3])
            self.assertEqual([r["id"] for r in oldest], [2, 1, 3])
            self.assertEqual([r["id"] for r in recent], [1])
