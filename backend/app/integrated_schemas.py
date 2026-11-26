"""Validated configuration for the adapted Cardinal plugins."""

import base64
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from .schemas import StrictModel

KINDS = {"auto-smm", "steam-points", "steam-rent", "auto-stars", "support-tickets"}


def https_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("Укажите HTTPS адрес API без токенов в ссылке")
    if (
        parsed.port not in (None, 443)
        or parsed.hostname in {"localhost", "localhost.localdomain"}
        or "." not in parsed.hostname
    ):
        raise ValueError("Нужен публичный HTTPS адрес API")
    return value.rstrip("/")


class SmmProvider(StrictModel):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=12, max_length=1000)
    api_key: str = Field(default="", max_length=2048)
    _url = field_validator("url")(https_endpoint)


class DeliveryConfig(StrictModel):
    enabled: bool = False
    confirm_target: bool = True
    start_message: str = Field(
        default="Спасибо за оплату! Отправьте данные для выдачи.", min_length=1, max_length=4000
    )
    success_message: str = Field(
        default="Заказ #{order_id} выполнен! Спасибо за покупку.", min_length=1, max_length=4000
    )

    @field_validator("start_message", "success_message")
    @classmethod
    def templates(cls, value):
        from string import Formatter

        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (
                name not in {"order_id", "quantity", "buyer", "target", "expires"}
                or spec
                or conversion
            ):
                raise ValueError("Переменные: {order_id}, {quantity}, {buyer}, {target}, {expires}")
        return value


class SmmConfig(DeliveryConfig):
    providers: list[SmmProvider] = Field(default_factory=list, max_length=10)
    allowed_domains: list[str] = Field(
        default_factory=lambda: [
            "t.me",
            "telegram.me",
            "vk.com",
            "youtube.com",
            "youtu.be",
            "instagram.com",
            "tiktok.com",
        ],
        max_length=50,
    )
    check_interval: int = Field(default=300, ge=60, le=3600)
    low_balance_threshold: Decimal = Field(default=Decimal("0"), ge=0, le=1000000)
    hide_on_low_balance: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def domains(cls, values):
        import re

        if any(not re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", value) for value in values):
            raise ValueError("Введите домены без https:// и путей")
        return [value.lower() for value in values]

    @model_validator(mode="after")
    def unique_providers(self):
        if len({row.id for row in self.providers}) != len(self.providers):
            raise ValueError("ID поставщиков должны различаться")
        return self


class PointsConfig(DeliveryConfig):
    api_key: str = Field(default="", max_length=2048)
    low_balance_threshold: Decimal = Field(default=Decimal("0"), ge=0, le=1000000)
    hide_on_low_balance: bool = False


class StarsConfig(DeliveryConfig):
    fragment_cookie: str = Field(default="", max_length=12000)
    fragment_hash: str = Field(default="", max_length=200)
    ton_api_key: str = Field(default="", max_length=2048)
    mnemonic: str = Field(default="", max_length=2000)
    wallet_address: str = Field(default="", max_length=100)
    max_ton_per_order: Decimal = Field(default=Decimal("1"), gt=0, le=10000)
    min_stars: int = Field(default=50, ge=1, le=1000000)
    max_stars: int = Field(default=100000, ge=1, le=1000000)
    show_sender: bool = False

    @model_validator(mode="after")
    def limits(self):
        if self.max_stars < self.min_stars:
            raise ValueError("Максимум Stars должен быть не меньше минимума")
        if self.mnemonic and len(self.mnemonic.split()) != 24:
            raise ValueError("Нужна seed-фраза TON из 24 слов")
        return self


class RentConfig(DeliveryConfig):
    warn_minutes: int = Field(default=10, ge=0, le=120)
    review_bonus_minutes: int = Field(default=0, ge=0, le=1440)
    auto_hide_empty: bool = False
    require_photo_review: bool = False
    warn_30_minutes: bool = True
    keep_extension_active: bool = False
    photo_timeout_minutes: int = Field(default=10, ge=1, le=120)
    photo_max_rejects: int = Field(default=3, ge=1, le=10)
    photo_notify_chat_ids: list[str] = Field(default_factory=list, max_length=20)
    photo_lot_ids: list[int] = Field(default_factory=list, max_length=1000)
    verified_ttl_hours: int = Field(default=24, ge=0, le=720)
    photo_mode: Literal["manual", "automatic"] = "manual"
    photo_exif_time: bool = False
    photo_time_tolerance: int = Field(default=3, ge=1, le=120)
    photo_timezone: str = Field(default="Europe/Moscow", max_length=80)
    photo_exif_geo: bool = False
    photo_geo_radius: int = Field(default=200, ge=20, le=10000)
    photo_overpass: bool = False
    photo_clubs: list[dict] = Field(default_factory=list, max_length=100)
    photo_reverse: bool = False
    photo_vision_api_key: str = Field(default="", max_length=2048)
    photo_ai: bool = False
    photo_ai_provider: Literal["gemini", "openai", "groq", "openrouter", "custom"] = "gemini"
    photo_ai_api_key: str = Field(default="", max_length=2048)
    photo_ai_model: str = Field(default="", max_length=150, pattern=r"^[a-zA-Z0-9_./:-]*$")
    photo_ai_base_url: str = Field(default="", max_length=500)
    photo_ai_prompt: str = Field(
        default="Проверьте, похоже ли изображение на рабочее место в компьютерном клубе. Укажите видимые признаки и сомнения. Референсы — только ориентир. Не утверждайте личность человека или подлинность фото.",
        max_length=6000,
    )

    @field_validator("photo_timezone")
    @classmethod
    def timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Неизвестный часовой пояс") from None
        return value

    @field_validator("photo_notify_chat_ids")
    @classmethod
    def notify_ids(cls, rows):
        import re

        if any(not re.fullmatch(r"-?\d{1,20}", value) for value in rows):
            raise ValueError("Telegram Chat ID должен быть числом")
        return list(dict.fromkeys(rows))

    @field_validator("photo_ai_base_url")
    @classmethod
    def ai_url(cls, value):
        return https_endpoint(value) if value else value

    @field_validator("photo_clubs")
    @classmethod
    def clubs(cls, rows):
        for row in rows:
            if (
                set(row) != {"name", "lat", "lon"}
                or not isinstance(row["name"], str)
                or len(row["name"]) > 150
            ):
                raise ValueError("Клуб: name, lat, lon")
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError):
                raise ValueError("Некорректные координаты клуба") from None
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Некорректные координаты клуба")
        return rows

    success_message: str = Field(
        default="Аренда оформлена до {expires}. Команды: !код и !время.",
        min_length=1,
        max_length=4000,
    )


