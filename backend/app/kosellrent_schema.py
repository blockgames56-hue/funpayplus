"""Web form metadata for the original plugin's settings (original keys retained)."""

import math

GROUPS = {
    "delivery": (
        "Выдача и безопасность",
        {
            "currency": "Валюта закупки",
            "auto_refund": "Автовозврат при ошибке выдачи",
            "price_guard_enabled": "Проверять себестоимость перед выдачей",
            "price_guard_margin_rub": "Запас к себестоимости, ₽",
            "multi_qty_confirm_enabled": "Уточнять отдельные аккаунты при покупке нескольких штук",
            "multi_qty_confirm_ttl_min": "Ожидание ответа покупателя, минут",
            "rotate_password_on_delivery": "Менять пароль перед выдачей",
            "wait_rotate_password": "Ждать завершения смены пароля",
            "full_logout_on_delivery": "Выходить из всех сессий Steam перед выдачей",
            "delivery_prefetch_enabled": "Предварительная подготовка выдачи на KOSell",
        },
    ),
    "stock": (
        "Наличие и уведомления",
        {
            "lot_sync_enabled": "Синхронизировать состояние лотов",
            "auto_hide_no_stock": "Скрывать лоты без свободных аккаунтов",
            "auto_hide_zero_balance": "Скрывать лоты при низком балансе",
            "balance_threshold": "Порог баланса в валюте закупки",
            "poll_sec": "Интервал проверки, секунд",
            "notify_sales": "Уведомлять о продажах",
            "notify_rental_end": "Сообщать покупателю об окончании аренды",
            "eta_grace_sec": "Допустимое ожидание аккаунта, секунд",
            "eta_notify_cooldown_sec": "Интервал уведомлений об ожидании, секунд",
            "tz_offset_hours": "Часовой пояс покупателя, UTC±",
        },
    ),
    "buyers": (
        "Продление и отзывы",
        {
            "friend_minutes": "Длительность режима «Для друга», минут",
            "extend_cooldown": "Пауза между продлениями, секунд",
            "review_bonus_enabled": "Начислять часы за отзыв",
            "review_bonus_hours": "Часов за отзыв",
            "review_bonus_min_stars": "Минимальная оценка для бонуса",
        },
    ),
    "creation": (
        "Автосоздание и цены",
        {
            "ac_usd_rub": "Курс USD/RUB для расчётов",
            "ac_subcat_cap": "Лимит лотов в подразделе",
            "ac_min_price": "Минимальная цена лота, ₽",
            "steam_showcase_enabled": "Создавать витрину в разделе аккаунтов Steam",
            "steam_showcase_top": "Количество игр в витрине Steam",
            "ac_debug": "Подробный журнал автосоздания (Debug)",
        },
    ),
}
RANGES = {
    "poll_sec": (15, 3600),
    "tz_offset_hours": (-12, 14),
    "review_bonus_min_stars": (1, 5),
    "review_bonus_hours": (1, 8760),
    "multi_qty_confirm_ttl_min": (1, 1440),
    "ac_usd_rub": (0.01, 100000),
    "ac_min_price": (1, 10000000),
    "ac_subcat_cap": (1, 1000),
    "steam_showcase_top": (0, 200),
    "friend_minutes": (1, 1440),
    "eta_grace_sec": (0, 86400),
    "eta_notify_cooldown_sec": (60, 86400),
}


