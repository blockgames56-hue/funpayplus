"""Validated, database-backed texts. Rendering never evaluates Python expressions."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from .db import Session
from .models import MessageTemplate, Settings

TAG = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
COMMON = ("order_id", "buyer", "price", "currency")


class MessageFormatter:
    @staticmethod
    def validate(template, allowed, *, max_length=8000):
        if not isinstance(template, str) or not template.strip() or len(template) > max_length:
            raise ValueError(f"Введите текст от 1 до {max_length} символов")
        # Braces deliberately have one unambiguous meaning; no attribute access,
        # conversions, format specifications or recursively expanded variables.
        residue = TAG.sub("", template)
        if "{" in residue or "}" in residue:
            raise ValueError("Теги записываются как {order_id}, без выражений и форматирования")
        unknown = set(TAG.findall(template)) - set(allowed)
        if unknown:
            raise ValueError("Недоступные теги: " + ", ".join(sorted(unknown)))
        return template

    @staticmethod
    def format(template, data):
        missing = set(TAG.findall(template)) - data.keys()
        if missing:
            raise ValueError("Не переданы значения тегов: " + ", ".join(sorted(missing)))
        return TAG.sub(lambda m: str(data[m[1]] if data[m[1]] is not None else ""), template)


@dataclass(frozen=True)
class Definition:
    plugin: str
    key: str
    title: str
    default: str
    variables: tuple = COMMON
    group: str = "Выдача"
    audience: str = "buyer"
    condition: str = ""


CATALOG: dict[tuple[str, str], Definition] = {}


def add(plugin, key, title, text, variables=(), group="Выдача", audience="buyer", condition=""):
    row = Definition(
        plugin,
        key,
        title,
        text,
        tuple(dict.fromkeys(COMMON + tuple(variables))),
        group,
        audience,
        condition,
    )
    MessageFormatter.validate(text, row.variables)
    CATALOG[plugin, key] = row


add(
    "kosell",
    "kosell_credentials",
    "Реквизиты аренды",
    "Аренда выдана на {duration_hours} ч.\n🎮 Логин: {login}\n🔑 Пароль: {password}\nSteam Guard: !код\nОстаток времени: !time\nПродление: !kosell продлить {login}\nДоступ до: {expires}. UID: {uid}",
    (
        "login",
        "password",
        "expires",
        "uid",
        "lot_id",
        "product_id",
        "duration_hours",
        "expires_at",
        "rental_uid",
    ),
)
add(
    "kosell",
    "kosell_guard_success",
    "Код Steam Guard",
    "✅ Ваш код Steam Guard: {code}",
    ("code", "login", "lot_id", "product_id", "duration_hours", "expires_at", "rental_uid"),
    group="Steam Guard",
    condition="Обязательная переменная: {code}. Можно указать логин и данные аренды.",
)
add(
    "kosell",
    "kosell_guard_limit",
    "Лимит запросов",
    "⚠️ Запрошено слишком много кодов за короткое время. Поставщик ограничил доступ. Попробуйте снова через минуту.",
    group="Steam Guard",
)
add(
    "kosell",
    "kosell_guard_fail_network",
    "Недоступность поставщика",
    "Система временно недоступна. Повторяем попытку...",
    group="Ошибки",
)
add(
    "kosell",
    "kosell_rent_expired",
    "Аренда завершена / возврат",
    "❌ Ошибка: Время вашей аренды истекло или заказ был возвращен. Выдача кодов недоступна.",
    group="Ошибки",
)
add(
    "kosell",
    "kosell_out_of_stock_alert",
    "Товар закончился",
    "⚠️ KoSell: Товар {item_name} закончился у поставщика. Лот на FunPay выключен.",
    ("item_name",),
    group="Уведомления",
    audience="admin",
)
add(
    "kosell",
    "kosell_price_alert",
    "Закупка дороже продажи",
    "⚠️ KoSell: закупка товара {item_name} дороже продажи. Лот на FunPay выключен.",
    ("item_name",),
    group="Уведомления",
    audience="admin",
)
add(
    "kosell",
    "kosell_manual_check",
    "Ручная сверка закупки",
    "⚠️ KoSell: заказ #{order_id} требует ручной сверки. {reason}. Повторная закупка заблокирована. Проверьте аренду в KoSell и подтвердите UID в приложении.",
    ("reason",),
    group="Уведомления",
    audience="admin",
)
add(
    "kosell",
    "kosell_select",
    "Выбор аренды",
    "У вас несколько аренд. Укажите UID: !код UID. Доступные UID KoSell: {uids}",
    ("uids",),
    group="Steam Guard",
)


add(
    "kosell",
    "kosell_extended",
    "Продление аренды",
    "✅ Аренда продлена на {hours} ч. Новый срок: {expires}. UID: {uid}",
    ("hours", "expires", "uid"),
    group="Продления",
)
add(
    "kosell",
    "kosell_review_bonus",
    "Бонус за отзыв",
    "Спасибо за отзыв! 🎁 К аренде добавлен {hours} ч. Доступ до {expires}.",
    ("hours", "expires", "uid"),
    group="Продления",
)
add(
    "kosell",
    "kosell_extend_intent",
    "Ожидание оплаты продления",
    "Для продления UID {uid} оплатите тот же товар в течение 20 минут. Срок продления будет равен купленному количеству часов.",
    ("uid",),
    group="Продления",
)
add(
    "kosell",
    "kosell_time",
    "Остаток времени",
    "⏳ Осталось {minutes} мин. Аренда действует до {expires}. UID: {uid}",
    ("minutes", "expires", "uid"),
    group="Продления",
)
add(
    "kosell",
    "kosell_low_balance",
    "Баланс ниже порога",
    "⚠️ KoSell: баланс ниже {threshold} {currency}. Пополните кошелёк.",
    ("threshold",),
    group="Уведомления",
    audience="admin",
)
add(
    "kosell",
    "kosell_balance_hidden",
    "Лот скрыт из-за баланса",
    "⚠️ KoSell: лот {item_name} выключен из-за низкого баланса. После пополнения будет включён, если наличие и цена подтвердятся.",
    ("item_name",),
    group="Уведомления",
    audience="admin",
)


add(
    "auto-robux",
    "roblox_start",
    "Начало проверки",
    "⏳ Заказ #{order_id}: проверяем геймпасс и сумму {amount} R$. Ожидайте результата.",
    ("amount",),
)
add(
    "auto-robux",
    "roblox_success",
    "Выдача подтверждена",
    "✅ Робуксы выданы! Куплен геймпасс на {amount} R$. Спасибо за покупку!",
    ("amount",),
    condition="Только после подтверждённой покупки. Текущий модуль проверяет геймпассы, поэтому автоматически этот текст не отправляет.",
)
add(
    "auto-robux",
    "roblox_no_money",
    "Недостаточно средств",
    "Не удалось выдать Robux: недостаточно средств у донора. Продавец уведомлён, ожидайте пополнения.",
)
add(
    "auto-robux",
    "roblox_invalid_link",
    "Неверный геймпасс",
    "Не удалось проверить заказ: геймпасс не найден, не продаётся или его цена неверна. Проверьте ссылку и стоимость.",
)
add(
    "auto-robux",
    "roblox_api_fail",
    "Roblox недоступен",
    "Сервис Roblox временно недоступен. Продавец проверит заказ и сообщит результат.",
)
add(
    "steam-gift",
    "gift_friend_request",
    "Заявка отправлена",
    "👋 Отправил заявку в друзья в Steam. Пожалуйста, примите её, чтобы получить подарок.",
)
add(
    "steam-gift",
    "gift_success",
    "Подарок отправлен",
    "🎁 Подарок отправлен! Приятной игры и спасибо за покупку.",
)
add(
    "steam-gift",
    "gift_region_error",
    "Разные регионы",
    "Не удалось отправить подарок из-за разницы в регионах Steam. Напишите продавцу — подберём решение.",
)
add(
    "steam-gift",
    "gift_timeout",
    "Заявка не принята",
    "Вы пока не приняли заявку в друзья. Напишите, когда будете готовы — продавец продолжит выдачу.",
)
add(
    "discord-boost",
    "discord_start",
    "Заказ принят",
    "⚡ Заказ #{order_id} принят. Пришлите действующую ссылку-приглашение на ваш Discord-сервер.",
)
add(
    "discord-boost",
    "discord_success",
    "Бусты выданы",
    "✅ Успешно выдано {count} бустов на ваш сервер! Спасибо за заказ.",
    ("count",),
)
add(
    "discord-boost",
    "discord_invalid_invite",
    "Неверное приглашение",
    "Ссылка-приглашение недействительна или срок её действия истёк. Создайте новое приглашение и пришлите его в чат.",
)
add(
    "discord-boost",
    "discord_no_tokens",
    "Нет доступных бустов",
    "Сейчас нет доступных аккаунтов с Nitro для выдачи. Продавец уведомлён, ожидайте пополнения.",
    condition="Отправляется после решения оператора: пользовательские токены Discord приложение не использует.",
)
add(
    "app-route",
    "route_success",
    "Товар получен",
    "✅ Заказ #{order_id} выполнен!\n\nВаш товар: {key}\n\nСпасибо за покупку. Если возникнут вопросы — напишите в этот чат.",
    ("key",),
)
add(
    "app-route",
    "route_fail",
    "Ошибка поставщика",
    "Поставщик не подтвердил выдачу заказа #{order_id}. Продавец уведомлён и проверит товар или возможность возврата. Повторно оплачивать заказ не нужно.",
)
add(
    "app-route",
    "route_admin_fail",
    "Сбой поставщика",
    "⚠️ Ошибка поставщика по заказу #{order_id}. Проверьте результат выдачи перед повторной отправкой.",
    audience="admin",
    group="Администратору",
)

RENT = "steam-rent"
add(
    RENT,
    "rent_credentials",
    "Данные аккаунта",
    "🎮 Ваша аренда началась!\n\nЛогин: {login}\nПароль: {password}\nИгра: {game}\nАренда до: {expires}\n\n🔐 Steam Guard: !код({login})\n⏳ Остаток времени: !время\n➕ Продление: !продлить({login})\n📖 Все команды: !аренда",
    ("login", "password", "game", "expires"),
)
for minutes in (30, 10):
    add(
        RENT,
        f"rent_warning_{minutes}m",
        f"Осталось {minutes} минут",
        f"⏰ До конца аренды {{login}} осталось менее {minutes} минут.\nИгра: {{game}}\nПродление: !продлить({{login}})",
        ("login", "game"),
        group="Срок аренды",
    )
add(
    RENT,
    "rent_warning",
    "Настраиваемое предупреждение",
    "⏰ До конца аренды {login} осталось менее {minutes} минут. Для продления: !продлить({login})",
    ("login", "minutes"),
    group="Срок аренды",
)
add(
    RENT,
    "rent_finished",
    "Аренда завершена",
    "✅ Время аренды {login} вышло. Сессия завершена, пароль изменён и проверен. Спасибо за аренду!",
    ("login",),
    group="Срок аренды",
)
add(
    RENT,
    "rent_no_accounts",
    "Нет свободных аккаунтов",
    "Сейчас нет свободных аккаунтов для вашего заказа. Вы добавлены в очередь. Продавец уведомлён — выдача продолжится, когда аккаунт освободится.",
)
add(
    RENT,
    "rent_extended",
    "Аренда продлена",
    "✅ Аренда {login} продлена на {hours} ч.\nНовый срок: {expires}\nПриятной игры!",
    ("login", "hours", "expires"),
    group="Срок аренды",
)
add(
    RENT,
    "rent_review_bonus",
    "Бонус за отзыв",
    "💚 Спасибо за отзыв! К аренде {login} добавлено {minutes} мин.\nНовый срок: {expires}",
    ("login", "minutes", "expires"),
    group="Срок аренды",
)
add(
    RENT,
    "rent_guard",
    "Steam Guard",
    "🔐 Код Steam Guard для {login}: {code}\nКод действует ограниченное время. Никому его не сообщайте.",
    ("login", "code"),
    group="Команды",
)
add(
    RENT,
    "rent_select_login",
    "Выбор аккаунта",
    "Укажите логин аккаунта: !код(ваш_логин).\nВаши аренды:\n{accounts}",
    ("accounts",),
    group="Команды",
)
add(
    RENT,
    "rent_time",
    "Остаток времени",
    "⏳ Ваши аренды:\n{accounts}",
    ("accounts",),
    group="Команды",
)
add(
    RENT,
    "rent_help",
    "Помощь по аренде",
    "🎮 Управление арендой\n\n!код(логин) — код Steam Guard\n!время — срок аренды\n!продлить(логин) — ссылка на продление\n!аккаунты — свободные аккаунты\n!cancel — отмена проверки фото\n!помощь — связаться с продавцом",
    group="Команды",
)
add(
    RENT,
    "rent_free_accounts",
    "Свободные аккаунты",
    "🎮 Доступно для аренды:\n{accounts}",
    ("accounts",),
    group="Команды",
)
add(
    RENT,
    "rent_no_active",
    "Нет активной аренды",
    "Активная аренда не найдена. После оплаты аккаунт появится здесь. Если заказ уже оплачен — напишите !помощь.",
    group="Команды",
)
add(
    RENT,
    "rent_extension_link",
    "Ссылка на продление",
    "➕ Продление аренды {login}:\n{url}\nПосле оплаты время добавится автоматически.",
    ("login", "url"),
    group="Команды",
)
add(
    RENT,
    "rent_extension_missing",
    "Нет лота продления",
    "Для этого аккаунта пока нет подходящего лота продления. Напишите продавцу — он поможет продлить аренду.",
    group="Команды",
)
add(
    RENT,
    "rent_blocked",
    "Покупатель в чёрном списке",
    "Выдача приостановлена. Свяжитесь с продавцом для уточнения деталей заказа.",
    group="Команды",
)
add(
    RENT,
    "rent_help_sent",
    "Продавец уведомлён",
    "Сообщение передано продавцу. Ожидайте ответа в этом чате.",
    group="Команды",
)
PCV = ("photo_timeout", "attempts_left", "reject_attempt", "reason", "game", "date", "time")
add(
    RENT,
    "pc_request",
    "Запрос фото",
    "✅ ОПЛАТА ПОЛУЧЕНА\n\nДля выдачи аренды подтвердите, что вы в ПК-клубе.\n\n📸 Пришлите фото рабочего места: на экране должны быть видны дата и время, рядом — обстановка клуба. Можно прислать скриншот с часами, чатом FunPay и рабочим столом клуба.\n\n⏳ На отправку фото: {photo_timeout} мин.\nИспользуйте своё актуальное изображение. Отмена проверки: !cancel.",
    PCV,
    "Проверка ПК-клуба",
)
for key, title, text in [
    (
        "pc_timeout",
        "Время истекло",
        "⏰ Время на подтверждение истекло. Если вы находитесь в ПК-клубе — напишите !помощь, разберёмся вручную.",
    ),
    (
        "pc_retry",
        "Нужно другое фото",
        "📸 Фото не принято: {reason}\nПришлите новое изображение с рабочим местом и часами. Осталось попыток: {attempts_left}. Отмена: !cancel.",
    ),
    (
        "pc_final",
        "Попытки исчерпаны",
        "Проверка фото требует помощи продавца. Напишите !помощь и объясните ситуацию. Заказ не отмечен выполненным.",
    ),
    ("pc_approved", "Фото одобрено", "✅ Верификация пройдена! Выдаю аккаунт…"),
    (
        "pc_cancelled",
        "Проверка отменена",
        "❌ Проверка аренды отменена. Свяжитесь с продавцом для решения по оплаченному заказу.",
    ),
    (
        "pc_photo_needed",
        "Ожидается изображение",
        "📸 Для проверки отправьте изображение рабочего места в ПК-клубе. Отмена: !cancel.",
    ),
    (
        "pc_received",
        "Фото получено",
        "📸 Фото получено. Выполняется проверка, ожидайте результата.",
    ),
    (
        "pc_review",
        "Ручная проверка",
        "⏳ Фото передано на проверку продавцу. Дождитесь решения, повторно отправлять его пока не нужно.",
    ),
    (
        "pc_processing",
        "Проверка выполняется",
        "⏳ Проверка изображения уже выполняется. Ожидайте результата.",
    ),
]:
    add(RENT, key, title, text, PCV, "Проверка ПК-клуба")
for key, title, text, variables in [
    (
        "rent_admin_stock",
        "Нет аккаунтов",
        "⚠️ Аренда: для заказа #{order_id} нет свободного аккаунта.",
        (),
    ),
    (
        "rent_admin_rotation",
        "Сбой смены пароля",
        "⚠️ Аренда #{lease_id}: смена пароля аккаунта «{label}» не подтверждена. Аккаунт в карантине — требуется проверка.",
        ("lease_id", "label"),
    ),
    (
        "pc_admin_review",
        "Фото ждёт решения",
        "📸 Заказ #{order_id}, покупатель {buyer}: фото ждёт проверки в приложении. Причина: {reason}",
        ("reason",),
    ),
    (
        "rent_admin_help",
        "Запрос помощи",
        "🆘 Покупатель {buyer} просит помощи по аренде. Откройте чат #{chat_id} в приложении.",
        ("chat_id",),
    ),
]:
    add(RENT, key, title, text, variables, "Администратору", "admin")

for plugin, target in (
    ("auto-smm", "ссылку на страницу или публикацию"),
    ("steam-points", "ссылку на профиль Steam"),
    ("auto-stars", "Telegram username получателя"),
):
    add(
        plugin,
        "delivery_start",
        "Заказ оплачен",
        f"✅ Заказ #{{order_id}} оплачен. Пришлите {target}. Количество: {{quantity}}.",
        ("quantity",),
    )
    add(
        plugin,
        "delivery_confirm",
        "Подтверждение получателя",
        "Проверьте получателя: {target}\nКоличество: {quantity}\nОтветьте +, чтобы подтвердить, или пришлите исправленные данные.",
        ("target", "quantity"),
    )
    add(
        plugin,
        "delivery_success",
        "Заказ выполнен",
        "✅ Заказ #{order_id} выполнен!\nПолучатель: {target}\nКоличество: {quantity}\nСпасибо за покупку 💚",
        ("target", "quantity", "expires"),
    )
    add(
        plugin,
        "delivery_invalid",
        "Неверный получатель",
        "Не удалось распознать получателя. Проверьте данные и отправьте их ещё раз.",
    )
    add(
        plugin,
        "delivery_error",
        "Требуется помощь",
        "Заказ #{order_id} требует проверки продавцом. Мы сохранили данные — повторно оплачивать заказ не нужно.",
    )
    add(
        plugin,
        "delivery_admin_error",
        "Ошибка выдачи",
        "⚠️ Заказ #{order_id} требует проверки. Причина: {reason}",
        ("reason",),
        "Администратору",
        "admin",
    )
add(
    "steam-gift",
    "gift_start",
    "Запрос профиля",
    "🎁 Заказ #{order_id} принят! Пришлите код друга или ссылку на профиль Steam.",
)
add(
    "steam-gift",
    "gift_admin_timeout",
    "Заявка не принята",
    "⚠️ Заказ #{order_id}: покупатель не принимает заявку в Steam более 10 минут. Требуется ручное вмешательство.",
    audience="admin",
    group="Администратору",
)


add(
    "steam-points",
    "points_pending",
    "Награды поступают не сразу",
    "🎉 Запрос на {quantity} очков Steam выполнен. Награды могут приходить до 20 минут.\nПрофиль: {target}",
    ("quantity", "target"),
)
add(
    "steam-points",
    "points_no_money",
    "Недостаточно средств",
    "Для выдачи заказа #{order_id} недостаточно средств у поставщика. Продавец уведомлён и проверит заказ.",
)
add(
    "support-tickets",
    "support_auto",
    "Автоматическое обращение",
    "Пожалуйста, проверьте и подтвердите выполненные заказы: {order_ids}",
    ("order_ids",),
    audience="support",
)
add(
    "support-tickets",
    "support_manual",
    "Ручное обращение",
    "Пожалуйста, проверьте выполненный заказ: {order_id}",
    audience="support",
)
for key, title, text, variables in [
    (
        "smm_status",
        "Статус заказа",
        "📋 Статус заказа: {status}. Осталось: {remains}",
        ("status", "remains"),
    ),
    ("smm_refill", "Рефилл запрошен", "✅ Рефилл запрошен. Ожидайте обработки поставщиком.", ()),
    (
        "smm_refill_pending",
        "Рефилл уже запрошен",
        "Рефилл уже запрошен. Ожидайте обработки поставщиком.",
        (),
    ),
    (
        "smm_provider_error",
        "Нет ответа поставщика",
        "Не удалось подтвердить ответ поставщика. Свяжитесь с продавцом — он проверит результат.",
        (),
    ),
]:
    add("auto-smm", key, title, text, variables, "Команды")


# These texts are extracted with ast.literal_eval, never by executing the
# Cardinal plugins. Only buyer-facing constants are shipped, never credentials.

for original in json.loads(
    Path(__file__).with_name("source_messages.json").read_text(encoding="utf-8")
):
    add(
        original["plugin"],
        original["key"],
        original["title"],
        original["text"],
        tuple(original["variables"]) + (PCV if original["plugin"] == "steam-rent" else ()),
        original["group"],
        audience="support" if original["plugin"] == "support-tickets" else "buyer",
    )


async def legacy_text(plugin, key, db):
    """Keep previously edited texts visible when upgrading existing installations."""
    from .integrated_schemas import CONFIG_MODELS

    field = {
        "delivery_start": "start_message",
        "delivery_success": "success_message",
        "support_auto": "message",
    }.get(key)
    if plugin in CONFIG_MODELS and field:
        setting = await db.get(Settings, f"integrated:{plugin}")
        value = json.loads(setting.value).get(field) if setting else None
        if value and value != getattr(CONFIG_MODELS[plugin](), field, None):
            return value
    if plugin in {"steam-gift", "discord-boost"} and key in {"gift_success", "discord_success"}:
        setting = await db.get(Settings, "fulfillment_plugins")
        value = (
            json.loads(setting.value).get(plugin.replace("-", "_"), {}).get("success_message")
            if setting
            else None
        )
        if value and value not in {"Подарок отправлен!", "Бусты активированы!"}:
            return value
    return None


async def render(plugin, key, data=None, *, db=None, legacy=None):
    definition = CATALOG[plugin, key]
    if db is None:
        async with Session() as session:
            return await render(plugin, key, data, db=session, legacy=legacy)
    row = await db.scalar(
        select(MessageTemplate).where(MessageTemplate.plugin == plugin, MessageTemplate.key == key)
    )
    template = (
        row.text if row else legacy or await legacy_text(plugin, key, db) or definition.default
    )
    # Context contains only explicitly permitted tags, never object attributes.
    context = {name: "" for name in definition.variables}
    context.update(data or {})
    return MessageFormatter.format(template, context)


def order_context(order, **extra):
    return {
        "order_id": order.funpay_order_id,
        "buyer": order.buyer_name,
        "price": str(order.sum),
        "currency": order.currency,
        **extra,
    }


async def send(plugin, key, chat_id, data=None, *, legacy=None):
    from .services import accounts

    text = await render(plugin, key, data, legacy=legacy)
    await accounts.send(chat_id, text)


async def notify(plugin, key, data=None, *, channel="errors"):
    from .services import telegram

    text = await render(plugin, key, data)
    if plugin == "steam-rent":
        from .integrated_config import plain_config

        settings = await plain_config(plugin)
        recipients = settings.get("photo_notify_chat_ids", [])
        if recipients:
            from .security import vault
            from .services import get_setting

            config = await get_setting("telegram", {})
            if config.get("token"):
                for recipient in recipients:
                    try:
                        await telegram.request(
                            vault.decrypt(config["token"]),
                            "sendMessage",
                            {"chat_id": recipient, "text": text[:4000]},
                        )
                    except Exception:
                        from .events import bus

                        await bus.log(
                            "notifications",
                            "Не удалось доставить уведомление аренды в Telegram",
                            level="error",
                            source_plugin=plugin,
                        )
                return
    # An unconfigured Telegram channel must never stop a paid rental or block
    # manual review in the application.
    await telegram.notify(channel, text)
