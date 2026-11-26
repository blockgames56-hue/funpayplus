from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative metadata for migrations and API."""


class Proxy(Base):
    __tablename__ = "proxies"
    id: Mapped[int] = mapped_column(primary_key=True)
    protocol: Mapped[str] = mapped_column(String(10))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int]
    username: Mapped[str | None] = mapped_column(String(100))
    password: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum("active", "dead", "checking", name="proxy_status"), default="checking"
    )
    ping_ms: Mapped[int | None]
    country_code: Mapped[str | None] = mapped_column(String(5))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    golden_key: Mapped[str] = mapped_column(Text)
    funpay_id: Mapped[int | None] = mapped_column(index=True, unique=True)
    username: Mapped[str | None] = mapped_column(String(100))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    balance_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    balance_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    balance_eur: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    active_sales: Mapped[int] = mapped_column(default=0)
    active_purchases: Mapped[int] = mapped_column(default=0)
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"))
    user_agent: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(
        Enum("online", "offline", "error", "banned", name="account_status"),
        default="offline",
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (UniqueConstraint("account_id", "funpay_chat_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    funpay_chat_id: Mapped[str] = mapped_column(String(100), index=True)
    interlocutor_id: Mapped[int]
    interlocutor_name: Mapped[str] = mapped_column(String(100))
    interlocutor_avatar: Mapped[str | None] = mapped_column(String(500))
    last_message_text: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    unread_count: Mapped[int] = mapped_column(default=0)
    is_pinned: Mapped[bool] = mapped_column(default=False)
    current_order_id: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str] = mapped_column(Text, default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("chat_id", "funpay_message_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), index=True)
    funpay_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    sender_id: Mapped[int]
    sender_name: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    is_my: Mapped[bool] = mapped_column(default=False)
    has_image: Mapped[bool] = mapped_column(default=False)
    image_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    funpay_order_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    sum: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(10))
    buyer_id: Mapped[int]
    buyer_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(Enum("paid", "closed", "refunded", name="order_status"))
    auto_delivered: Mapped[bool] = mapped_column(default=False)
    delivery_text: Mapped[str | None] = mapped_column(Text)
    delivery_state: Mapped[str] = mapped_column(String(20), default="pending")
    thanks_state: Mapped[str] = mapped_column(String(20), default="pending")
    date_source: Mapped[str] = mapped_column(String(30), default="observed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Lot(Base):
    __tablename__ = "lots"
    __table_args__ = (UniqueConstraint("account_id", "funpay_lot_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    funpay_lot_id: Mapped[str] = mapped_column(String(50), index=True)
    node_id: Mapped[int] = mapped_column(index=True)
    game_id: Mapped[int] = mapped_column(default=0)
    category_title: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[int] = mapped_column(default=1)
    is_active: Mapped[bool] = mapped_column(default=True)
    auto_raise: Mapped[bool] = mapped_column(default=False)
    auto_restore: Mapped[bool] = mapped_column(default=False)
    last_raised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_raise_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AutoDeliveryRule(Base):
    __tablename__ = "auto_delivery_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    lot_title_pattern: Mapped[str] = mapped_column(String(255))
    match_regex: Mapped[bool] = mapped_column(default=False)
    delivery_type: Mapped[str] = mapped_column(
        Enum("pool_keys", "static_text", name="delivery_type")
    )
    message_template: Mapped[str] = mapped_column(Text)
    keys_pool: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True)


class BlacklistEntry(Base):
    __tablename__ = "blacklist"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(unique=True, index=True)
    username: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Settings(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(20), default="info")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(100))
    telegram_message_id: Mapped[int]
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    __table_args__ = (UniqueConstraint("telegram_chat_id", "telegram_message_id"),)
