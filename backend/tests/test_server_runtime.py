"""Offline VPS invariants: responsive API, bounded event buffers and client disconnects."""

import asyncio
import re
import tempfile
import threading
import unittest
from pathlib import Path
from time import perf_counter
from unittest.mock import AsyncMock, Mock, patch

import httpx
from app import main
from app.config import config
from app.events import EventBus
from app.funpay_gateway import FunPayGateway
from fastapi import WebSocketDisconnect
from sqlalchemy.ext.asyncio import create_async_engine


class ServerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_funpay_sdk_does_not_block_twenty_health_requests(self):
        temporary = tempfile.TemporaryDirectory(prefix="server-health-probe-")
        self.addCleanup(temporary.cleanup)
        engine = create_async_engine(
            "sqlite+aiosqlite:///" + (Path(temporary.name) / "probe.db").as_posix()
        )
        gateway = FunPayGateway("offline-test-key-not-a-real-cookie")
        started, released = threading.Event(), threading.Event()

        def slow_remote():
            started.set()
            return released.wait(10)

        slow = asyncio.create_task(gateway.call(slow_remote))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:

                async def probe():
                    before = perf_counter()
                    response = await client.get(
                        "/api/health", headers={"Authorization": f"Bearer {config.api_token}"}
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()["ok"])
                    return (perf_counter() - before) * 1000

                with patch.object(main, "engine", engine):
                    latency = await asyncio.wait_for(
                        asyncio.gather(*(probe() for _ in range(20))), timeout=4
                    )
            self.assertEqual(engine.pool.checkedout(), 0)
            self.assertFalse(
                slow.done(), "Health requests must finish while the remote SDK is still waiting"
            )
            print(
                f"LOCAL ASGI/SQLite: 20 health requests during blocked SDK; p50={sorted(latency)[10]:.1f} ms, p95={sorted(latency)[18]:.1f} ms"
            )
        finally:
            released.set()
            await slow
            await gateway.close()
            await engine.dispose()

    async def test_slow_browser_has_bounded_queue_and_latest_events(self):
        bus = EventBus()
        socket = Mock()
        queue = bus.subscribe(socket, ["chats"])
        for i in range(1000):
            await bus.publish("chats", {"id": i})
        self.assertEqual(queue.qsize(), 256)
        self.assertEqual((await queue.get())["data"]["id"], 744)
        last = None
        while not queue.empty():
            last = await queue.get()
        self.assertEqual(last["data"]["id"], 999)
        await bus.publish("orders", {"id": 1000})
        self.assertTrue(queue.empty())
        bus.remove(socket)
        self.assertFalse(bus.subscribers)
        self.assertFalse(bus.channels)

    async def test_browser_disconnect_does_not_stop_server_automation(self):
        socket = Mock()
        socket.headers = {"authorization": f"Bearer {config.api_token}"}
        socket.query_params, socket.cookies = {}, {}
        socket.accept = AsyncMock()
        socket.receive_text = AsyncMock(side_effect=WebSocketDisconnect())
        socket.send_json = AsyncMock()
        socket.close = AsyncMock()
        bus = EventBus()
        with (
            patch.object(main, "bus", bus),
            patch.object(main.workers, "stop", AsyncMock()) as stop,
        ):
            await main.websocket(socket)
        socket.accept.assert_awaited_once()
        stop.assert_not_awaited()
        self.assertFalse(bus.subscribers)


class ContainerContracts(unittest.TestCase):
    def test_shutdown_deadlines_allow_drain_without_duplicate_worker_processes(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "backend/Dockerfile").read_text()
        compose = (root / "docker-compose.yml").read_text()
        graceful = int(re.search(r"--graceful-timeout (\d+)", dockerfile)[1])
        container_grace = int(re.search(r"stop_grace_period: (\d+)s", compose)[1])
        self.assertLess(graceful, container_grace)
        self.assertGreaterEqual(graceful, 90)
        self.assertRegex(dockerfile, r"--workers 1(?: |\")")
        self.assertEqual(compose.count("restart: unless-stopped"), 3)
        self.assertIn('max-size: "10m"', compose)
        self.assertIn('max-file: "3"', compose)
