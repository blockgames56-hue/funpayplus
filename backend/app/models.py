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
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    review_count: Mapped[int | None] = mapped_column()
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


class ReplyEvent(Base):
    __tablename__ = "reply_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="SET NULL"), index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    payload: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    last_message_cursor: Mapped[int | None] = mapped_column(BigInteger)
    runner_baselined: Mapped[bool] = mapped_column(default=False)
    runner_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
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


class IncomingEvent(Base):
    """Committed with the message, dispatched independently of network polling."""

    __tablename__ = "incoming_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(250), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(20), default="RECEIVED", index=True)
    reason: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageSend(Base):
    __tablename__ = "message_sends"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(250), unique=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TicketEvent(Base):
    __tablename__ = "ticket_events"
    __table_args__ = (UniqueConstraint("account_id", "funpay_message_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"))
    funpay_message_id: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    funpay_order_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    funpay_lot_id: Mapped[str | None] = mapped_column(String(50), index=True)
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
    amount: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    auto_raise: Mapped[bool] = mapped_column(default=False)
    auto_restore: Mapped[bool] = mapped_column(default=False)
    last_raised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_raise_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LotPromotion(Base):
    __tablename__ = "lot_promotions"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"), index=True)
    original_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    discounted_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    percent: Mapped[int]
    state: Mapped[str] = mapped_column(String(20), default="applying")
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AutoDumpRule(Base):
    __tablename__ = "auto_dump_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(
        ForeignKey("lots.id", ondelete="CASCADE"), unique=True, index=True
    )
    category_url: Mapped[str] = mapped_column(String(1000))
    absolute_min: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    step: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1.00"))
    competitor_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    is_enabled: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(30), default="idle")
    last_error: Mapped[str | None] = mapped_column(String(300))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    options: Mapped[str] = mapped_column(Text, default='{"convert_commission":true}')


class PluginBinding(Base):
    __tablename__ = "plugin_bindings"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"), unique=True)
    quantity: Mapped[int] = mapped_column(default=1)
    provider_id: Mapped[str] = mapped_column(String(50), default="")
    service_id: Mapped[str] = mapped_column(String(100), default="")
    is_extension: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)


class PluginJob(Base):
    __tablename__ = "plugin_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("plugin_bindings.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(30), index=True)
    state: Mapped[str] = mapped_column(String(30), default="waiting_target", index=True)
    quantity: Mapped[int] = mapped_column(default=1)
    target: Mapped[str | None] = mapped_column(String(1000))
    remote_id: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RentalAccount(Base):
    __tablename__ = "rental_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("plugin_bindings.id", ondelete="RESTRICT"), index=True
    )
    label: Mapped[str] = mapped_column(String(150))
    credentials: Mapped[str] = mapped_column(Text)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(30), default="free", index=True)
    pending_password: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String(500))
    game: Mapped[str] = mapped_column(String(150), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class RentalLease(Base):
    __tablename__ = "rental_leases"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("plugin_jobs.id", ondelete="RESTRICT"), unique=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("rental_accounts.id", ondelete="RESTRICT"), index=True
    )
    state: Mapped[str] = mapped_column(String(30), default="active", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bonus_awarded: Mapped[bool] = mapped_column(default=False)
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("chats.id", ondelete="RESTRICT"))
    buyer_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    buyer_name: Mapped[str] = mapped_column(String(100), default="")
    funpay_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT")
    )
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    warned_30_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str] = mapped_column(String(30), default="expired")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RentalAccountBinding(Base):
    __tablename__ = "rental_account_bindings"
    __table_args__ = (UniqueConstraint("account_id", "binding_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("rental_accounts.id", ondelete="CASCADE"), index=True
    )
    binding_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_bindings.id", ondelete="CASCADE"), index=True
    )