def validate_settings(data, module):
    allowed = {key for _, fields in GROUPS.values() for key in fields} | {
        "api_key",
        "product_markup_percent",
        "steam_showcase_force_ids",
        "ac_template",
        "texts",
    }
    if data.keys() - allowed:
        raise ValueError("Неизвестные параметры настроек")
    result = dict(data)
    for key, value in data.items():
        default = module.DEFAULT_SETTINGS.get(key, False if key == "ac_debug" else None)
        if isinstance(default, bool) and type(value) is not bool:
            raise ValueError(f"{key}: ожидается переключатель")
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            if type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError(f"{key}: требуется конечное число")
            low, high = RANGES.get(key, (0, 10000000))
            if not low <= value <= high or (isinstance(default, int) and int(value) != value):
                raise ValueError(f"{key}: допустимо от {low} до {high}")
            result[key] = int(value) if isinstance(default, int) else value
    if "api_key" in result:
        if not isinstance(result["api_key"], str) or len(result["api_key"]) > 500:
            raise ValueError("Некорректный API-ключ")
        result["api_key"] = module._sanitize_key(result["api_key"])
        if not result["api_key"]:
            result.pop("api_key")  # An empty password input preserves the saved key.
    if "currency" in result and result["currency"] not in {"RUB", "USD"}:
        raise ValueError("Валюта: RUB или USD")
    if "texts" in result:
        texts = result["texts"]
        if (
            not isinstance(texts, dict)
            or texts.keys() - module.DEFAULT_TEXTS.keys()
            or any(not isinstance(text, str) or len(text) > 20000 for text in texts.values())
        ):
            raise ValueError("Некорректные шаблоны сообщений")
    if "steam_showcase_force_ids" in result and (
        not isinstance(result["steam_showcase_force_ids"], list)
        or len(result["steam_showcase_force_ids"]) > 1000
        or any(type(i) is not int or i <= 0 for i in result["steam_showcase_force_ids"])
    ):
        raise ValueError("ID витрины: список положительных чисел")
    if "product_markup_percent" in result:
        markup = result["product_markup_percent"]
        if (
            not isinstance(markup, dict)
            or len(markup) > 1000
            or any(
                not str(k).isdigit()
                or type(v) not in {float, int}
                or not math.isfinite(v)
                or not 0 <= v <= 100000
                for k, v in markup.items()
            )
        ):
            raise ValueError("Наценка: ID товара → неотрицательный процент")
    if "ac_template" in result:
        template = result["ac_template"]
        if not isinstance(template, dict):
            raise ValueError("Заполните шаблон создания лотов")
        fields = {
            "durations",
            "summary_ru",
            "summary_en",
            "desc_ru",
            "desc_en",
            "price_mode",
            "markup_percent",
            "fixed_public_rub",
            "fixed_prices",
            "usd_rub",
            "min_price",
        }
        if template.keys() - fields:
            raise ValueError("Неизвестные поля шаблона лотов")
        durations = template.get("durations", [])
        if (
            not isinstance(durations, list)
            or not durations
            or len(durations) > 100
            or any(type(h) is not int or not 1 <= h <= 8760 for h in durations)
        ):
            raise ValueError("Сроки аренды: от 1 до 8760 часов")
        if template.get("price_mode") not in {"markup", "fixed"}:
            raise ValueError("Выберите наценку или фиксированную цену")
        if (
            template.get("price_mode") == "fixed"
            and not template.get("fixed_public_rub")
            and not template.get("fixed_prices")
        ):
            raise ValueError("Укажите фиксированную цену лота или цены по срокам")
        for key in ("summary_ru", "summary_en", "desc_ru", "desc_en"):
            if not isinstance(template.get(key, ""), str) or len(template.get(key, "")) > 20000:
                raise ValueError("Слишком длинный текст лота")
        for key in ("markup_percent", "fixed_public_rub", "usd_rub", "min_price"):
            value = template.get(key)
            if value is not None and (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not 0 <= value <= 10000000
            ):
                raise ValueError(f"{key}: некорректное число")
        prices = template.get("fixed_prices", {})
        if (
            not isinstance(prices, dict)
            or len(prices) > 100
            or any(
                not str(k).isdigit()
                or type(v) not in {float, int}
                or not math.isfinite(v)
                or not 0 < v <= 10000000
                for k, v in prices.items()
            )
        ):
            raise ValueError("Фиксированные цены: часы → положительная цена")
        result["ac_template"] = module._sanitize_ac_template_public(template)[0]
    return result


def metadata(module):
    return [
        {
            "id": ident,
            "label": label,
            "fields": [
                {
                    "key": key,
                    "label": text,
                    "description": module.PARAM_DESC.get(key, ""),
                    "type": "boolean"
                    if isinstance(
                        module.DEFAULT_SETTINGS.get(key, False if key == "ac_debug" else None), bool
                    )
                    else "select"
                    if key == "currency"
                    else "number",
                    "min": RANGES.get(key, (0, 10000000))[0],
                    "max": RANGES.get(key, (0, 10000000))[1],
                }
                for key, text in fields.items()
            ],
        }
        for ident, (label, fields) in GROUPS.items()
    ]
