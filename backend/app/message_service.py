"""Core automated-send validation and per-account rate limiting."""

import asyncio
from time import monotonic

from .blacklist import is_blocked
from .db import Session
from .models import Account, Chat


class AccountRateLimiter:
    """Serialize automated FunPay writes per account; manual replies are unaffected."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.locks: dict[int, asyncio.Lock] = {}
        self.next_at: dict[int, float] = {}

    async def send(self, account_id: int, chat_id: int, text: str, *, idempotency_key=None):
        from .funpay import FunPayError
        from .services import accounts

        async with self.locks.setdefault(account_id, asyncio.Lock()):
            delay = self.next_at.get(account_id, 0) - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            async with Session() as db:
                account = await db.get(Account, account_id)
                chat = await db.get(Chat, chat_id)
                if not account or not account.is_active or not chat or chat.account_id != account_id:
                    raise FunPayError("Аккаунт отключён или диалог удалён")
                if await is_blocked(
                    db, account_id=account_id, user_id=chat.interlocutor_id, chat_id=chat_id
                ):
                    raise FunPayError("Пользователь находится в чёрном списке")
            # Reserve the next slot before attempting an ambiguous network write.
            self.next_at[account_id] = monotonic() + self.interval
            await accounts.send(chat_id, text, source="automation", idempotency_key=idempotency_key)


rate_limiter = AccountRateLimiter()


