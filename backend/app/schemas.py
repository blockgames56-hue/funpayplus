from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .funpay import USER_AGENT


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(StrictModel):
    token: str = Field(min_length=24, max_length=512)


class AccountCreate(StrictModel):
    golden_key: str = Field(min_length=16, max_length=512)
    proxy_id: int | None = None
    user_agent: str = Field(default=USER_AGENT, min_length=10, max_length=500)

    @field_validator("golden_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("Неверный формат golden_key")
        return value


class AccountPatch(StrictModel):
    proxy_id: int | None = None
    is_active: bool | None = None


class EmailMailboxSettings(StrictModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=3, max_length=320)
    password: str = Field(default="", max_length=1024)


class MarketBuyerSettings(StrictModel):
    token: str = Field(default="", max_length=2048)
    filter_url: str = Field(min_length=15, max_length=4000)
    max_price: Decimal = Field(gt=0, le=10000000)
    reserve_balance: Decimal = Field(ge=0, le=10000000)
    enabled: bool = False


class MarketConnection(StrictModel):
    token: str = Field(min_length=1, max_length=2048)


class DiscordBoostSettings(StrictModel):
    enabled: bool = False
    lot_phrase: str = Field(default="", max_length=200)
    boost_count: int = Field(default=2, ge=1, le=14)
    success_message: str = Field(default="Бусты активированы!", min_length=1, max_length=500)


class SteamGiftSettings(StrictModel):
    enabled: bool = False
    lot_phrase: str = Field(default="", max_length=200)
    api_key: str = Field(default="", max_length=512)
    seller_steamid: str = Field(default="", max_length=32)
    success_message: str = Field(default="Подарок отправлен!", min_length=1, max_length=500)


class FulfillmentSettings(StrictModel):
    discord_boost: DiscordBoostSettings = Field(default_factory=DiscordBoostSettings)
    steam_gift: SteamGiftSettings = Field(default_factory=SteamGiftSettings)


class FulfillmentTarget(StrictModel):
    value: str = Field(min_length=2, max_length=500)


class RouteSupplierCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    api_type: Literal["webhook", "telegram_bot"] = "webhook"
    endpoint_url: str = Field(min_length=12, max_length=1000)
    token: str = Field(default="", max_length=2048)


class RouteSupplierUpdate(RouteSupplierCreate):
    is_active: bool = True


class RouteMappingCreate(StrictModel):
    account_id: int = Field(gt=0)
    funpay_item_id: str = Field(pattern=r"^[0-9]+$", max_length=50)
    supplier_id: int = Field(gt=0)
    supplier_item_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(default=1, ge=1, le=10000)


class OfflineSettings(StrictModel):
    heartbeat: bool = False
    hide_on_exit: bool = False


class DumpOptions(StrictModel):
    keywords: str = Field(default="", max_length=2000)
    excluded_sellers: list[str] = Field(default_factory=list, max_length=100)
    only_sellers: list[str] = Field(default_factory=list, max_length=100)
    min_rating: int = Field(default=0, ge=0, le=5)
    ignore_unrated: bool = False
    max_price: Decimal | None = Field(default=None, gt=0, le=10000000, decimal_places=2)
    divider: Decimal = Field(default=Decimal("0.01"), gt=0, le=100000, decimal_places=2)
    convert_commission: bool = True


class AutoDumpCreate(StrictModel):
    lot_id: int = Field(gt=0)
    category_url: str = Field(min_length=20, max_length=1000)
    absolute_min: Decimal = Field(gt=0, le=10000000, decimal_places=2)
    step: Decimal = Field(default=Decimal("1.00"), gt=0, le=100000, decimal_places=2)
    is_enabled: bool = False
    options: DumpOptions = Field(default_factory=DumpOptions)


class AutoDumpPatch(StrictModel):
    is_enabled: bool


class RobuxSettings(StrictModel):
    universe_id: int = Field(gt=0)
    game_pass_id: int = Field(gt=0)
    target_amount: int = Field(gt=0, le=1000000)
    cover_fee: bool = True
    cookie: str = Field(default="", max_length=4096)


class RobuxConnection(StrictModel):
    cookie: str = Field(min_length=1, max_length=4096)


class MessageCreate(StrictModel):
    text: str = Field(min_length=1, max_length=20000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=250)


class ChatPatch(StrictModel):
    notes: str | None = Field(default=None, max_length=10000)
    is_pinned: bool | None = None
    unread_count: Literal[0] | None = None


class ProxyBatch(StrictModel):
    lines: str = Field(min_length=1, max_length=50000)


class RuleCreate(StrictModel):
    account_id: int = Field(gt=0)
    lot_title_pattern: str = Field(min_length=1, max_length=255)
    match_regex: bool = False
    delivery_type: Literal["pool_keys", "static_text"]
    message_template: str = Field(min_length=1, max_length=10000)
    keys_pool: str = Field(default="", max_length=2000000)
    is_active: bool = True

    @field_validator("message_template")
    @classmethod
    def valid_template(cls, value: str) -> str:
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (
                name not in {"item", "order_id", "buyer", "date"} or spec or conversion
            ):
                raise ValueError("Доступны только {item}, {order_id}, {buyer}, {date}")
        return value


class BlacklistCreate(StrictModel):
    user_id: int = Field(gt=0)
    username: str = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=2000)
    scope: Literal["global", "account", "chat"] = "global"
    account_id: int | None = Field(default=None, gt=0)
    chat_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_scope(self):
        if self.scope == "global" and (self.account_id is not None or self.chat_id is not None):
            raise ValueError("Глобальная блокировка не привязывается к аккаунту или диалогу")
        if self.scope == "account" and (self.account_id is None or self.chat_id is not None):
            raise ValueError("Для аккаунта укажите только аккаунт")
        if self.scope == "chat" and self.chat_id is None:
            raise ValueError("Для диалога укажите чат")
        return self


