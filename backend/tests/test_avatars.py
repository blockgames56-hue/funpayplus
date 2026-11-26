"""Avatar URLs and local disk cache, without live FunPay requests."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from app import avatar_cache, events, services
from app.avatar_urls import normalize_avatar_url
from app.funpay import FunPayClient, avatar_url, soup_of
from app.models import Account, Base, Chat, ChatMessage
from app.security import vault
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class AvatarCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_funpay_cdn_is_allowed_and_lookalikes_are_rejected(self):
        url = "https://sfunpay.com/s/avatar/0o/f1/0of16i4yd33l81w2urta.jpg"
        self.assertEqual(
            avatar_url(
                soup_of(f'<div class="avatar-photo" style="background-image: url({url});"></div>')
            ),
            url,
        )
        self.assertTrue(avatar_cache.valid_avatar_url(url))
        self.assertEqual(
            normalize_avatar_url("//sfunpay.com/s/avatar/photo.jpg"),
            "https://sfunpay.com/s/avatar/photo.jpg",
        )
        for invalid in (
            "https://sfunpay.com.evil.example/photo.jpg",
            "https://evilsfunpay.com/photo.jpg",
            "https://user:pass@sfunpay.com/photo.jpg",
            "https://sfunpay.com:123/photo.jpg",
            "http://sfunpay.com/photo.jpg",
            "https://funpay.com/img/layout/avatar.png",
        ):
            self.assertIsNone(normalize_avatar_url(invalid))

    async def test_img_avatar_photo_and_lazy_image_are_parsed(self):
        self.assertEqual(
            avatar_url(
                soup_of(
                    '<img class="avatar-photo" data-src="https://sfunpay.com/s/avatar/photo.png">'
                ).img
            ),
            "https://sfunpay.com/s/avatar/photo.png",
        )

    async def test_cdn_image_download_and_external_redirect(self):
        output = io.BytesIO()
        Image.new("RGB", (4, 4), "blue").save(output, "PNG")
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=output.getvalue())
        )
        with patch.object(
            avatar_cache.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: real_client(transport=transport, **kwargs),
        ):
            data = await avatar_cache.download("https://sfunpay.com/s/avatar/photo.png")
        self.assertEqual(avatar_cache.image_format(data), ("png", "image/png"))
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                302, headers={"location": "https://other.example/photo.png"}
            )
        )
        with patch.object(
            avatar_cache.httpx,
            "AsyncClient",
            side_effect=lambda **kwargs: real_client(transport=transport, **kwargs),
        ):
            with self.assertRaises(services.FunPayError):
                await avatar_cache.download("https://sfunpay.com/s/avatar/photo.png")

    async def test_numeric_contact_nodes_do_not_block_photos(self):
        client = FunPayClient("test-cookie")
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    text='<a class="contact-item" data-id="199310261"><div class="contact-item-name">Buyer</div><div class="avatar-photo" style="background-image:url(https://sfunpay.com/s/avatar/photo.jpg)"></div></a>',
                )
            )
        )
        try:
            rows = await client.get_chats()
            self.assertEqual(rows[0]["interlocutor_id"], 0)
            self.assertEqual(
                rows[0]["interlocutor_avatar"], "https://sfunpay.com/s/avatar/photo.jpg"
            )
        finally:
            await client.close()

    async def test_cache_uses_one_remote_fetch_and_rejects_external_hosts(self):
        image = Image.new("RGB", (4, 4), "red")
        output = io.BytesIO()
        image.save(output, "PNG")
        storage = Path(__file__).resolve().parents[2] / "storage"
        with tempfile.TemporaryDirectory(dir=storage) as directory:
            with (
                patch.object(avatar_cache.config, "storage_dir", Path(directory)),
                patch.object(
                    avatar_cache, "download", new=AsyncMock(return_value=output.getvalue())
                ) as download,
            ):
                first = await avatar_cache.cached_avatar("https://funpay.com/files/sample.png")
                second = await avatar_cache.cached_avatar("https://funpay.com/files/sample.png")
                self.assertEqual(first.path, second.path)
                self.assertTrue(Path(first.path).exists())
                download.assert_awaited_once()
        self.assertFalse(avatar_cache.valid_avatar_url("https://funpay.com.evil.example/x.png"))
        self.assertFalse(avatar_cache.valid_avatar_url("http://funpay.com/x.png"))


class AvatarRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [
            patch.object(services, "Session", self.session),
            patch.object(events, "Session", self.session),
        ]
        for item in self.patches:
            item.start()
        self.service = services.AccountService()
        self.client = AsyncMock()
        self.client.user_id = 7
        self.client.get_user_avatar.return_value = "https://sfunpay.com/s/avatar/account.jpg"
        self.service.client = AsyncMock(return_value=self.client)
        async with self.session() as db:
            db.add(
                Account(
                    id=1,
                    title="Seller",
                    username="Seller",
                    funpay_id=7,
                    golden_key=vault.encrypt("fake-cookie"),
                    user_agent="test",
                )
            )
            db.add(
                Chat(
                    id=1,
                    account_id=1,
                    funpay_chat_id="199310261",
                    interlocutor_id=0,
                    interlocutor_name="Buyer",
                    unread_count=1,
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.engine.dispose()

    async def test_missing_account_photo_is_resolved_once_and_persisted(self):
        first = await self.service.ensure_avatar("account", 1)
        second = await self.service.ensure_avatar("account", 1)
        self.assertEqual(first, second)
        self.client.get_user_avatar.assert_awaited_once_with(7)
        async with self.session() as db:
            self.assertEqual((await db.get(Account, 1)).avatar_url, first)

    async def test_absent_photo_is_not_requested_for_every_component(self):
        self.client.get_user_avatar.return_value = None
        self.assertIsNone(await self.service.ensure_avatar("account", 1))
        self.assertIsNone(await self.service.ensure_avatar("account", 1))
        self.client.get_user_avatar.assert_awaited_once()

    async def test_chat_photo_is_taken_from_contact_list_without_user_id(self):
        self.client.get_chats.return_value = [
            {
                "funpay_chat_id": "199310261",
                "interlocutor_id": 0,
                "interlocutor_name": "Buyer",
                "interlocutor_avatar": "https://sfunpay.com/s/avatar/buyer.jpg",
                "last_message_text": "",
                "unread_count": 1,
            }
        ]
        self.assertEqual(
            await self.service.ensure_avatar("chat", 1), "https://sfunpay.com/s/avatar/buyer.jpg"
        )
        self.client.get_user_avatar.assert_not_awaited()

    async def test_duplicate_messages_repair_identity_without_replaying_events(self):
        async with self.session() as db:
            db.add(
                ChatMessage(
                    chat_id=1, funpay_message_id=55, sender_id=42, sender_name="Buyer", text="Hello"
                )
            )
            await db.commit()
        objects = [
            {
                "type": "chat_node",
                "data": {
                    "node": {"id": 199310261},
                    "messages": [
                        {
                            "id": 55,
                            "author": 42,
                            "html": '<div class="chat-msg-item"><a class="chat-msg-author-link" href="/users/42/">Buyer</a><div class="avatar-photo" style="background-image:url(https://sfunpay.com/s/avatar/buyer.jpg)"></div></div>',
                        }
                    ],
                },
            }
        ]
        with patch.object(services.bus, "emit", AsyncMock()) as emit:
            await self.service.ingest(1, objects, automate=False)
            emit.assert_not_awaited()
        async with self.session() as db:
            chat = await db.get(Chat, 1)
            self.assertEqual(chat.interlocutor_id, 42)
            self.assertEqual(chat.interlocutor_avatar, "https://sfunpay.com/s/avatar/buyer.jpg")
            self.assertEqual(chat.unread_count, 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(ChatMessage)), 1)
