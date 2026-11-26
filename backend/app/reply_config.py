"""Shared response settings used by account pages and the responder workspace."""

import asyncio
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .message_templates import MessageFormatter
from .security import vault
from .services import get_setting

lock = asyncio.Lock()
TAGS = (
    "date",
    "date_only",
    "time",
    "buyer_name",
    "buyer_id",
    "buyer",
    "order_id",
    "lot_name",
    "lot_price",
    "lot_amount",
    "order_title",
    "order_price",
    "order_link",
    "chat_id",
    "seller_name",
    "stars",
    "review_text",
    "args",
    "command",
    "message",
    "buyer_username",
    "seller_username",
)


class AccountPolicy(BaseModel):
    auto_raise: bool = False
    raise_interval: int = Field(default=7200, ge=60, le=86400)
    confirmation: bool = False
    confirmation_text: str = Field(
        default="Спасибо, {buyer_name}! Будем рады видеть вас снова.", max_length=5000
    )
    review_reply: bool = False
    review_chat: bool = False
    review_chat_text: str = Field(default="Спасибо за отзыв, {buyer_name}!", max_length=5000)
    review_reminder: bool = False
    review_reminder_text: str = Field(
        default="Спасибо за покупку, {buyer_name}! Если всё понравилось, оставьте отзыв: {order_link}",
        max_length=5000,
    )
    confirmation_reminder: bool = False
    reminder_minutes: int = Field(default=60, ge=5, le=10080)
    reminder_count: int = Field(default=1, ge=1, le=3)
    reminder_text: str = Field(
        default="Здравствуйте, {buyer_name}! Если с заказом #{order_id} всё в порядке, подтвердите его, пожалуйста: {order_link}",
        max_length=5000,
    )