class SupportConfig(StrictModel):
    enabled: bool = False
    account_id: int | None = Field(default=None, gt=0)
    phpsessid: str = Field(default="", max_length=512)
    min_age_hours: int = Field(default=24, ge=24, le=8760)
    interval_minutes: int = Field(default=60, ge=60, le=10080)
    batch_limit: int = Field(default=5, ge=1, le=10)
    only_delivered: bool = True
    message: str = Field(
        default="Прошу проверить и подтвердить выполненные заказы: {order_ids}",
        min_length=10,
        max_length=4000,
    )

    @field_validator("message")
    @classmethod
    def template(cls, value):
        from string import Formatter

        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (name != "order_ids" or spec or conversion):
                raise ValueError("Доступная переменная: {order_ids}")
        return value


CONFIG_MODELS = {
    "auto-smm": SmmConfig,
    "steam-points": PointsConfig,
    "auto-stars": StarsConfig,
    "steam-rent": RentConfig,
    "support-tickets": SupportConfig,
}


class BindingCreate(StrictModel):
    lot_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=1000000)
    provider_id: str = Field(default="", max_length=50)
    service_id: str = Field(default="", max_length=100)
    is_extension: bool = False
    is_active: bool = True


class TargetInput(StrictModel):
    target: str = Field(min_length=1, max_length=1000)


class RentalAccountCreate(StrictModel):
    binding_id: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=150)
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    shared_secret: str = Field(min_length=1, max_length=100)
    identity_secret: str = Field(min_length=1, max_length=100)
    device_id: str = Field(min_length=8, max_length=100)
    steamid: str = Field(pattern=r"^7656119\d{10}$")

    @field_validator("shared_secret", "identity_secret")
    @classmethod
    def secret(cls, value):
        try:
            if len(base64.b64decode(value, validate=True)) < 16:
                raise ValueError()
        except (ValueError, base64.binascii.Error):
            raise ValueError("Неверный формат секрета maFile") from None
        return value


class RentalStateChange(StrictModel):
    state: Literal["free", "frozen"]


class SupportSend(StrictModel):
    order_ids: list[int] = Field(min_length=1, max_length=10)
