"""Scope-aware blacklist matching shared by all active automation paths."""

from sqlalchemy import and_, or_, select

from .models import BlacklistEntry


def matching_entry(account_id: int, user_id: int, chat_id: int | None = None):
    """Return a query matching global, account, and optional-chat exclusions."""
    scopes = [
        and_(BlacklistEntry.account_id.is_(None), BlacklistEntry.chat_id.is_(None)),
        and_(BlacklistEntry.account_id == account_id, BlacklistEntry.chat_id.is_(None)),
    ]
    if chat_id is not None:
        scopes.append(BlacklistEntry.chat_id == chat_id)
    return select(BlacklistEntry.id).where(
        BlacklistEntry.user_id == user_id,
        or_(*scopes),
    ).limit(1)


async def is_blocked(db, *, account_id: int, user_id: int, chat_id: int | None = None) -> bool:
    return bool(await db.scalar(matching_entry(account_id, user_id, chat_id)))


def scope(entry: BlacklistEntry) -> str:
    if entry.chat_id is not None:
        return "chat"
    if entry.account_id is not None:
        return "account"
    return "global"