class BlacklistImport(StrictModel):
    account_id: int = Field(gt=0)
    chat_ids: list[int] = Field(min_length=1, max_length=500)
    scope: Literal["global", "account"] = "global"


class LotBulk(StrictModel):
    dry_run: bool = False
    ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["raise", "price", "activate", "deactivate", "delete", "auto_raise_on", "auto_raise_off"]
    percent: float = Field(default=0, ge=-99, le=1000)

    @field_validator("ids")
    @classmethod
    def positive_ids(cls, values):
        if any(value <= 0 for value in values):
            raise ValueError("ID лота должен быть положительным")
        return list(dict.fromkeys(values))


class LotSave(StrictModel):
    fields: dict[str, str] = Field(default_factory=dict, max_length=150)


class LotCreate(LotSave):
    account_id: int = Field(gt=0)
    node_id: int = Field(gt=0)
    count: int = Field(default=1, ge=1, le=20)


class LotCopies(LotSave):
    count: int = Field(default=1, ge=1, le=20)


class PromotionCreate(StrictModel):
    lot_id: int = Field(gt=0)
    percent: int = Field(ge=1, le=90)
    hours: int = Field(ge=1, le=720)


class TelegramSettings(StrictModel):
    token: str = Field(default="", max_length=300)
    chat_id: str = Field(default="", max_length=100)
    events: list[Literal["orders", "chats", "lots", "stock", "errors", "tickets"]] = Field(
        default_factory=lambda: ["orders", "chats", "stock", "errors"]
    )
    enabled: bool = False

    @field_validator("token")
    @classmethod
    def token_format(cls, value):
        if value and not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", value):
            raise ValueError("Некорректный токен бота")
        return value


class ReplyCommand(StrictModel):
    trigger: str = Field(min_length=1, max_length=100)
    reply: str = Field(min_length=1, max_length=5000)
    match: Literal["exact", "contains"] = "exact"
    is_active: bool = True


class TicketTemplate(StrictModel):
    pattern: str = Field(min_length=1, max_length=500)
    reply: str = Field(min_length=1, max_length=5000)
    is_active: bool = True


class PluginSettings(StrictModel):
    responder: bool = False
    reply_delay_min: float = Field(default=2, ge=0, le=30)
    reply_delay_max: float = Field(default=5, ge=0, le=60)
    auto_ticket: bool = False
    ticket_templates: list[TicketTemplate] = Field(default_factory=list, max_length=30)
    first_message: str = Field(
        default="Здравствуйте! Спасибо за сообщение. Скоро отвечу.", max_length=5000
    )
    keywords: str = Field(default="", max_length=1000)
    keyword_reply: str = Field(default="", max_length=5000)
    night_start: int = Field(default=23, ge=0, le=23)
    night_end: int = Field(default=8, ge=0, le=23)
    night_reply: str = Field(default="", max_length=5000)
    offline_activity: bool = False
    auto_thanks: bool = False
    thanks_text: str = Field(default="Спасибо за покупку и отзыв!", max_length=5000)
    review_replies: dict[int, str] = Field(default_factory=dict)
    commands: list[ReplyCommand] = Field(default_factory=list, max_length=100)

    @field_validator("review_replies")
    @classmethod
    def validate_review_replies(cls, value: dict[int, str]) -> dict[int, str]:
        if any(rating not in range(1, 6) or len(reply) > 5000 for rating, reply in value.items()):
            raise ValueError("Ответы доступны для оценок 1–5 и не длиннее 5000 символов")
        return value


class LoyaltySettings(StrictModel):
    enabled: bool = False
    points_per_100_rub: int = Field(default=10, ge=0, le=1000)
    repeat_bonus: int = Field(default=10, ge=0, le=10000)
    reward_cost: int = Field(default=100, ge=1, le=1000000)
    reward_text: str = Field(
        default="Спасибо за покупки! Ваш бонус: {buyer}", min_length=1, max_length=3000
    )
    promotion_multiplier: int = Field(default=1, ge=1, le=10)
    promotion_until: date | None = None


class AppSettings(StrictModel):
    commission_percent: float = Field(default=0, ge=0, le=100)
    timezone: str = Field(default="Europe/Tallinn", max_length=100)
    auto_raise_interval: int = Field(default=3600, ge=60, le=86400)
    sounds: bool = True
    compact: bool = False

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Неизвестный часовой пояс") from error
        return value


class CopyPublish(StrictModel):
    account_id: int = Field(gt=0)
    source_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=30000)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    amount: int = Field(ge=1, le=1000000)
    extra: dict[str, str] = Field(default_factory=dict, max_length=50)


class KeyPoolAppend(StrictModel):
    keys: str = Field(min_length=1, max_length=2000000)