class RentalBuyer(Base):
    __tablename__ = "rental_buyers"
    __table_args__ = (UniqueConstraint("account_id", "buyer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    buyer_id: Mapped[int] = mapped_column(BigInteger)
    notes: Mapped[str] = mapped_column(Text, default="")
    verified_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RentalBonus(Base):
    __tablename__ = "rental_bonuses"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("rental_leases.id", ondelete="RESTRICT"))
    minutes: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RentalAction(Base):
    __tablename__ = "rental_actions"
    request_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    lease_id: Mapped[int] = mapped_column(ForeignKey("rental_leases.id", ondelete="RESTRICT"))
    kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("plugin", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plugin: Mapped[str] = mapped_column(String(30), index=True)
    key: Mapped[str] = mapped_column(String(80))
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class KosellRental(Base):
    __tablename__ = "kosell_rentals"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), unique=True)
    binding_id: Mapped[int | None] = mapped_column(
        ForeignKey("plugin_bindings.id", ondelete="SET NULL")
    )
    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="RESTRICT"))
    buyer_id: Mapped[int] = mapped_column(BigInteger)
    kosell_uid: Mapped[str | None] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="PURCHASING", index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KosellOperation(Base):
    __tablename__ = "kosell_operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_id: Mapped[int] = mapped_column(
        ForeignKey("kosell_rentals.id", ondelete="RESTRICT"), index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), unique=True
    )
    event_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="CLAIMED", index=True)
    hours: Mapped[int]
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KosellMessage(Base):
    __tablename__ = "kosell_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_id: Mapped[int] = mapped_column(
        ForeignKey("kosell_rentals.id", ondelete="RESTRICT"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SupportRequest(Base):
    __tablename__ = "support_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(30), default="submitting")
    remote_id: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportRequestOrder(Base):
    __tablename__ = "support_request_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("support_requests.id", ondelete="CASCADE"), index=True
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)


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


class DeliveryReservation(Base):
    __tablename__ = "delivery_reservations"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("auto_delivery_rules.id", ondelete="SET NULL"))
    item: Mapped[str | None] = mapped_column(Text)  # Encrypted; never returned by diagnostics.
    resolution_note: Mapped[str | None] = mapped_column(Text)  # Encrypted operator audit.
    state: Mapped[str] = mapped_column(String(20), default="reserved", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BlacklistEntry(Base):
    __tablename__ = "blacklist"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(index=True)
    username: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BonusBalance(Base):
    __tablename__ = "bonus_balances"
    __table_args__ = (UniqueConstraint("account_id", "buyer_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    buyer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    buyer_name: Mapped[str] = mapped_column(String(100))
    points: Mapped[int] = mapped_column(default=0)
    purchases: Mapped[int] = mapped_column(default=0)


class BonusEntry(Base):
    __tablename__ = "bonus_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    buyer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), unique=True
    )
    points: Mapped[int]
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Settings(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class MarketPurchase(Base):
    __tablename__ = "market_purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40))
    game: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(500))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(10), default="rub")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    credentials: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FulfillmentTask(Base):
    __tablename__ = "fulfillment_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="waiting_target")
    target: Mapped[str | None] = mapped_column(String(500))
    guild_id: Mapped[str | None] = mapped_column(String(40))
    boost_count: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    friend_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppRouteSupplier(Base):
    __tablename__ = "app_route_suppliers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    api_type: Mapped[str] = mapped_column(String(20), default="webhook")
    endpoint_url: Mapped[str] = mapped_column(String(1000))
    token: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)


class AppRouteMapping(Base):
    __tablename__ = "app_route_mappings"
    __table_args__ = (UniqueConstraint("account_id", "funpay_item_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    funpay_item_id: Mapped[str] = mapped_column(String(50))
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("app_route_suppliers.id", ondelete="CASCADE")
    )
    supplier_item_id: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(default=1)


class AppRouteDelivery(Base):
    __tablename__ = "app_route_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), unique=True, index=True
    )
    mapping_id: Mapped[int] = mapped_column(
        ForeignKey("app_route_mappings.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="pending")
    result_data: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    channel: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(20), default="info")
    source_plugin: Mapped[str | None] = mapped_column(String(30), index=True)
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


class OfflineGuardAccount(Base):
    """Steam account added to the guard plugin; credentials stay encrypted."""

    __tablename__ = "offline_guard_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(150))
    login: Mapped[str] = mapped_column(String(100))
    credentials: Mapped[str] = mapped_column(Text)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