class Rule(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    triggers: list[str] = Field(min_length=1, max_length=30)
    reply: str = Field(min_length=1, max_length=5000)
    match: Literal["contains", "exact", "starts_with"] = "contains"
    cooldown: int = Field(default=60, ge=0, le=86400)
    account_cooldown: int = Field(default=0, ge=0, le=86400)
    buyer_cooldown: int = Field(default=0, ge=0, le=86400)
    active_days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    active_from: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    active_until: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True
    case_sensitive: bool = False
    allow_arguments: bool = False
    priority: int = Field(default=0, ge=-1000, le=1000)

    @model_validator(mode="after")
    def validate_schedule(self):
        if len(set(self.active_days)) != len(self.active_days) or any(
            day < 0 or day > 6 for day in self.active_days
        ):
            raise ValueError("Дни расписания должны быть числами от 0 (понедельник) до 6")
        if bool(self.active_from) != bool(self.active_until):
            raise ValueError("Укажите и начало, и конец периода правила")
        return self


class ReplyConfig(BaseModel):
    dry_run: bool = False
    account_scope: Literal["all", "selected"] = "all"
    account_ids: list[int] = Field(default_factory=list, max_length=1000)
    greeting_enabled: bool = True
    greeting_text: str = Field(
        default="Привет, {buyer_name}! 👋\nСпасибо за обращение. Чем можем помочь?", max_length=5000
    )
    greeting_mode: Literal["once", "daily"] = "once"
    greeting_delay: int = Field(default=0, ge=0, le=60)
    paid_enabled: bool = False
    paid_text: str = Field(
        default="Спасибо за покупку, {buyer_name}! Заказ #{order_id} уже в работе.", max_length=5000
    )
    paid_delay: int = Field(default=0, ge=0, le=60)
    keywords_enabled: bool = True
    keywords: list[Rule] = Field(default_factory=list, max_length=100)
    commands_enabled: bool = True
    prefix: str = Field(default="!", min_length=1, max_length=3)
    commands: list[Rule] = Field(default_factory=list, max_length=100)
    reviews_enabled: bool = False
    review_target: Literal["review", "chat"] = "review"
    review_delay: int = Field(default=0, ge=0, le=60)
    skip_empty_reviews: bool = True
    review_texts: dict[str, str] = Field(
        default_factory=lambda: {
            "5": "Спасибо за отличный отзыв, {buyer_name}! ⭐⭐⭐⭐⭐\nБудем рады видеть вас снова!",
            "4": "Благодарим за хороший отзыв! Если что-то можно улучшить — напишите.",
            "3": "Спасибо за отзыв. Подскажите, что можно улучшить?",
            "2": "Жаль, что вам не понравилось. Напишите, что пошло не так.",
            "1": "Очень жаль! Напишите нам, чтобы мы могли разобраться.",
        }
    )
    refunds_enabled: bool = False
    ai_enabled: bool = False
    ai_provider: Literal["openai"] = "openai"
    ai_model: str = Field(default="", max_length=100)
    ai_key: str = Field(default="", max_length=2048)
    ai_lot_ids: list[str] = Field(default_factory=list, max_length=1000)
    ai_handoff_text: str = Field(
        default="Сейчас уточню информацию у продавца. Пожалуйста, ожидайте.", max_length=5000
    )

    @model_validator(mode="after")
    def validate_account_scope(self):
        if any(ident <= 0 for ident in self.account_ids):
            raise ValueError("ID аккаунтов должны быть положительными")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("Аккаунты в области не должны повторяться")
        if self.account_scope == "selected" and not self.account_ids:
            raise ValueError("Выберите хотя бы один аккаунт")
        return self


def validate(model):
    values = model.model_dump()
    texts = [v for k, v in values.items() if k.endswith("_text")]
    if isinstance(model, ReplyConfig):
        if any(
            k not in {"1", "2", "3", "4", "5"} or len(v) > 5000
            for k, v in model.review_texts.items()
        ):
            raise ValueError("Оценки от 1 до 5, тексты до 5000 символов")
        if any(c.isspace() or c.isalnum() for c in model.prefix):
            raise ValueError("Префикс должен состоять из знаков, например !")
        if len({r.id for r in model.commands}) != len(model.commands) or len(
            {r.id for r in model.keywords}
        ) != len(model.keywords):
            raise ValueError("ID правил должны быть уникальны")
        for rule in model.commands + model.keywords:
            if any(not t.strip() or len(t) > 100 for t in rule.triggers):
                raise ValueError("Фраза или команда: от 1 до 100 символов")
            texts.append(rule.reply)
        texts += list(model.review_texts.values())
    for text in texts:
        if text.strip():
            MessageFormatter.validate(text, TAGS, max_length=5000)


async def config(private=False):
    stored = await get_setting("responder:v2", None)
    if stored is None:
        old = await get_setting("plugins", {})
        model = ReplyConfig(
            reviews_enabled=bool(old.get("auto_thanks")),
            greeting_text=old.get("first_message") or ReplyConfig().greeting_text,
            review_texts={
                **ReplyConfig().review_texts,
                **{str(k): v for k, v in old.get("review_replies", {}).items()},
            },
            commands=[
                Rule(
                    id=f"legacy-{i}",
                    triggers=[c["trigger"].lstrip("!")],
                    reply=c["reply"],
                    match=c.get("match", "exact"),
                    enabled=c.get("is_active", True),
                )
                for i, c in enumerate(old.get("commands", []))
            ],
        )
        if old.get("keywords") and old.get("keyword_reply"):
            model.keywords = [
                Rule(
                    id="legacy-keywords",
                    triggers=[t.strip() for t in old["keywords"].split("|") if t.strip()][:30]
                    or [old["keywords"]],
                    reply=old["keyword_reply"],
                )
            ]
        stored = model.model_dump()
    result = {**ReplyConfig().model_dump(), **stored}
    if private:
        result["ai_key"] = vault.decrypt(result["ai_key"]) if result["ai_key"] else ""
    else:
        result["has_ai_key"], result["ai_key"] = bool(result["ai_key"]), ""
    return result


async def policy(account_id):
    stored = await get_setting(f"account-automation:{account_id}", None)
    if stored is None:
        old = await get_setting("plugins", {})
        replies = await get_setting("responder:v2", {})
        enabled = old.get("auto_thanks", False)
        if replies.get("version") == 2:
            enabled = bool(
                replies.get("reviews_enabled")
                and (
                    replies.get("account_scope", "all") == "all"
                    or account_id in replies.get("account_ids", [])
                )
            )
        return AccountPolicy(review_reply=enabled).model_dump()
    return {**AccountPolicy().model_dump(), **stored}


def variants(text):
    choices = re.split(r"(?<!\\)\|", text)
    import random

    return random.choice([s.strip().replace(r"\|", "|") for s in choices if s.strip()] or [""])
