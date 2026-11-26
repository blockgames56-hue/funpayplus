from __future__ import annotations

import asyncio
from collections import defaultdict

from fastapi import WebSocket

from .db import Session
from .models import Activity


class EventBus:
    def __init__(self):
        self.subscribers: dict[WebSocket, asyncio.Queue] = {}
        self.channels: dict[WebSocket, set[str]] = defaultdict(set)
        self.listeners: dict[str, list] = defaultdict(list)

    def on(self, event: str, listener) -> None:
        if listener not in self.listeners[event]:
            self.listeners[event].append(listener)

    def off(self, event: str, listener) -> None:
        if listener in self.listeners[event]:
            self.listeners[event].remove(listener)

    async def emit(self, event: str, *args) -> None:
        for listener in list(self.listeners[event]):
            await listener(*args)

    def subscribe(self, socket: WebSocket, channels: list[str]) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.subscribers[socket] = queue
        self.channels[socket] = set(channels) & {
            "accounts",
            "chats",
            "notifications",
            "logs",
            "lots",
            "orders",
            "loyalty",
            "tickets",
        }
        return queue

    def remove(self, socket: WebSocket) -> None:
        self.subscribers.pop(socket, None)
        self.channels.pop(socket, None)

    async def publish(self, channel: str, data: dict) -> None:
        event = {"channel": channel, "data": data}
        for socket, queue in list(self.subscribers.items()):
            if channel in self.channels[socket]:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(event)

    async def log(
        self,
        channel: str,
        text: str,
        account_id: int | None = None,
        level: str = "info",
        *,
        source_plugin: str | None = None,
    ) -> None:
        async with Session() as db:
            db.add(
                Activity(
                    channel=channel,
                    text=text[:2000],
                    account_id=account_id,
                    level=level,
                    source_plugin=source_plugin,
                )
            )
            await db.commit()
        data = {
            "text": text,
            "account_id": account_id,
            "level": level,
            "source_plugin": source_plugin,
        }
        if channel != "logs":
            await self.publish(channel, data)
        await self.publish("logs", data)


bus = EventBus()
