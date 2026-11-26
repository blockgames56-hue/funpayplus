from __future__ import annotations
import ast
import html as _html
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque, Counter
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any, Callable, Optional


def _ensure_deps():
    try:
        import requests  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "requests"])


_ensure_deps()

import requests
import telebot
try:
    from telebot.handler_backends import ContinueHandling
except Exception:
    ContinueHandling = None
from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B
from tg_bot import CBT
from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent
from FunPayAPI.common.enums import MessageTypes, SubCategoryTypes
from FunPayAPI.types import LotFields

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "KOSellRent"
VERSION = "1.1.52"
DESCRIPTION = "KOSellRent — аренда аккаунтов через KOSell.store"
CREDITS = "@vanyaxgod"
UUID = "0d8a4c2f-6e71-4b3a-9c4d-2f1e8a7b6c50"
SETTINGS_PAGE = True

logger = logging.getLogger("kosell_rent")
LP = "[KOSellRent]"

API_BASE = "https://www.kosell.store/api/v1"
TIMEOUT = 25
AUTOCREATE_BLOCKED_PRODUCT_IDS = {4, 275}
AUTOCREATE_BLOCKED_NAME_RE = re.compile(r"\b(atomic\s*heart|schedule\s*i)\b", re.I)
LOT_SYNC_BATCH_SIZE = 25


def _kosell_err_code(value, status_code=None) -> str:
    if value is None:
        return "connection_error" if status_code is None else f"http_{status_code}"
    if isinstance(value, dict):
        raw = value.get("error") or value.get("detail") or value.get("code")
        if isinstance(raw, (dict, list)):
            return _kosell_err_code(raw, status_code)
        if raw:
            return str(raw)
        msg = value.get("message") or value.get("msg")
        if msg:
            m = re.search(
                r"\b(no_stock|insufficient_balance|invalid_api_key|rate_limited|not_found|account_disabled|"
                r"expired|not_active|cooldown|operation_inflight|verify)\b",
                str(msg), re.I)
            if m:
                return m.group(1).lower()
        return f"http_{status_code}" if status_code else str(value)
    if isinstance(value, list):
        return str(value[0]) if value else (f"http_{status_code}" if status_code else "unknown")
    text = str(value).strip()
    if not text:
        return f"http_{status_code}" if status_code else "unknown"
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
            if parsed is not value:
                return _kosell_err_code(parsed, status_code)
        except Exception:
            pass
    m = re.search(
        r"\b(no_stock|insufficient_balance|invalid_api_key|rate_limited|not_found|account_disabled|"
        r"expired|not_active|cooldown|operation_inflight|verify)\b",
        text, re.I)
    if m:
        return m.group(1).lower()
    return text

LEGACY_PLUGIN_DIR = "storage/plugins/kosell_rent"
PLUGIN_DIR = "storage/plugins/kosell_rent"


def _migrate_plugin_dir():
    os.makedirs(PLUGIN_DIR, exist_ok=True)
    if not os.path.isdir(LEGACY_PLUGIN_DIR):
        return
    if os.path.abspath(LEGACY_PLUGIN_DIR) == os.path.abspath(PLUGIN_DIR):
        return
    for name in os.listdir(LEGACY_PLUGIN_DIR):
        if name == "update_pending.json":
            continue
        src = os.path.join(LEGACY_PLUGIN_DIR, name)
        dst = os.path.join(PLUGIN_DIR, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logger.debug(f"{LP} migrate {name}: {e}")


_migrate_plugin_dir()
SETTINGS_FILE = os.path.join(PLUGIN_DIR, "settings.json")
MAPPINGS_FILE = os.path.join(PLUGIN_DIR, "mappings.json")
RENTALS_FILE = os.path.join(PLUGIN_DIR, "rentals.json")
HIDDEN_FILE = os.path.join(PLUGIN_DIR, "hidden.json")
HANDLED_FILE = os.path.join(PLUGIN_DIR, "handled_orders.json")
PENDING_QTY_FILE = os.path.join(PLUGIN_DIR, "pending_qty_orders.json")
STATS_FILE = os.path.join(PLUGIN_DIR, "stats.json")
def _chmod_private(path: str, mode: int) -> None:
    try:
        if os.path.exists(path):
            os.chmod(path, mode)
    except OSError:
        pass


def _harden_private_paths() -> None:
    _chmod_private(PLUGIN_DIR, 0o700)
    try:
        names = os.listdir(PLUGIN_DIR)
    except OSError:
        names = []
    for name in names:
        path = os.path.join(PLUGIN_DIR, name)
        _chmod_private(path, 0o700 if os.path.isdir(path) else 0o600)
    for path in ("logs/log.log", "plugins/kosellrent.log", "configs/_main.cfg"):
        _chmod_private(path, 0o600)


ORDER_URL = "https://funpay.com/orders/{order_id}/"

DEFAULT_TEXTS = {
    "text_delivery": (
        "🦊 Quantum Rent\n"
        "━━━━━━━━━━━━━━\n"
        "Готово, аренда активирована.\n\n"
        "🎮 Игра: {game}\n"
        "👤 Логин: {login}\n"
        "🔑 Пароль: {password}\n\n"
        "⏳ Время аренды: {hours} ч.\n"
        "🕒 До: {expires}\n\n"
        "🔐 Steam Guard: отправьте в чат\n"
        "!кодстим {login}\n\n"
        "Если нужно продлить этот аккаунт после новой оплаты:\n"
        "!продлить {login}\n\n"
        "Приятной игры."
    ),
    "text_extension": (
        "🦊 Quantum Rent\n"
        "━━━━━━━━━━━━━━\n"
        "Аренда продлена.\n\n"
        "🎮 Игра: {game}\n"
        "👤 Аккаунт: {login}\n"
        "⏳ Добавлено: {hours} ч.\n"
        "🕒 Новое окончание: {expires}\n\n"
        "Steam Guard при необходимости: !кодстим {login}"
    ),
    "text_friend_activated": (
        "🦊 Режим «для друга» включён\n"
        "━━━━━━━━━━━━━━\n"
        "Следующая оплата в течение {minutes} мин. выдаст новый отдельный аккаунт, а не продлит текущий."
    ),
    "text_friend_already": (
        "🦊 Режим «для друга» уже активен.\n"
        "Следующая оплата выдаст отдельный аккаунт."
    ),
    "text_code": (
        "🦊 Steam Guard\n"
        "━━━━━━━━━━━━━━\n"
        "👤 {login}\n"
        "🔐 Код: {code}\n"
        "⏱ Действует примерно {ttl} сек."
    ),
    "text_code_ask_login": (
        "🦊 У вас несколько активных аккаунтов.\n"
        "Напишите команду с нужным логином:\n"
        "!кодстим логин\n\n"
        "Доступные логины:\n{logins}"
    ),
    "text_code_not_found": (
        "🦊 Активный аккаунт не найден.\n"
        "Если вы только что оплатили заказ, подождите минуту и попробуйте снова: !кодстим логин"
    ),
    "text_ask_which_account": (
        "🦊 Для {game} найдено несколько аккаунтов.\n"
        "Какой аккаунт продлить после оплаты?\n\n"
        "Напишите:\n"
        "!продлить логин\n\n"
        "Доступные логины:\n{logins}"
    ),
    "text_extend_applied": (
        "🦊 Продление выбрано\n"
        "━━━━━━━━━━━━━━\n"
        "👤 Аккаунт: {login}\n"
        "⏳ Добавлено: {hours} ч.\n"
        "🕒 До: {expires}"
    ),
    "text_extend_no_pending": (
        "🦊 Сейчас нет ожидающего выбора аккаунта.\n"
        "Если бот попросил выбрать аккаунт для продления, отправьте:\n"
        "!продлить логин"
    ),
    "text_review_bonus": (
        "🦊 Спасибо за отзыв!\n"
        "━━━━━━━━━━━━━━\n"
        "Мы добавили бонус к аренде: {hours} ч.\n"
        "🕒 Новое окончание: {expires}"
    ),
    "text_partial": (
        "🦊 Выдача выполнена частично\n"
        "━━━━━━━━━━━━━━\n"
        "Выдано аккаунтов: {delivered} из {ordered}.\n\n"
        "Остальные аккаунты временно недоступны. Продавец уже уведомлён и довыдаст их в ближайшее время."
    ),
    "text_qty_choice": (
        "🦊 Quantum Rent\n"
        "━━━━━━━━━━━━━━\n"
        "Вы купили {qty} шт. лота «{game}».\n\n"
        "Как удобнее выдать аренду?\n\n"
        "Да — {qty} отдельных аккаунта по {hours_unit} ч.\n"
        "Нет — один аккаунт на {total_hours} ч.\n\n"
        "Ответьте одним словом: Да или Нет."
    ),
    "text_qty_timeout_admin": (
        "Покупатель не ответил, как обработать заказ с qty={qty}. "
        "KOSell не списывался, выдача остановлена. Проверьте заказ вручную."
    ),
    "text_problem": (
        "🦊 Quantum Rent\n"
        "━━━━━━━━━━━━━━\n"
        "Автоматическая выдача временно не сработала.\n\n"
        "Продавец уже получил уведомление и скоро поможет с заказом."
    ),
    "text_eta": (
        "🦊 Quantum Rent\n"
        "━━━━━━━━━━━━━━\n"
        "Сейчас нет свободных аккаунтов, но они скоро появятся.\n\n"
        "⏳ Ближайшее освобождение: примерно через {eta}\n\n"
        "Оплата принята — аккаунт выдам автоматически, как только он освободится."
    ),
    "text_delivery_pending": (
        "🦊 KOSell\n"
        "━━━━━━━━━━━━━━\n"
        "Готово, аренда активирована.\n\n"
        "🎮 Игра: {game}\n"
        "👤 Логин: {login}\n"
        "🔑 Пароль: присылаем отдельным сообщением (обновляется после смены)\n\n"
        "⏳ Время аренды: {hours} ч.\n"
        "🕒 До: {expires}\n\n"
        "🔐 Steam Guard: отправьте в чат\n"
        "!кодстим {login}\n\n"
        "Если нужно продлить этот аккаунт после новой оплаты:\n"
        "!продлить {login}"
    ),
    "text_password_changed": (
        "🦊 Пароль аккаунта обновлён\n"
        "━━━━━━━━━━━━━━\n"
        "🎮 Игра: {game}\n"
        "👤 Логин: {login}\n"
        "🔑 Новый пароль: {password}\n\n"
        "Сохраните его — старый больше не действует."
    ),
    "text_rental_ended": (
        "🦊 Аренда завершена\n"
        "━━━━━━━━━━━━━━\n"
        "Игра: {game}\n\n"
        "Спасибо, что выбрали нас! Если всё прошло хорошо, будем благодарны за отзыв — "
        "это помогает держать сервис быстрым и цены приятными."
    ),
}

DEFAULT_SETTINGS = {
    "enabled": True,
    "api_key": "",
    "currency": "RUB",
    "lot_sync_enabled": True,
    "auto_hide_no_stock": False,
    "auto_hide_zero_balance": False,
    "auto_refund": False,
    "price_guard_enabled": True,
    "price_guard_margin_rub": 0,
    "multi_qty_confirm_enabled": True,
    "multi_qty_confirm_ttl_min": 30,
    "review_bonus_enabled": False,
    "notify_sales": False,
    "friend_minutes": 10,
    "review_bonus_hours": 3,
    "review_bonus_min_stars": 5,
    "balance_threshold": 0,
    "notify_rental_end": True,
    "rotate_password_on_delivery": False,
    "wait_rotate_password": True,
    "full_logout_on_delivery": False,
    "eta_grace_sec": 900,
    "eta_notify_cooldown_sec": 600,
    "delivery_prefetch_enabled": False,
    "tz_offset_hours": 3,
    "poll_sec": 60,
    "extend_cooldown": 30,
    "texts": {},
    "texts_template_version": 3,
    "map_url": "https://www.kosell.store/downloads/kosell_funpay_map.json",
    "ac_usd_rub": 95.0,
    "ac_subcat_cap": 15,
    "ac_min_price": 15,
    "product_markup_percent": {"343": 30},
    "steam_showcase_enabled": True,
    "steam_showcase_top": 30,
    "steam_showcase_force_ids": [343],
    "ac_template": None,
}

TEXTS_TEMPLATE_VERSION = DEFAULT_SETTINGS["texts_template_version"]

LEGACY_TEXT_MARKERS = {
    "text_delivery": ("Ваш арендованный аккаунт", "Steam Guard код: напишите"),
    "text_extension": ("Аренда аккаунта {login}", "Новое время окончания"),
    "text_friend_activated": ("Режим «Для друга» активен",),
    "text_friend_already": ("Режим «Для друга» уже активен",),
    "text_code": ("Steam Guard код для {login}",),
    "text_code_ask_login": ("У вас несколько аккаунтов. Укажите логин",),
    "text_code_not_found": ("Активный аккаунт не найден",),
    "text_ask_which_account": ("У вас несколько аккаунтов для {game}", "Какой продлить"),
    "text_extend_applied": ("Продление применено для {login}",),
    "text_extend_no_pending": ("Нет ожидающего выбора аккаунта",),
    "text_review_bonus": ("Спасибо за отзыв! Аккаунт продлён",),
    "text_partial": ("Выдано аккаунтов: {delivered} из {ordered}",),
    "text_qty_choice": ("Вы купили {qty} шт. лота", "Нужны {qty} отдельных"),
    "text_qty_timeout_admin": ("Покупатель не ответил, как обработать заказ",),
    "text_problem": ("Что-то пошло не так с автоматической выдачей",),
    "text_rental_ended": ("Аренда аккаунта ({game}) завершена",),
}


def _normalize_text_template(text: str) -> str:
    text = _strip_public_contacts(text)
    text = re.sub(r"(?<!\S)!код(?!стим)(?=\s|$)", "!кодстим", text)
    text = re.sub(r"(?<!\S)!прод(?!лить)(?=\s|$)", "!продлить", text)
    return text


def _strip_public_contacts(text: str) -> str:
    if not isinstance(text, str):
        return text
    contacts = r"(?:@vanyaxgod|@KOSell1)"
    text = re.sub(rf"(?im)^\s*(?:поддержка|support)\s*:?\s*{contacts}\s*$\n?", "", text)
    text = re.sub(rf"(?im)^.*{contacts}.*$\n?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _migrate_saved_texts(saved_texts, old_version: int) -> "tuple[dict, bool]":
    saved_texts = saved_texts if isinstance(saved_texts, dict) else {}
    migrated = {}
    changed = False
    for key, value in saved_texts.items():
        if not isinstance(value, str):
            changed = True
            continue
        markers = LEGACY_TEXT_MARKERS.get(key, ())
        use_new_default = old_version < TEXTS_TEMPLATE_VERSION and key in DEFAULT_TEXTS and any(m in value for m in markers)
        new_value = DEFAULT_TEXTS[key] if use_new_default else _normalize_text_template(value)
        if new_value != value:
            changed = True
        migrated[key] = new_value
    for key, value in DEFAULT_TEXTS.items():
        if key not in migrated:
            migrated[key] = value
            changed = True
    if old_version < TEXTS_TEMPLATE_VERSION:
        changed = True
    return migrated, changed


def _sanitize_ac_template_public(tpl) -> "tuple[dict | None, bool]":
    if not isinstance(tpl, dict):
        return tpl, False
    out = dict(tpl)
    changed = False
    for key in ("summary_ru", "summary_en", "desc_ru", "desc_en"):
        val = out.get(key)
        if isinstance(val, str):
            clean = _strip_public_contacts(val)
            if clean != val:
                out[key] = clean
                changed = True
    return out, changed


MAP_FILE = os.path.join(PLUGIN_DIR, "funpay_map.json")
MAP_OVERRIDES_FILE = os.path.join(PLUGIN_DIR, "funpay_map_overrides.json")
MISC_GAMES_FILE = os.path.join(PLUGIN_DIR, "misc_funpay_games.json")
NAME_OVERRIDES_FILE = os.path.join(PLUGIN_DIR, "name_overrides.json")
MAP_URLS = [
    "https://www.kosell.store/downloads/kosell_funpay_map.json",
    "https://kosell.store/downloads/kosell_funpay_map.json",
    "https://ru.kosell.store/downloads/kosell_funpay_map.json",
]
PH_TIME, PH_GAME = "KOSTIME", "KOSGAME"
KOSELL_MIN_HOURS = 1
UPDATE_URLS = []
AUTO_UPDATE_ENABLED = False
PLUGIN_INFO_URLS = [
    "https://www.kosell.store/api/plugin/info",
    "https://kosell.store/api/plugin/info",
    "https://ru.kosell.store/api/plugin/info",
]
UPDATE_CHECK_SEC = 1800
SELF_PATH = os.path.abspath(__file__)
UPDATE_PENDING_FILE = os.path.join(PLUGIN_DIR, "update_pending.json")
AC_DEBUG = False
AC_CREATE_DELAY = 2.5
AC_DELETE_DELAY = 0.4
FP_SUMMARY_LIMIT = 70
FP_SUMMARY_RETRY_LIMIT = 60
FP_LIMITS_HINT = (
    "Лимиты FunPay: краткое описание EN — до ~70 символов; "
    "на подкатегорию обычно ~8–10 лотов (зависит от аккаунта). "
    "Дублирующие лоты в одном разделе запрещены."
)
DURATION_PRESETS = {
    "short": [3, 10, 24],
    "standard": [3, 24, 72, 168],
    "full": [3, 24, 72, 168, 360, 720],
}

SETTINGS: dict = {}
MAPPINGS: list = []
RENTALS: dict = {}
HIDDEN: dict = {}
HANDLED: list = []
PENDING_QTY: dict = {}
STATS: dict = {}

cardinal_instance: "Cardinal | None" = None
tg = None
bot: "telebot.TeleBot | None" = None

_lock = threading.Lock()
_poll_stop = threading.Event()
_poll_thread: "threading.Thread | None" = None

_friend: dict = {}           # buyer_key -> activated_at (ts)
_pending_ext: dict = {}      # buyer_key -> {hours, order_id, product_id, allowed_logins, ts}
_extend_intent: dict = {}    # buyer_key -> {login, ts}; explicit next-order extension
_ext_last: dict = {}         # buyer_key -> ts (cooldown)
_processed_msgs: set = set()
_tg_processed_msg_ids: set = set()
_processed_review: set = set()
_admin_alert_dedup: dict = {}
_balance_alerted = {"low": False}
_tg_state_shadow: dict = {}
_fp_cmd_dedup_lock = threading.Lock()
_FP_CMD_DEDUP_TTL = 15
_FP_CMD_DEDUP_FILE = os.path.join(tempfile.gettempdir(), "kosellrent_cmd_dedup.json")

_map_cache: dict = {"data": None, "ts": 0}
_misc_cache: dict = {}
_ac_wizard: dict = {}
_ac_plan: dict = {}          # user_id -> {"items": [...]}
_ac_running: dict = {"on": False}
_ac_lock = threading.Lock()
_update_state: dict = {"pending_restart": False, "latest": None}


def _ac_begin(title: str) -> bool:
    with _ac_lock:
        if _ac_running.get("on"):
            return False
        _ac_running.clear()
        _ac_running.update({"on": True, "title": title, "done": 0, "total": 0,
                            "created": 0, "skipped": 0, "failed": 0, "started": None,
                            "cancel": False})
        return True


def _pause_cardinal_autoraise(reason: str = ""):
    try:
        cfg = getattr(cardinal_instance, "MAIN_CFG", None)
        if not cfg or "FunPay" not in cfg:
            return None
        section = cfg["FunPay"]
        previous = section.get("autoRaise", fallback="0")
        if section.getboolean("autoRaise", fallback=False):
            section["autoRaise"] = "0"
            logger.info(f"{LP} Cardinal autoRaise paused for KOSellRent task ({reason})")
        return previous
    except Exception as e:
        logger.debug(f"{LP} could not pause Cardinal autoRaise: {e}")
        return None


def _restore_cardinal_autoraise(previous):
    if previous is None:
        return
    try:
        cfg = getattr(cardinal_instance, "MAIN_CFG", None)
        if not cfg or "FunPay" not in cfg:
            return
        cfg["FunPay"]["autoRaise"] = str(previous)
        if str(previous).strip().lower() in ("1", "true", "yes", "on"):
            logger.info(f"{LP} Cardinal autoRaise restored after KOSellRent task")
    except Exception as e:
        logger.debug(f"{LP} could not restore Cardinal autoRaise: {e}")
_commission_cache: dict = {}  # subcat_id -> float
_products_cache: dict = {"data": None, "ts": 0, "err": False}   # кэш KOSell /rental/products
_pub_lots_cache: dict = {}    # subcat_id -> {"ts": ts, "lots": [(price, desc, server), ...]}
_bal_cache: dict = {"ts": 0.0, "data": None}  # кэш баланса для меню (60с)
_price_cache: dict = {}       # (product_id, hours) -> {"ts": ts, "rub": float}
_node_form_cache: dict = {}   # subcat_id -> {"ts": ts, "form": {...}}
AC_CACHE_TTL = 900           # 15 минут


def _fp_cmd_norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:160]


def _fp_cmd_seen(chat_id, text: str) -> bool:
    now = time.time()
    key = f"{chat_id}:{_fp_cmd_norm(text)}"

    try:
        import fcntl
        os.makedirs(os.path.dirname(_FP_CMD_DEDUP_FILE), exist_ok=True)
        try:
            fd = os.open(_FP_CMD_DEDUP_FILE, os.O_RDWR)
        except FileNotFoundError:
            fd = os.open(_FP_CMD_DEDUP_FILE, os.O_RDWR | os.O_CREAT, 0o666)
        try:
            os.chmod(_FP_CMD_DEDUP_FILE, 0o666)
        except Exception:
            pass
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.seek(0)
            try:
                data = json.load(fh) or {}
            except Exception:
                data = {}
            data = {str(k): float(v) for k, v in data.items() if now - float(v) <= _FP_CMD_DEDUP_TTL}
            if key in data:
                return True
            data[key] = now
            fh.seek(0)
            fh.truncate()
            json.dump(data, fh, ensure_ascii=False)
            return False
    except Exception:
        dkey = f"{key}:{int(now) // _FP_CMD_DEDUP_TTL}"
        with _fp_cmd_dedup_lock:
            if dkey in _processed_msgs:
                return True
            _processed_msgs.add(dkey)
            if len(_processed_msgs) > 500:
                _processed_msgs.clear()
        return False


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path, data):
    try:
        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        _chmod_private(path, 0o600)
    except Exception as e:
        logger.error(f"{LP} save {path} failed: {e}")


def _autolot_block_reason(*items) -> str:
    parts = []
    for item in items:
        if not item:
            continue
        if isinstance(item, dict):
            pid = item.get("product_id") or item.get("id")
            if str(pid).isdigit() and int(pid) in AUTOCREATE_BLOCKED_PRODUCT_IDS:
                return f"product_id={pid}"
            for key in ("product_name", "game_name", "title_keyword", "name"):
                val = item.get(key)
                if val:
                    parts.append(str(val))
        else:
            parts.append(str(item))
    text = " ".join(parts)
    match = AUTOCREATE_BLOCKED_NAME_RE.search(text)
    return match.group(1) if match else ""


def load_all():
    global SETTINGS, MAPPINGS, RENTALS, HIDDEN, HANDLED, PENDING_QTY, STATS
    _harden_private_paths()
    raw_settings = _load_json(SETTINGS_FILE, {})
    raw_settings = raw_settings if isinstance(raw_settings, dict) else {}
    try:
        old_texts_version = int(raw_settings.get("texts_template_version") or 0)
    except Exception:
        old_texts_version = 0
    migrated_texts, texts_changed = _migrate_saved_texts(raw_settings.get("texts") or {}, old_texts_version)
    migrated_ac_template, ac_template_changed = _sanitize_ac_template_public(raw_settings.get("ac_template"))
    SETTINGS = {**DEFAULT_SETTINGS, **raw_settings}
    SETTINGS["texts"] = {**DEFAULT_TEXTS, **migrated_texts}
    SETTINGS["texts_template_version"] = TEXTS_TEMPLATE_VERSION
    SETTINGS["ac_template"] = migrated_ac_template
    if texts_changed or ac_template_changed:
        _save_json(SETTINGS_FILE, SETTINGS)
    mp = _load_json(MAPPINGS_FILE, [])
    MAPPINGS = mp if isinstance(mp, list) else []
    changed = False
    before_count = len(MAPPINGS)
    MAPPINGS = [m for m in MAPPINGS if not (isinstance(m, dict) and _autolot_block_reason(m))]
    if len(MAPPINGS) != before_count:
        changed = True
        logger.info(f"{LP} Убрано запрещённых привязок автолотов: {before_count - len(MAPPINGS)}")
    for m in MAPPINGS:
        if isinstance(m, dict) and not m.get("rent_code") and m.get("product_id") is not None and m.get("hours") is not None:
            m["rent_code"] = _make_rent_code(m.get("product_id"), m.get("hours"))
            changed = True
    RENTALS = _load_json(RENTALS_FILE, {})
    HIDDEN = _load_json(HIDDEN_FILE, {})
    h = _load_json(HANDLED_FILE, [])
    HANDLED = h if isinstance(h, list) else []
    pq = _load_json(PENDING_QTY_FILE, {})
    PENDING_QTY = pq if isinstance(pq, dict) else {}
    STATS = _normalize_stats(_load_json(STATS_FILE, {}))
    known = {str(m.get("lot_id")) for m in MAPPINGS if m.get("lot_id")}
    orphans = [lid for lid in HIDDEN if lid not in known]
    if orphans:
        for lid in orphans:
            HIDDEN.pop(lid, None)
        save_hidden()
        logger.info(f"{LP} Очищено осиротевших скрытых лотов: {len(orphans)}")
    if changed:
        save_mappings()
    logger.info(f"{LP} Loaded: mappings={len(MAPPINGS)}, rentals_buyers={len(RENTALS)}")


def save_settings():
    _save_json(SETTINGS_FILE, SETTINGS)


def save_mappings():
    _save_json(MAPPINGS_FILE, MAPPINGS)


def save_rentals():
    _save_json(RENTALS_FILE, RENTALS)


def save_hidden():
    _save_json(HIDDEN_FILE, HIDDEN)


def save_handled():
    _save_json(HANDLED_FILE, HANDLED[-500:])


def save_pending_qty():
    _save_json(PENDING_QTY_FILE, PENDING_QTY)


def save_stats():
    _save_json(STATS_FILE, STATS)


def _empty_stats() -> dict:
    return {
        "orders_seen": {},
        "orders_total": 0,
        "rentals_total": 0,
        "extensions_total": 0,
        "review_bonuses_total": 0,
        "hours_total": 0.0,
        "funpay_earned_rub": 0.0,
        "kosell_spent_rub": 0.0,
        "by_product": {},
    }


def _normalize_stats(raw) -> dict:
    st = _empty_stats()
    if isinstance(raw, dict):
        for key in st:
            if key in raw:
                st[key] = raw[key]
    if not isinstance(st.get("orders_seen"), dict):
        st["orders_seen"] = {}
    if not isinstance(st.get("by_product"), dict):
        st["by_product"] = {}
    return st


def _stats_product(product_id, name: str) -> dict:
    key = str(product_id or "unknown")
    item = STATS.setdefault("by_product", {}).setdefault(key, {
        "name": name or "аккаунт",
        "orders": 0,
        "rentals": 0,
        "extensions": 0,
        "hours": 0.0,
        "earned_rub": 0.0,
        "spent_rub": 0.0,
    })
    if name:
        item["name"] = name
    return item


def _stats_add_order(order, product_id=None, product_name: str = "") -> None:
    oid = str(getattr(order, "id", "") or "")
    paid = _order_paid_rub(order)
    if not oid or paid is None:
        return
    seen = STATS.setdefault("orders_seen", {})
    if oid in seen:
        return
    seen[oid] = paid
    if len(seen) > 2000:
        for old in list(seen)[:len(seen) - 2000]:
            seen.pop(old, None)
    STATS["orders_total"] = int(STATS.get("orders_total", 0) or 0) + 1
    STATS["funpay_earned_rub"] = float(STATS.get("funpay_earned_rub", 0) or 0) + paid
    prod = _stats_product(product_id, product_name)
    prod["orders"] = int(prod.get("orders", 0) or 0) + 1
    prod["earned_rub"] = float(prod.get("earned_rub", 0) or 0) + paid
    save_stats()


def _stats_add_kosell(product_id, product_name: str, hours: float, cost_rub: "float | None",
                      kind: str = "rental") -> None:
    hours = float(hours or 0)
    cost = float(cost_rub or 0)
    if kind == "extension":
        STATS["extensions_total"] = int(STATS.get("extensions_total", 0) or 0) + 1
    elif kind == "review_bonus":
        STATS["review_bonuses_total"] = int(STATS.get("review_bonuses_total", 0) or 0) + 1
    else:
        STATS["rentals_total"] = int(STATS.get("rentals_total", 0) or 0) + 1
    STATS["hours_total"] = float(STATS.get("hours_total", 0) or 0) + hours
    STATS["kosell_spent_rub"] = float(STATS.get("kosell_spent_rub", 0) or 0) + cost
    prod = _stats_product(product_id, product_name)
    if kind == "extension":
        prod["extensions"] = int(prod.get("extensions", 0) or 0) + 1
    elif kind == "rental":
        prod["rentals"] = int(prod.get("rentals", 0) or 0) + 1
    prod["hours"] = float(prod.get("hours", 0) or 0) + hours
    prod["spent_rub"] = float(prod.get("spent_rub", 0) or 0) + cost
    save_stats()


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def get_text(key: str) -> str:
    return (SETTINGS.get("texts") or {}).get(key) or DEFAULT_TEXTS.get(key, "")


def fmt(key: str, **kwargs) -> str:
    try:
        return get_text(key).format_map(_SafeDict(**kwargs))
    except Exception:
        return get_text(key)


def _tg_state_key(chat_id, user_id) -> str:
    return f"{chat_id}:{user_id}"


def _continue_tg_handling():
    try:
        return ContinueHandling() if ContinueHandling else None
    except Exception:
        return None


def _remember_tg_state(chat_id, user_id, state: str) -> None:
    _tg_state_shadow[_tg_state_key(chat_id, user_id)] = {"state": state, "ts": _now()}
    _arm_tg_next_step(chat_id)


def _forget_tg_state(chat_id, user_id) -> None:
    _tg_state_shadow.pop(_tg_state_key(chat_id, user_id), None)


def _arm_tg_next_step(chat_id) -> None:
    try:
        handler = globals().get("tg_msg_dispatch")
        if bot and handler and hasattr(bot, "register_next_step_handler_by_chat_id"):
            bot.register_next_step_handler_by_chat_id(chat_id, handler)
    except Exception as e:
        logger.debug(f"{LP} [TG] next step arm failed: {e}")


def _set_tg_state(chat_id, message_id, user_id, state: str) -> None:
    try:
        if tg:
            tg.set_state(chat_id, message_id, user_id, state)
    except Exception as e:
        logger.debug(f"{LP} [TG] set_state failed: {e}")
    _remember_tg_state(chat_id, user_id, state)


def _clear_tg_state(chat_id, user_id, delete: bool = True) -> None:
    try:
        if tg:
            tg.clear_state(chat_id, user_id, delete)
    except Exception as e:
        logger.debug(f"{LP} [TG] clear_state failed: {e}")
    _forget_tg_state(chat_id, user_id)


def _get_tg_state(chat_id, user_id):
    sd = None
    try:
        sd = tg.get_state(chat_id, user_id) if tg else None
    except Exception:
        sd = None
    if sd:
        return sd
    local = _tg_state_shadow.get(_tg_state_key(chat_id, user_id))
    if not local:
        return None
    if _now() - float(local.get("ts") or 0) > 1800:
        _forget_tg_state(chat_id, user_id)
        return None
    return {"state": local.get("state", "")}


def _parse_iso(value) -> float:
    if not value:
        return 0.0
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _tz_label(offset: int) -> str:
    if offset == 3:
        return "МСК"
    if offset == 0:
        return "UTC"
    return f"UTC{'+' if offset >= 0 else ''}{offset}"


def _fmt_expires(value) -> str:
    ts = _parse_iso(value)
    if not ts:
        return str(value or "—")
    try:
        offset = int(SETTINGS.get("tz_offset_hours", 3) or 0)
    except Exception:
        offset = 3
    try:
        tz = timezone(timedelta(hours=offset))
        return datetime.fromtimestamp(ts, tz=tz).strftime("%d.%m.%Y %H:%M ") + _tz_label(offset)
    except Exception:
        return str(value)


class KOSellAPI:
    def __init__(self, api_key: str):
        self.key = api_key
        self.s = requests.Session()
        self.s.headers.update({
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        self._calls = deque()

    def _throttle(self):
        now = time.time()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        if len(self._calls) >= 58:
            wait = 60 - (now - self._calls[0]) + 0.5
            if wait > 0:
                logger.warning(f"{LP} [API] self-throttle, wait {wait:.1f}s")
                time.sleep(wait)
        self._calls.append(time.time())

    def _req(self, method, path, **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        # _req не должен перехватывать Idempotency-Key: он прокидывается через kwargs
        url = f"{API_BASE}{path}"
        for attempt in range(3):
            self._throttle()
            try:
                r = self.s.request(method, url, **kwargs)
            except requests.RequestException as e:
                logger.warning(f"{LP} [API] {method} {path} net error (try {attempt+1}): {e}")
                if attempt == 2:
                    return None
                time.sleep(2)
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "5") or 5)
                logger.warning(f"{LP} [API] 429 on {path}, wait {wait}s")
                time.sleep(wait)
                continue
            return r
        return None

    @staticmethod
    def _err_code(r) -> str:
        if r is None:
            return "connection_error"
        try:
            j = r.json()
            return _kosell_err_code(j, r.status_code)
        except Exception:
            pass
        return f"http_{r.status_code}"

    def balance(self):
        r = self._req("GET", "/account/balance")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def products(self, search: "str | None" = None, currency: str = "RUB"):
        path = f"/rental/products?currency={requests.utils.quote(currency)}"
        if search:
            path += f"&search={requests.utils.quote(search)}"
        r = self._req("GET", path)
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                return data if isinstance(data, list) else []
            except Exception:
                return None
        return None

    def calculate_price(self, product_id: int, hours: int):
        body = {"product_id": int(product_id), "hours": int(hours)}
        r = self._req("POST", "/rental/calculate-price", json=body)
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def calculate_extend_price(self, rental_uid: str, hours: int):
        """Стоимость продления конкретной аренды (KOSell API v1.1)."""
        body = {"rental_uid": str(rental_uid), "hours": int(hours)}
        r = self._req("POST", "/rental/calculate-extend-price", json=body)
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def rent(self, product_id: int, hours: int, currency: str,
             idempotency_key: "str | None" = None, verify_before_issue: bool = False):
        """
        Создаёт аренду.
        :param idempotency_key: уникальный ключ (напр. ID заказа FunPay). Повторная
            отправка того же ключа после сетевого сбоя не создаст дубль на KOSell.
        :param verify_before_issue: запросить проверку аккаунта перед выдачей.
        """
        body = {
            "product_id": int(product_id),
            "duration_hours": int(hours),
            "currency": currency,
        }
        if verify_before_issue:
            body["verify_before_issue"] = True
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        r = self._req("POST", "/rental/rent", json=body, headers=headers)
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def credentials(self, uid: str):
        r = self._req("GET", f"/rental/{uid}/credentials")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def code(self, uid: str):
        r = self._req("GET", f"/rental/{uid}/code")
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error") and not data.get("code"):
                    return data, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def extend(self, uid: str, hours: int, currency: str, idempotency_key: "str | None" = None):
        body = {"hours": int(hours), "currency": currency}
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        r = self._req("POST", f"/rental/{uid}/extend", json=body, headers=headers)
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def terminate(self, uid: str):
        """Досрочно завершает аренду (KOSell API v1.1)."""
        r = self._req("POST", f"/rental/{uid}/terminate")
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def rotate_password(self, uid: str):
        """Смена пароля Steam (KOSell API v1.1)."""
        r = self._req("POST", f"/rental/{uid}/rotate-password")
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def rotate_password_status(self, uid: str):
        r = self._req("GET", f"/rental/{uid}/rotate-password/status")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def full_logout(self, uid: str):
        """Полный выход из всех сессий Steam (KOSell API v1.1)."""
        r = self._req("POST", f"/rental/{uid}/full-logout")
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def full_logout_status(self, uid: str):
        r = self._req("GET", f"/rental/{uid}/full-logout/status")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def active(self):
        r = self._req("GET", "/rental/active")
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                return data if isinstance(data, list) else []
            except Exception:
                return None
        return None

    # ---- rental-delivery (официальная система «подготовки выдачи») ----

    def delivery_preferences(self):
        """GET /api/rental-delivery/preferences — включена ли подготовка выдачи."""
        r = self._req("GET", "/api/rental-delivery/preferences")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None

    def delivery_save_preferences(self, enabled: bool):
        """PUT /api/rental-delivery/preferences — вкл/выкл подготовку выдачи."""
        body = {"enabled": bool(enabled)}
        r = self._req("PUT", "/api/rental-delivery/preferences", json=body)
        if r is not None and r.status_code in (200, 201):
            try:
                data = r.json()
                if isinstance(data, dict) and data.get("error"):
                    return None, _kosell_err_code(data, r.status_code)
                return data, None
            except Exception:
                return None, "bad_response"
        return None, self._err_code(r)

    def delivery_pending(self):
        """GET /api/rental-delivery/pending — список аренд в процессе подготовки."""
        r = self._req("GET", "/api/rental-delivery/pending")
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                return data if isinstance(data, list) else []
            except Exception:
                return None
        return None

    def delivery_detail(self, rental_uid: str):
        """GET /api/rental-delivery/{uid} — статус подготовки конкретной аренды."""
        r = self._req("GET", f"/api/rental-delivery/{rental_uid}")
        if r is not None and r.status_code == 200:
            try:
                return r.json()
            except Exception:
                return None
        return None


def _sanitize_key(raw: str) -> str:
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] in "\"'`" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return "".join(ch for ch in s if ch.isprintable() and not ch.isspace())


def _get_api() -> "KOSellAPI | None":
    key = _sanitize_key(SETTINGS.get("api_key", ""))
    if not key:
        return None
    return KOSellAPI(key)


def _dbg(msg: str):
    if SETTINGS.get("ac_debug"):
        logger.info(f"{LP} [AC-DEBUG] {msg}")


def _products(api: "KOSellAPI | None", force: bool = False):
    if api is None:
        return None
    now = time.time()
    if (not force and _products_cache["data"] is not None
            and now - _products_cache["ts"] < AC_CACHE_TTL):
        return _products_cache["data"]
    data = api.products()
    if data is None:
        _products_cache["err"] = True
        _dbg("products(): ошибка API (None) — отдаю прошлый кэш если есть")
        return _products_cache["data"] if _products_cache["data"] is not None else None
    _products_cache.update({"data": data, "ts": now, "err": False})
    _dbg(f"products(): загружено {len(data)} товаров, кэш на {AC_CACHE_TTL//60} мин")
    return data


def _bar(done: int, total: int, width: int = 14) -> str:
    total = max(1, total)
    frac = min(1.0, done / total)
    fill = int(round(frac * width))
    return f"{'█' * fill}{'░' * (width - fill)} {int(frac * 100)}% ({done}/{total})"


def _send(chat_id, text: str):
    if cardinal_instance and chat_id and text:
        try:
            cardinal_instance.send_message(chat_id, text, watermark=False)
        except Exception as e:
            logger.error(f"{LP} send to {chat_id} failed: {e}")


def notify_buyer_problem(chat_id):
    _send(chat_id, get_text("text_problem"))


def _admins():
    if cardinal_instance and cardinal_instance.telegram:
        return list(cardinal_instance.telegram.authorized_users)
    return []


def notify_admins(reason: str, order=None, raw: str = "", extra: str = "", dedup_key: "str | None" = None):
    if dedup_key:
        last = _admin_alert_dedup.get(dedup_key, 0)
        if time.time() - last < 60:
            return
        _admin_alert_dedup[dedup_key] = time.time()

    lines = [f"<b>{LP}</b>", f"⚠️ {_html.escape(reason)}"]
    kb = None
    if order is not None:
        oid = getattr(order, "id", "?")
        buyer = getattr(order, "buyer_username", "?")
        lines.append(f"Покупатель: <b>{_html.escape(str(buyer))}</b>")
        lines.append(f"Заказ: <code>{_html.escape(str(oid))}</code>")
        desc = getattr(order, "description", "") or ""
        if desc:
            lines.append(f"Лот: {_html.escape(desc[:80])}")
        kb = K()
        kb.add(B("🔗 Открыть заказ", url=ORDER_URL.format(order_id=oid)))
    if extra:
        lines.append(_html.escape(extra))
    if raw:
        lines.append(f"<code>{_html.escape(raw[:200])}</code>")
    text = "\n".join(lines)
    if not bot:
        return
    for admin_id in _admins():
        try:
            bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


def notify_admins_info(text: str):
    if not bot:
        return
    for admin_id in _admins():
        try:
            bot.send_message(admin_id, f"{LP} {text}", parse_mode="HTML")
        except Exception:
            pass


_HUMAN_ERR = {
    "no_stock": "Нет свободных аккаунтов на KOSell",
    "insufficient_balance": "Недостаточно средств на балансе KOSell",
    "invalid_api_key": "Неверный API ключ KOSell",
    "account_disabled": "Аккаунт KOSell отключён",
    "rate_limited": "Превышен лимит запросов KOSell",
    "not_found": "Товар/аренда не найдены на KOSell",
    "connection_error": "Нет связи с KOSell",
    "expired": "Аренда истекла",
    "not_active": "Аренда не активна",
    "cooldown": "Операция на кулдауне KOSell",
    "operation_inflight": "Операция уже выполняется на KOSell",
    "verify": "Аккаунт не прошёл проверку перед выдачей",
}


def _human_err(code) -> str:
    norm = _kosell_err_code(code)
    return _HUMAN_ERR.get(norm, f"Ошибка KOSell: {code}")


def _is_no_stock(err) -> bool:
    return _kosell_err_code(err) == "no_stock"


def _hour_chunks(hours: int) -> list[int]:
    try:
        left = int(hours or 0)
    except Exception:
        left = KOSELL_MIN_HOURS
    left = max(KOSELL_MIN_HOURS, left)
    chunks = []
    while left > 0:
        chunk = min(720, left)
        chunks.append(chunk)
        left -= chunk
    return chunks


def _duration_in_desc(hours: int, desc_l: str) -> bool:
    if not desc_l or not hours:
        return False
    for lang in ("ru", "en"):
        t = _format_kostime(int(hours), lang).strip().lower()
        if t and t in desc_l:
            return True
    return False


def _duration_candidates(text: str) -> set[int]:
    low = (text or "").lower()
    out: set[int] = set()
    for n, unit in re.findall(
        r"(?<!\d)(\d{1,4})\s*(час(?:а|ов)?|ч\b|h\b|hour[s]?|день|дня|дней|д\b|сут(?:ки|ок)?|day[s]?|недел[яиюьеь]*|week[s]?|месяц(?:а|ев)?|мес\b|month[s]?)",
        low,
        re.I,
    ):
        try:
            val = int(n)
        except Exception:
            continue
        if val <= 0:
            continue
        u = unit.lower()
        if u.startswith(("час", "ч", "h", "hour")):
            out.add(val)
        elif u.startswith(("д", "сут", "day")):
            out.add(val * 24)
        elif u.startswith(("нед", "week")):
            out.add(val * 168)
        elif u.startswith(("мес", "month")):
            out.add(val * 720)
    if re.search(r"\b(сутки|суток)\b", low):
        out.add(24)
    if re.search(r"\b(неделя|неделю|week)\b", low):
        out.add(168)
    if re.search(r"\b(месяц|month)\b", low):
        out.add(720)
    return out


def _order_title_duration_mismatch(order, hours_unit: int) -> "set[int] | None":
    title = getattr(order, "description", "") or ""
    cands = _duration_candidates(title)
    if cands and int(hours_unit) not in cands:
        return cands
    return None


_RX_RENT_CODE = re.compile(r"\brent\s*:\s*([a-z0-9][a-z0-9_-]{1,48})\b", re.I)


def _make_rent_code(product_id, hours, placement: str = "") -> str:
    try:
        tail = f"-{placement}" if placement else ""
        return f"rent:{int(product_id)}-{int(hours)}{tail}"
    except Exception:
        raw = re.sub(r"[^a-z0-9_-]+", "-", f"{product_id}-{hours}-{placement}".lower()).strip("-")
        return f"rent:{raw or 'unknown'}"


def _short_err(err, limit: int = 220) -> str:
    s = re.sub(r"\s+", " ", str(err or "")).strip()
    return s[:limit] + ("..." if len(s) > limit else "")


def _extract_rent_codes(text: str) -> list[str]:
    seen = []
    for m in _RX_RENT_CODE.finditer(text or ""):
        code = "rent:" + m.group(1).lower()
        if code not in seen:
            seen.append(code)
    return seen


def _mapping_rent_code(mapping: dict) -> str:
    code = (mapping.get("rent_code") or "").strip().lower()
    if code.startswith("rent:"):
        return code
    placement = mapping.get("placement") or "main"
    return _make_rent_code(mapping.get("product_id"), mapping.get("hours"),
                           "" if placement == "main" else placement).lower()


def _mapping_rent_codes(mapping: dict) -> list[str]:
    codes = []
    placement = mapping.get("placement") or "main"
    raw_codes = [
        mapping.get("rent_code"),
        _make_rent_code(mapping.get("product_id"), mapping.get("hours"), "" if placement == "main" else placement),
        f"rent:{mapping.get('lot_id')}" if mapping.get("lot_id") else "",
    ]
    if placement == "main":
        raw_codes.append(_make_rent_code(mapping.get("product_id"), mapping.get("hours")))
    for code in raw_codes:
        code = (code or "").strip().lower()
        if code.startswith("rent:") and code not in codes:
            codes.append(code)
    return codes


def _append_rent_code_full(text: str, code: str) -> str:
    code = (code or "").strip()
    if not code or code.lower() in (text or "").lower():
        return text or ""
    base = (text or "").rstrip()
    return f"{base}\n\n{code}" if base else code


def _strip_rent_code_from_title(text: str) -> str:
    s = str(text or "")
    s = re.sub(r"\s*[\[\(\{]?\s*rent\s*:\s*[a-z0-9][a-z0-9_-]{1,48}\s*[\]\)\}]?", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def _limit_lot_title(text: str, limit: int = FP_SUMMARY_LIMIT) -> str:
    s = _strip_rent_code_from_title(text)
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[:max(1, limit - 3)].rstrip(" .,;:!?|/-_") + "..."
    return s


def _limit_lot_title_keep_tail(text: str, tail: str, limit: int = FP_SUMMARY_LIMIT) -> str:
    s = _strip_rent_code_from_title(text)
    s = re.sub(r"\s+", " ", s).strip()
    tail = re.sub(r"\s+", " ", str(tail or "")).strip()
    if not limit or len(s) <= limit:
        return s
    if not tail:
        return _limit_lot_title(s, limit)
    idx = s.lower().rfind(tail.lower())
    if idx < 0:
        return _limit_lot_title(s, limit)
    head = s[:idx].rstrip(" .,;:!?|/-_")
    tail_part = s[idx:].strip()
    keep = limit - len(tail_part) - 4
    if keep <= 0:
        return _limit_lot_title(tail_part, limit)
    return f"{head[:keep].rstrip(' .,;:!?|/-_')}... {tail_part}".strip()


def _last_duration_text(text: str) -> str:
    matches = list(re.finditer(
        r"(?<!\d)\d{1,4}\s*(?:час(?:а|ов)?|ч\b|h\b|hour[s]?|день|дня|дней|д\b|сут(?:ки|ок)?|day[s]?|недел[яиюьеь]*|week[s]?|месяц(?:а|ев)?|мес\b|month[s]?)",
        str(text or ""),
        re.I,
    ))
    return matches[-1].group(0).strip() if matches else ""


def _sanitize_summary_fields(fields: dict) -> bool:
    changed = False
    if "fields[summary][ru]" in fields:
        old = fields.get("fields[summary][ru]") or ""
        new = _limit_lot_title_keep_tail(old, _last_duration_text(old))
        if new != old:
            fields["fields[summary][ru]"] = new
            changed = True
    if "fields[summary][en]" in fields:
        old = fields.get("fields[summary][en]") or ""
        new = _limit_lot_title_keep_tail(_ascii(_strip_rent_code_from_title(old)), _last_duration_text(old))
        if old and not _en_title_ok(new):
            new = "RENTAL ACCOUNT INSTANT AUTO DELIVERY"
        if new != old:
            fields["fields[summary][en]"] = new
            changed = True
    return changed


def _match_mapping(order, lot_id) -> "dict | None":
    order_text = "\n".join(str(x or "") for x in (
        getattr(order, "description", ""),
        getattr(order, "html", ""),
    ))
    desc_l = order_text.lower()
    subcat = getattr(getattr(order, "subcategory", None), "id", None)

    codes = _extract_rent_codes(order_text)
    if codes:
        code_cands = [m for m in MAPPINGS if any(c in codes for c in _mapping_rent_codes(m))]
        if len(code_cands) == 1:
            _dbg(f"_match_mapping: матч по rent-коду {_mapping_rent_code(code_cands[0])}")
            return code_cands[0]
        if len(code_cands) > 1:
            logger.warning(f"{LP} order {getattr(order, 'id', '?')}: неоднозначный rent-код {codes}, пропуск")
            return None

    if lot_id:
        for m in MAPPINGS:
            if m.get("lot_id") and str(m["lot_id"]) == str(lot_id):
                return m
        logger.info(f"{LP} order {getattr(order, 'id', '?')}: lot_id={lot_id} не привязан к KOSell, пропуск")
        return None

    # Без lot_id нельзя безопасно полагаться на одну подкатегорию: в ней могут
    # продаваться обычные аккаунты, валюты и чужие лоты. Разрешаем только
    # fallback по явному ключевому слову и длительности из названия лота.
    cands = []
    for m in MAPPINGS:
        kw = (m.get("title_keyword") or "").strip().lower()
        if not kw:
            continue
        sc = m.get("subcategory_id")
        if sc and subcat and str(sc) != str(subcat):
            continue
        if kw in desc_l:
            cands.append(m)

    dur_cands = [m for m in cands if _duration_in_desc(int(m.get("hours") or 0), desc_l)]

    if desc_l and dur_cands:
        if len(dur_cands) == 1:
            _dbg(f"_match_mapping: fallback по ключу и длительности (lot_id не найден в событии) -> "
                 f"hours={dur_cands[0].get('hours')} {dur_cands[0].get('product_name')}")
            return dur_cands[0]

    if len(cands) == 1:
        logger.warning(f"{LP} order {getattr(order, 'id', '?')}: lot_id не найден, "
                       f"ключевое слово совпало, но длительность не распознана — пропуск во избежание неверной выдачи")
        return None
    if len(cands) > 1:
        logger.warning(f"{LP} order {getattr(order, 'id', '?')}: неоднозначная привязка "
                       f"(игра найдена, длительность из названия не распознана), пропуск во избежание неверной выдачи")
        return None
    return None


def _buyer_key(chat_id) -> str:
    return str(chat_id)


def _now() -> float:
    return time.time()


def _active_rentals(buyer_key: str) -> list:
    out = []
    for r in RENTALS.get(buyer_key, []):
        exp = r.get("expires_ts", 0)
        if not exp or exp > _now() - 60:
            out.append(r)
    return out


def _add_rental(buyer_key, rec: dict):
    RENTALS.setdefault(buyer_key, []).append(rec)
    save_rentals()


def _cleanup_rentals():
    changed = False
    for bk in list(RENTALS.keys()):
        kept = []
        for r in RENTALS[bk]:
            exp = r.get("expires_ts") or 0
            # exp=0 — дата не распарсилась; аренда может быть живой, не вычищаем.
            if not exp or exp > _now() - 3600:
                kept.append(r)
        if len(kept) != len(RENTALS[bk]):
            RENTALS[bk] = kept
            changed = True
        if not kept:
            del RENTALS[bk]
            changed = True
    if changed:
        save_rentals()


def _friend_active(buyer_key: str) -> bool:
    ts = _friend.get(buyer_key)
    if not ts:
        return False
    if _now() - ts > SETTINGS.get("friend_minutes", 10) * 60:
        _friend.pop(buyer_key, None)
        return False
    return True


def _friend_set(buyer_key: str):
    _friend[buyer_key] = _now()


def _friend_clear(buyer_key: str):
    _friend.pop(buyer_key, None)


def _extend_intent_set(buyer_key: str, login: str):
    if login:
        _extend_intent[buyer_key] = {"login": login, "ts": _now()}


def _extend_intent_clear(buyer_key: str):
    _extend_intent.pop(buyer_key, None)


def _extend_intent_get(buyer_key: str) -> "dict | None":
    intent = _extend_intent.get(buyer_key)
    if not intent:
        return None
    ttl = max(5, int(SETTINGS.get("friend_minutes", 10) or 10)) * 60
    if _now() - float(intent.get("ts") or 0) > ttl:
        _extend_intent_clear(buyer_key)
        return None
    return intent


def _find_rental_by_login(buyer_key: str, login: str, product_id=None) -> "dict | None":
    login_l = (login or "").strip().lower()
    if not login_l:
        return None
    for r in _active_rentals(buyer_key):
        if product_id is not None and str(r.get("product_id")) != str(product_id):
            continue
        if (r.get("login") or "").strip().lower() == login_l:
            return r
    return None


def _maybe_refund(order, force: bool = False, terminate_rental: bool = True):
    """
    Авто-возврат средств FunPay по заказу.

    При возврате дополнительно (если включено auto_refund или force и есть
    привязка к аренде) досрочно завершает аренду на KOSell (/terminate),
    чтобы аккаунт не «тикал» впустую и быстрее освободился.
    """
    if not (force or SETTINGS.get("auto_refund")):
        return False
    oid = getattr(order, "id", None)
    if not oid or not cardinal_instance:
        return False

    # Пробуем завершить аренды KOSell по заказу (если они были выданы).
    # ВАЖНО: при мульти-qty (несколько отдельных аккаунтов на один заказ)
    # завершаем ВСЕ аренды этого заказа, а не только первую.
    if terminate_rental:
        try:
            oid_s = str(oid)
            api = _get_api()
            terminated = 0
            for bk, rentals in list(RENTALS.items()):
                for rental in rentals:
                    if str(rental.get("order_id") or "") == oid_s and rental.get("uid"):
                        if api:
                            t_data, t_err = api.terminate(rental["uid"])
                            if t_err:
                                logger.warning(f"{LP} terminate after refund failed for {rental['uid']}: {t_err}")
                            else:
                                terminated += 1
                                logger.info(f"{LP} rental {rental['uid']} terminated after refund of order {oid}")
            if terminated:
                notify_admins_info(f"🛑 Досрочно завершено аренд по заказу {oid}: {terminated}")
        except Exception as e:
            logger.warning(f"{LP} terminate on refund failed: {e}")

    try:
        cardinal_instance.account.refund(oid)
        notify_admins_info(f"💸 Выполнен авто-возврат по заказу {oid}")
        return True
    except Exception as e:
        notify_admins(f"Не удалось сделать авто-возврат: {e}", order=order, dedup_key=f"refund:{oid}")
        return False


def _order_paid_rub(order) -> "float | None":
    for attr in ("price", "total", "sum", "amount_rub"):
        raw = getattr(order, attr, None)
        if raw in (None, ""):
            continue
        try:
            paid = float(str(raw).replace(" ", "").replace(",", "."))
            return paid if paid > 0 else None
        except Exception:
            continue
    return None


def _kosell_required_cost_rub(api: "KOSellAPI", product_id, hours: int,
                              rental_uid: "str | None" = None) -> "float | None":
    """
    Себестоимость KOSell в рублях.

    Если передан rental_uid — считает по отдельному эндпоинту
    /rental/calculate-extend-price (у продления своя цена/скидка),
    иначе — /rental/calculate-price (новая аренда).
    """
    # Продление конкретной аренды: свой прайсинг в API v1.1
    if rental_uid:
        try:
            data = api.calculate_extend_price(rental_uid, int(hours))
            if isinstance(data, dict):
                cost = data.get("total_rub")
                if cost is None:
                    cost = data.get("total_usd")
                    if cost is not None:
                        try:
                            rate = float(data.get("usd_rub_rate") or 0)
                            cost = float(cost) * (rate if rate > 0 else SETTINGS.get("ac_usd_rub", 95.0))
                        except Exception:
                            cost = None
                if cost is not None:
                    cost = float(cost)
                    return cost if cost > 0 else None
        except Exception:
            pass

    if not rental_uid:
        try:
            data = api.calculate_price(int(product_id), int(hours))
            if isinstance(data, dict) and data.get("total_rub") is not None:
                cost = float(data["total_rub"])
                return cost if cost > 0 else None
        except Exception:
            pass
    else:
        # У продления нет product_id-расчёта; пробуем обычный как фолбэк только
        # если product_id доступен (старое поведение)
        try:
            data = api.calculate_price(int(product_id), int(hours))
            if isinstance(data, dict) and data.get("total_rub") is not None:
                cost = float(data["total_rub"])
                return cost if cost > 0 else None
        except Exception:
            pass

    prod = None
    try:
        pid = int(product_id)
        prod = next((p for p in (_products(api) or []) if int(p.get("id") or 0) == pid), None)
    except Exception:
        prod = None
    if not prod:
        return None
    try:
        cost = float(_kosell_cost_rub(api, prod, int(hours)))
        return cost if cost > 0 else None
    except Exception:
        return None


def _price_guard_ok(api: "KOSellAPI", mapping: dict, order, hours: int, chat_id,
                    count: int = 1, rental_uid: "str | None" = None) -> bool:
    if not SETTINGS.get("price_guard_enabled", True):
        return True

    paid = _order_paid_rub(order)
    if paid is None:
        logger.warning(f"{LP} order {getattr(order, 'id', '?')}: не удалось определить цену заказа, price_guard пропущен")
        return True

    unit_cost = _kosell_required_cost_rub(api, mapping.get("product_id"), hours, rental_uid=rental_uid)
    if unit_cost is None:
        logger.warning(f"{LP} order {getattr(order, 'id', '?')}: не удалось определить себестоимость KOSell, price_guard пропущен")
        return True

    try:
        margin = max(0.0, float(SETTINGS.get("price_guard_margin_rub", 0) or 0))
    except Exception:
        margin = 0.0
    count = max(1, int(count or 1))
    cost = round(unit_cost * count, 2)
    required = round(cost + margin, 2)
    if paid + 1e-9 >= required:
        return True

    notify_buyer_problem(chat_id)
    notify_admins(
        f"Защита от минусовой цены: заказ оплачен на {_money(paid)}₽, "
        f"себестоимость KOSell {_money(cost)}₽"
        + (f" + запас {_money(margin)}₽" if margin > 0 else "")
        + f". Выдача остановлена.",
        order=order,
        extra=f"product_id={mapping.get('product_id')} hours={hours} count={count} required={_money(required)}₽",
        dedup_key=f"price_guard:{getattr(order, 'id', '?')}",
    )
    _maybe_refund(order)
    return False


def _rotate_password_and_logout(api, uid: str, login: str, order, chat_id=None,
                                notify_fail: bool = True) -> tuple:
    """
    Выполняет подготовку аккаунта перед выдачей новому покупателю:
    1. (опционально) полный разлогин всех сессий Steam;
    2. (опционально) смена пароля.

    :return: (ok, password, pending)
        password — актуален ПОСЛЕ смены пароля (если смена включена и завершилась);
        pending  — True, если смена пароля запущена, но новый пароль ещё не готов:
                   выдачу слать БЕЗ пароля (старый уже недействителен/скоро станет),
                   новый пароль догоняем в фоне и пришлём покупателю отдельно.
    """
    password = ""
    pending = False

    if SETTINGS.get("full_logout_on_delivery"):
        data, err = api.full_logout(uid)
        if err or not data:
            if notify_fail:
                notify_admins(f"Не удалось выполнить полный разлогин Steam для {login} "
                              f"(uid={uid}): {_human_err(err or 'unknown')}",
                              order=order, raw=str(err), dedup_key=f"logout:{uid}")
        else:
            # Ждём завершения (статус), т.к. операция асинхронная
            if SETTINGS.get("wait_rotate_password", True):
                try:
                    for _ in range(30):
                        st = api.full_logout_status(uid)
                        if st is None:
                            break
                        done = (st.get("done") is True or st.get("status") == "done"
                                or (st.get("ready") is True))
                        if done:
                            break
                        time.sleep(2)
                except Exception as e:
                    logger.warning(f"{LP} full_logout wait failed for {uid}: {e}")

    if SETTINGS.get("rotate_password_on_delivery"):
        data, err = api.rotate_password(uid)
        if err or not data:
            if notify_fail:
                notify_admins(f"Не удалось сменить пароль для {login} (uid={uid}): {_human_err(err or 'unknown')}",
                              order=order, raw=str(err), dedup_key=f"rotate:{uid}")
        else:
            rotate_err = False
            if SETTINGS.get("wait_rotate_password", True):
                # Ждём новый пароль (до ~90с). Если пришёл — отдаём его сразу.
                try:
                    for _ in range(45):
                        st = api.rotate_password_status(uid)
                        if st is None:
                            break
                        p = (st.get("new_password") or st.get("password") or "")
                        if p:
                            password = p
                            break
                        if st.get("done") is True or st.get("status") == "done":
                            break
                        time.sleep(2)
                except Exception as e:
                    logger.warning(f"{LP} rotate wait failed for {uid}: {e}")
            else:
                # Ждать не просили — быстрая проверка, вдруг пароль уже готов
                try:
                    st = api.rotate_password_status(uid)
                    if st:
                        password = (st.get("new_password") or st.get("password") or "")
                except Exception:
                    password = ""
            if not password:
                # Финальная попытка прочитать пароль из status
                try:
                    st = api.rotate_password_status(uid)
                    if st:
                        password = (st.get("new_password") or st.get("password") or "")
                except Exception:
                    password = ""
                if not password:
                    # Ротация запущена, но нового пароля нет: старый не отдаём —
                    # запускаем фоновый догон пароля и помечаем выдачу pending.
                    pending = True
                    _schedule_password_followup(uid, login, chat_id,
                                                getattr(order, "id", None), notify_fail=notify_fail)
    return True, password, pending


def _schedule_password_followup(uid: str, login: str, chat_id, order_id, notify_fail: bool = True) -> None:
    """Фоновый догон нового пароля после rotate-password (максимум ~10 минут)."""
    try:
        api_key = str(SETTINGS.get("api_key", "") or "").strip()
        threading.Thread(target=_password_followup_worker,
                         args=(api_key, uid, login, chat_id, order_id, notify_fail),
                         daemon=True).start()
    except Exception as e:
        logger.error(f"{LP} password followup schedule failed: {e}")


def _password_followup_worker(api_key: str, uid: str, login: str, chat_id, order_id, notify_fail: bool = True) -> None:
    try:
        if not api_key:
            return
        api = KOSellAPI(api_key)
        deadline = time.time() + 600  # 10 минут
        while time.time() < deadline:
            time.sleep(5)
            try:
                st = api.rotate_password_status(uid)
                if not st:
                    continue
                p = (st.get("new_password") or st.get("password") or "")
                if p:
                    _password_followup_deliver(uid, login, chat_id, p)
                    return
            except Exception:
                continue
        if notify_fail:
            notify_admins(f"Не удалось получить новый пароль после смены для {login} "
                          f"(uid={uid}) за 10 минут — проверьте вручную статус ротации.",
                          extra=f"order_id={order_id}")
    except Exception as e:
        logger.warning(f"{LP} password followup failed for {uid}: {e}")


def _password_followup_deliver(uid: str, login: str, chat_id, password: str) -> None:
    game = ""
    changed = False
    try:
        for bk, rentals in list(RENTALS.items()):
            for r in rentals:
                if str(r.get("uid")) == str(uid):
                    r["password"] = password
                    r.pop("password_pending", None)
                    game = r.get("product_name") or game
                    changed = True
    except Exception:
        pass
    if changed:
        save_rentals()
    if chat_id and password:
        _send(chat_id, fmt("text_password_changed",
                           game=game or "аккаунт", login=login, password=password))


def _do_new_rental(api, mapping, chat_id, buyer_name, order, hours: int, notify_fail: bool = True,
                   idem_suffix: "int | None" = None):
    product_id = mapping["product_id"]
    currency = mapping.get("currency") or SETTINGS.get("currency", "RUB")
    game = mapping.get("product_name") or "аккаунт"

    chunks = _hour_chunks(hours)
    requested_hours = sum(chunks)
    # Идемпотентность: при сетевом сбое повторный запрос с тем же ключом
    # не создаст дубль аренды на KOSell (API v1.1). Для мульти-заказов
    # (qty > 1, отдельные аккаунты) каждый аккаунт получает свой суффикс,
    # иначе все повторы вернули бы один и тот же rental_uid.
    idem = None
    try:
        oid = str(getattr(order, "id", "") or "").strip()
        if oid:
            idem = f"fp-{oid}" if idem_suffix is None else f"fp-{oid}-{int(idem_suffix)}"
    except Exception:
        idem = None
    data, err = api.rent(product_id, chunks[0], currency, idempotency_key=idem)
    if err or not data:
        if _kosell_err_code(err) == "insufficient_balance":
            _balance_alerted["low"] = True
        if notify_fail:
            notify_buyer_problem(chat_id)
            notify_admins(f"Не удалось выдать аккаунт: {_human_err(err or 'unknown')}",
                          order=order, raw=str(err), dedup_key=f"rent:{err}")
            _maybe_refund(order, force=_is_no_stock(err))
        return False, err

    uid = data.get("rental_uid")
    login = data.get("steam_login", "")
    expires_raw = data.get("expires_at")
    delivered_hours = chunks[0]
    # Каждый дозаказ часов идемпотентен: повторный запрос с тем же ключом
    # после сетевого сбоя не продлит аренду дважды.
    for i, chunk in enumerate(chunks[1:], 1):
        ext_idem = None
        try:
            oid = str(getattr(order, "id", "") or "").strip()
            if oid:
                ext_idem = f"fp-ext-{uid}-{i}" if idem_suffix is None else f"fp-ext-{uid}-{i}-{int(idem_suffix)}"
        except Exception:
            ext_idem = None
        ext_data, ext_err = api.extend(uid, chunk, currency, idempotency_key=ext_idem)
        if ext_err or not ext_data:
            if _kosell_err_code(ext_err) == "insufficient_balance":
                _balance_alerted["low"] = True
            notify_admins(f"Аккаунт выдан, но не удалось докинуть всё время: "
                          f"{delivered_hours}/{requested_hours} ч ({_human_err(ext_err or 'unknown')})",
                          order=order, extra=f"uid={uid} login={login}", raw=str(ext_err),
                          dedup_key=f"rent_partial:{uid}:{ext_err}")
            break
        delivered_hours += chunk
        expires_raw = ext_data.get("new_expires_at") or expires_raw

    # Подготовка к выдаче: полный разлогин и/или смена пароля (API v1.1)
    # Уведомляем покупателя отдельным сообщением, если пароль менялся.
    rotated_password = ""
    password_pending = False
    if SETTINGS.get("rotate_password_on_delivery") or SETTINGS.get("full_logout_on_delivery"):
        _ok, rotated_password, password_pending = _rotate_password_and_logout(
            api, uid, login, order, chat_id=chat_id, notify_fail=notify_fail)

    password = ""
    if not password_pending:
        creds = api.credentials(uid)
        if creds:
            password = creds.get("steam_password", "")
    # Если пароль менялся — отдаём покупателю новый, а не из credentials
    if rotated_password:
        password = rotated_password
    if not password and not password_pending:
        notify_admins("Аренда создана, но не удалось получить пароль (credentials)",
                      order=order, extra=f"uid={uid} login={login}", dedup_key=f"cred:{uid}")

    rec = {
        "uid": uid,
        "product_id": product_id,
        "product_name": game,
        "login": login,
        "hours": delivered_hours,
        "expires_raw": expires_raw,
        "expires_ts": _parse_iso(expires_raw),
        "lot_id": mapping.get("lot_id"),
        "order_id": getattr(order, "id", None),
        "chat_id": chat_id,
        "created_at": _now(),
        "review_bonus_given": False,
    }
    if password_pending:
        rec["password_pending"] = True
    _add_rental(_buyer_key(chat_id), rec)
    cost_rub = _kosell_required_cost_rub(api, product_id, delivered_hours)
    _stats_add_order(order, product_id, game)
    _stats_add_kosell(product_id, game, delivered_hours, cost_rub, "rental")

    if password_pending:
        # Ротация пароля ещё не завершилась: выдачу шлём без пароля —
        # старый уже недействителен/скоро станет. Новый догоним в фоне.
        _send(chat_id, fmt("text_delivery_pending", login=login, game=game,
                           hours=delivered_hours, expires=_fmt_expires(expires_raw)))
    else:
        _send(chat_id, fmt("text_delivery", login=login, password=password, game=game,
                           hours=delivered_hours, expires=_fmt_expires(expires_raw)))
    # Если пароль менялся при выдаче — отдельно уведомляем покупателя
    if rotated_password:
        _send(chat_id, fmt("text_password_changed", game=game, login=login, password=password))
    if SETTINGS.get("notify_sales"):
        paid_rub = _order_paid_rub(order)
        profit_rub = None if paid_rub is None or cost_rub is None else paid_rub - cost_rub
        notify_admins_info(
            "✅ Заказ создан!\n\n"
            f"🆔 ID коселл: {_html.escape(str(uid or '—'))}\n"
            f"👤 Покупатель: {_html.escape(str(getattr(order, 'buyer_username', '?') or '?'))}\n"
            f"💰 Цена заказа: {_money_dash(paid_rub)}₽\n"
            f"💸 Потрачено на API: {_money_dash(cost_rub)}₽\n"
            f"💎 Чистая прибыль: {_money_dash(profit_rub)}₽"
        )
    return True, None


def _do_extend(api, mapping, chat_id, rental, order, hours: int):
    currency = mapping.get("currency") or SETTINGS.get("currency", "RUB")
    game = mapping.get("product_name") or rental.get("product_name") or "аккаунт"
    chunks = _hour_chunks(hours)
    requested_hours = sum(chunks)
    applied_hours = 0
    new_exp = rental.get("expires_raw")
    oid = str(getattr(order, "id", "") or "").strip()
    for i, chunk in enumerate(chunks, 1):
        # Идемпотентность продления: при сетевом сбое повторный запрос с тем же
        # ключом не начислит часы дважды (KOSell API v1.1).
        ext_idem = f"fp-ext-{rental.get('uid')}-{i}-{oid}" if oid else None
        data, err = api.extend(rental["uid"], chunk, currency, idempotency_key=ext_idem)
        if err or not data:
            notify_buyer_problem(chat_id)
            if _kosell_err_code(err) == "insufficient_balance":
                _balance_alerted["low"] = True
            if applied_hours:
                notify_admins(f"Продление применено частично: {applied_hours}/{requested_hours} ч "
                              f"({_human_err(err or 'unknown')})",
                              order=order, extra=f"uid={rental['uid']}", raw=str(err), dedup_key=f"ext_partial:{err}")
                break
            notify_admins(f"Не удалось продлить аренду: {_human_err(err or 'unknown')}",
                          order=order, extra=f"uid={rental['uid']}", raw=str(err), dedup_key=f"ext:{err}")
            _maybe_refund(order, force=_is_no_stock(err))
            return False
        applied_hours += chunk
        new_exp = data.get("new_expires_at") or new_exp
    rental["expires_raw"] = new_exp
    rental["expires_ts"] = _parse_iso(new_exp)
    save_rentals()
    # Для продления используем отдельный расчёт стоимости (API v1.1):
    # у продления своя цена/скидка, отличная от новой аренды.
    cost_rub = _kosell_required_cost_rub(api, mapping.get("product_id"), applied_hours,
                                         rental_uid=rental.get("uid"))
    _stats_add_order(order, mapping.get("product_id"), game)
    _stats_add_kosell(mapping.get("product_id"), game, applied_hours, cost_rub, "extension")
    _send(chat_id, fmt("text_extension", login=rental.get("login", ""), game=game,
                       hours=applied_hours, expires=_fmt_expires(new_exp)))
    if SETTINGS.get("notify_sales"):
        notify_admins_info(f"✅ Продлён {rental.get('login')} (+{hours} ч), заказ {getattr(order, 'id', '?')}")
    return True


def _order_snapshot(order) -> dict:
    return {
        "id": str(getattr(order, "id", "") or ""),
        "buyer_username": getattr(order, "buyer_username", "") or "",
        "chat_id": getattr(order, "chat_id", None),
        "description": getattr(order, "description", "") or "",
        "price": getattr(order, "price", None),
        "amount": getattr(order, "amount", None),
        "html": getattr(order, "html", "") or "",
    }


def _order_from_snapshot(data: dict):
    data = data or {}
    return type("O", (), {
        "id": data.get("id"),
        "buyer_username": data.get("buyer_username", ""),
        "chat_id": data.get("chat_id"),
        "description": data.get("description", ""),
        "price": data.get("price"),
        "amount": data.get("amount"),
        "html": data.get("html", ""),
    })()


def _qty_ttl_sec() -> int:
    try:
        mins = int(SETTINGS.get("multi_qty_confirm_ttl_min", 30) or 30)
    except Exception:
        mins = 30
    return max(1, mins) * 60


def _pending_qty_for_buyer(buyer_key: str) -> "dict | None":
    _expire_pending_qty_orders()
    cands = [p for p in PENDING_QTY.values() if str(p.get("buyer_key")) == str(buyer_key)]
    if not cands:
        return None
    return sorted(cands, key=lambda p: float(p.get("ts") or 0), reverse=True)[0]


def _pending_qty_remove(order_id) -> None:
    oid = str(order_id or "")
    if oid and oid in PENDING_QTY:
        PENDING_QTY.pop(oid, None)
        save_pending_qty()


def _pending_qty_put(order, mapping: dict, lot_id, chat_id, buyer_name: str,
                     buyer_key: str, qty: int, hours_unit: int, friend_mode: bool) -> None:
    oid = str(getattr(order, "id", "") or "")
    if not oid:
        return
    PENDING_QTY[oid] = {
        "order_id": oid,
        "ts": _now(),
        "buyer_key": str(buyer_key),
        "chat_id": chat_id,
        "buyer_name": buyer_name or "",
        "lot_id": lot_id,
        "mapping": dict(mapping or {}),
        "qty": int(qty),
        "hours_unit": int(hours_unit),
        "friend_mode": bool(friend_mode),
        "order": _order_snapshot(order),
    }
    save_pending_qty()
    _send(chat_id, fmt("text_qty_choice",
                       qty=qty,
                       game=mapping.get("product_name") or mapping.get("title_keyword") or "аккаунт",
                       hours_unit=hours_unit,
                       total_hours=hours_unit * qty))


_QTY_YES = {"да", "д", "yes", "y", "+", "ага", "нужны", "нужно", "отдельные", "несколько"}
_QTY_NO = {"нет", "не", "no", "n", "-", "один", "одно", "1"}


def _qty_answer(text: str) -> "str | None":
    clean = re.sub(r"[^\w+\- ]+", " ", (text or "").lower(), flags=re.I)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return None
    first = clean.split()[0]
    if clean in _QTY_YES or first in _QTY_YES:
        return "separate"
    if clean in _QTY_NO or first in _QTY_NO:
        return "combined"
    return None


def _execute_separate_order(api, mapping: dict, chat_id, buyer_name, order,
                            hours_unit: int, count: int, buyer_key: str, force_friend: bool = False) -> bool:
    count = max(1, int(count or 1))
    hours_unit = max(KOSELL_MIN_HOURS, int(hours_unit or KOSELL_MIN_HOURS))
    if not _price_guard_ok(api, mapping, order, hours_unit, chat_id, count=count):
        return True
    if force_friend:
        _friend_clear(buyer_key)

    delivered = 0
    last_err = None
    for idx in range(count):
        ok, err = _do_new_rental(api, mapping, chat_id, buyer_name, order, hours_unit,
                                 notify_fail=False, idem_suffix=idx)
        if ok:
            delivered += 1
            continue
        last_err = err
        break

    if delivered == count:
        return True
    if delivered:
        _send(chat_id, fmt("text_partial", delivered=delivered, ordered=count))
        notify_admins(f"Мульти-qty: выдано только {delivered}/{count} отдельных аккаунтов "
                      f"по {hours_unit} ч ({_human_err(last_err or 'unknown')})",
                      order=order, raw=str(last_err), dedup_key=f"qty_partial:{getattr(order, 'id', '?')}")
        return True

    notify_buyer_problem(chat_id)
    notify_admins(f"Мульти-qty: не удалось выдать ни одного аккаунта из {count} "
                  f"по {hours_unit} ч ({_human_err(last_err or 'unknown')})",
                  order=order, raw=str(last_err), dedup_key=f"qty_fail:{getattr(order, 'id', '?')}")
    _maybe_refund(order, force=_is_no_stock(last_err))
    return True


def _execute_combined_order(api, mapping: dict, chat_id, buyer_name, order, total_hours: int,
                            buyer_key: str, force_friend: bool = False,
                            explicit_login: "str | None" = None) -> bool:
    total_hours = max(KOSELL_MIN_HOURS, int(total_hours or KOSELL_MIN_HOURS))

    if explicit_login:
        target = _find_rental_by_login(buyer_key, explicit_login, mapping.get("product_id"))
        if not target:
            _send(chat_id, get_text("text_extend_no_pending"))
            return False
        # Продление конкретной аренды: себестоимость считаем по extend-цене
        if not _price_guard_ok(api, mapping, order, total_hours, chat_id, rental_uid=target.get("uid")):
            return True
        _friend_clear(buyer_key)
        _extend_intent_clear(buyer_key)
        _do_extend(api, mapping, chat_id, target, order, total_hours)
        return True

    existing = [r for r in _active_rentals(buyer_key) if str(r.get("product_id")) == str(mapping["product_id"])]

    intent = _extend_intent_get(buyer_key)
    if intent:
        target = _find_rental_by_login(buyer_key, intent.get("login", ""), mapping["product_id"])
        if target:
            if not _price_guard_ok(api, mapping, order, total_hours, chat_id, rental_uid=target.get("uid")):
                return True
            _friend_clear(buyer_key)
            _extend_intent_clear(buyer_key)
            _do_extend(api, mapping, chat_id, target, order, total_hours)
            return True

    if force_friend or _friend_active(buyer_key):
        if not _price_guard_ok(api, mapping, order, total_hours, chat_id):
            return True
        _friend_clear(buyer_key)
        ok, err = _do_new_rental(api, mapping, chat_id, buyer_name, order, total_hours, notify_fail=False)
        if not ok:
            notify_buyer_problem(chat_id)
            notify_admins(f"Режим друга: не удалось выдать новый аккаунт на {total_hours} ч "
                          f"({_human_err(err or 'unknown')})",
                          order=order, raw=str(err), dedup_key=f"friend:{err}")
            _maybe_refund(order, force=_is_no_stock(err))
        return True

    if len(existing) == 0:
        if not _price_guard_ok(api, mapping, order, total_hours, chat_id):
            return True
        _do_new_rental(api, mapping, chat_id, buyer_name, order, total_hours)
    elif len(existing) == 1:
        # Продление: цена считается по extend-эндпоинту
        if not _price_guard_ok(api, mapping, order, total_hours, chat_id, rental_uid=existing[0].get("uid")):
            return True
        _do_extend(api, mapping, chat_id, existing[0], order, total_hours)
    else:
        logins = [r.get("login", "") for r in existing]
        _pending_ext[buyer_key] = {
            "hours": total_hours,
            "order_id": order.id,
            "product_id": mapping["product_id"],
            "allowed_logins": logins,
            "ts": _now(),
        }
        _send(chat_id, fmt("text_ask_which_account",
                           game=mapping.get("product_name") or "аккаунт",
                           logins="\n".join(f"• {l}" for l in logins)))
    return True


def _apply_pending_qty(pending: dict, decision: str, explicit_login: "str | None" = None) -> bool:
    api = _get_api()
    order = _order_from_snapshot(pending.get("order") or {})
    mapping = pending.get("mapping") or {}
    chat_id = pending.get("chat_id") or getattr(order, "chat_id", None)
    buyer_name = pending.get("buyer_name") or getattr(order, "buyer_username", "")
    buyer_key = str(pending.get("buyer_key") or _buyer_key(chat_id))
    qty = max(1, int(pending.get("qty") or 1))
    hours_unit = max(KOSELL_MIN_HOURS, int(pending.get("hours_unit") or KOSELL_MIN_HOURS))

    if not api:
        notify_buyer_problem(chat_id)
        notify_admins("Заказ ожидал выбора qty, но API ключ KOSell не задан — выдача невозможна",
                      order=order, dedup_key=f"qty_no_api:{getattr(order, 'id', '?')}")
        _pending_qty_remove(pending.get("order_id"))
        return True

    if decision == "combined" and explicit_login:
        target = _find_rental_by_login(buyer_key, explicit_login, mapping.get("product_id"))
        if not target:
            _send(chat_id, get_text("text_extend_no_pending"))
            return False

    _pending_qty_remove(pending.get("order_id"))
    if decision == "separate":
        return _execute_separate_order(api, mapping, chat_id, buyer_name, order,
                                       hours_unit, qty, buyer_key,
                                       force_friend=bool(pending.get("friend_mode")))
    return _execute_combined_order(api, mapping, chat_id, buyer_name, order,
                                   hours_unit * qty, buyer_key,
                                   force_friend=bool(pending.get("friend_mode")),
                                   explicit_login=explicit_login)


def _expire_pending_qty_orders(silent: bool = False) -> None:
    if not PENDING_QTY:
        return
    now = _now()
    ttl = _qty_ttl_sec()
    expired = []
    for oid, pending in list(PENDING_QTY.items()):
        try:
            ts = float(pending.get("ts") or 0)
        except Exception:
            ts = 0
        if now - ts > ttl:
            expired.append((oid, pending))
    if not expired:
        return
    for oid, pending in expired:
        PENDING_QTY.pop(oid, None)
    save_pending_qty()
    if silent:
        return
    for oid, pending in expired:
        order = _order_from_snapshot(pending.get("order") or {"id": oid})
        notify_admins(fmt("text_qty_timeout_admin",
                          qty=pending.get("qty", "?"),
                          game=(pending.get("mapping") or {}).get("product_name") or "аккаунт"),
                      order=order,
                      extra=f"hours_unit={pending.get('hours_unit')} order_id={oid}",
                      dedup_key=f"qty_timeout:{oid}")


def _process_order(cardinal: "Cardinal", order, lot_id):
    api = _get_api()
    if not api:
        notify_admins("Заказ получен, но API ключ KOSell не задан — выдача невозможна", order=order,
                      dedup_key="no_api")
        notify_buyer_problem(order.chat_id)
        _maybe_refund(order)
        return

    mapping = _match_mapping(order, lot_id)
    if not mapping:
        logger.info(f"{LP} order {order.id}: нет привязки (lot_id={lot_id})")
        return

    buyer_name = order.buyer_username or ""
    chat_obj = cardinal.account.get_chat_by_name(buyer_name, make_request=True)
    chat_id = chat_obj.id if chat_obj else order.chat_id

    try:
        qty = max(1, int(order.amount or 1))
    except Exception:
        qty = 1
    try:
        hours_unit = max(KOSELL_MIN_HOURS, int(mapping.get("hours") or 1))
    except Exception:
        hours_unit = KOSELL_MIN_HOURS
    buyer_key = _buyer_key(chat_id)

    logger.info(f"{LP} order {order.id} matched product={mapping.get('product_id')} qty={qty} hours_unit={hours_unit}")

    total_hours = hours_unit * qty
    mismatch = _order_title_duration_mismatch(order, hours_unit)
    if mismatch:
        got = ", ".join(f"{h}ч" for h in sorted(mismatch))
        notify_buyer_problem(chat_id)
        notify_admins(f"Срок лота не совпал с привязкой: в названии похоже {got}, "
                      f"а привязка даёт {hours_unit}ч. Выдача остановлена.",
                      order=order, dedup_key=f"duration_mismatch:{order.id}")
        _maybe_refund(order)
        return

    intent = _extend_intent_get(buyer_key)
    explicit_target = None
    if intent:
        explicit_target = _find_rental_by_login(buyer_key, intent.get("login", ""), mapping["product_id"])

    if (qty > 1 and SETTINGS.get("multi_qty_confirm_enabled", True)
            and explicit_target is None):
        friend_mode = bool(_friend_active(buyer_key))
        _pending_qty_put(order, mapping, lot_id, chat_id, buyer_name, buyer_key,
                         qty, hours_unit, friend_mode)
        return

    _execute_combined_order(api, mapping, chat_id, buyer_name, order, total_hours, buyer_key)


def on_new_order(cardinal: "Cardinal", event: NewOrderEvent):
    if not SETTINGS.get("enabled"):
        return
    order = event.order
    try:
        with _lock:
            if order.id in HANDLED:
                return
            HANDLED.append(order.id)
            save_handled()
        lot_id = getattr(event, "lot_id", None)
        if not lot_id:
            m = re.search(r'/(?:lots|chips)/offer\?id=(\d+)', getattr(order, "html", "") or "")
            lot_id = m.group(1) if m else None
        _process_order(cardinal, order, lot_id)
    except Exception as e:
        logger.error(f"{LP} on_new_order error: {e}", exc_info=True)
        try:
            notify_buyer_problem(order.chat_id)
            notify_admins("Внутренняя ошибка при обработке заказа", order=order, raw=str(e))
        except Exception:
            pass


def _cmd_friend(chat_id, buyer_key):
    if _friend_active(buyer_key):
        _send(chat_id, get_text("text_friend_already"))
        return
    _extend_intent_clear(buyer_key)
    _friend_set(buyer_key)
    _send(chat_id, fmt("text_friend_activated", minutes=SETTINGS.get("friend_minutes", 10)))


def _cmd_code(chat_id, buyer_key, arg):
    api = _get_api()
    if not api:
        _send(chat_id, get_text("text_code_not_found"))
        return
    rentals = _active_rentals(buyer_key)
    if not rentals:
        _send(chat_id, get_text("text_code_not_found"))
        return
    target = None
    if arg:
        for r in rentals:
            if (r.get("login") or "").lower() == arg.lower():
                target = r
                break
        if not target:
            _send(chat_id, get_text("text_code_not_found"))
            return
    else:
        if len(rentals) == 1:
            target = rentals[0]
        else:
            logins = "\n".join(f"• {r.get('login', '')}" for r in rentals)
            _send(chat_id, fmt("text_code_ask_login", logins=logins))
            return
    data, err = api.code(target["uid"])
    # API v1.1: формат SteamCodeInfo {success, code, expires_in, error}
    if err or not data or not data.get("code") or data.get("success") is False:
        reason = err or (data or {}).get("error")
        _send(chat_id, get_text("text_code_not_found"))
        notify_admins(f"Не удалось получить Steam Guard код{f': {reason}' if reason else ''}",
                      extra=f"uid={target['uid']} login={target.get('login')}",
                      dedup_key=f"code:{target['uid']}")
        return
    try:
        ttl = int(data.get("expires_in", 30) or 30)
    except Exception:
        ttl = 30
    if ttl < 5:
        _send(chat_id, "⏳ Секунду, готовлю свежий код…")
        time.sleep(ttl + 1)
        fresh, ferr = api.code(target["uid"])
        if not ferr and fresh and fresh.get("code") and fresh.get("success") is not False:
            data = fresh
            try:
                ttl = int(data.get("expires_in", 30) or 30)
            except Exception:
                ttl = 30
    _send(chat_id, fmt("text_code", login=target.get("login", ""),
                       code=data.get("code"), ttl=ttl))


def _cmd_extend(chat_id, buyer_key, arg, buyer_name):
    last = _ext_last.get(buyer_key, 0)
    if _now() - last < SETTINGS.get("extend_cooldown", 30):
        return
    _friend_clear(buyer_key)
    pending = _pending_ext.get(buyer_key)
    if not arg:
        _send(chat_id, get_text("text_extend_no_pending"))
        return
    if not pending:
        target = _find_rental_by_login(buyer_key, arg)
        if not target:
            _send(chat_id, get_text("text_extend_no_pending"))
            return
        _extend_intent_set(buyer_key, target.get("login") or arg)
        _send(chat_id, f"✅ Ок, следующий оплаченный лот этой игры продлю для {target.get('login') or arg}.")
        return
    if arg.lower() not in [l.lower() for l in pending.get("allowed_logins", [])]:
        _send(chat_id, get_text("text_extend_no_pending"))
        return
    rentals = _active_rentals(buyer_key)
    target = None
    for r in rentals:
        if (r.get("login") or "").lower() == arg.lower():
            target = r
            break
    if not target:
        _send(chat_id, get_text("text_extend_no_pending"))
        return
    api = _get_api()
    if not api:
        notify_buyer_problem(chat_id)
        return
    _ext_last[buyer_key] = _now()
    mapping = next((m for m in MAPPINGS if str(m.get("product_id")) == str(pending["product_id"])), {})
    fake_order = type("O", (), {"id": pending.get("order_id"), "buyer_username": buyer_name,
                                "chat_id": chat_id, "description": ""})()
    if _do_extend(api, mapping or {"product_id": pending["product_id"]}, chat_id, target, fake_order, pending["hours"]):
        _pending_ext.pop(buyer_key, None)
        _extend_intent_clear(buyer_key)


def _handle_buyer_text(chat_id, text: str, buyer_name=None):
    if not SETTINGS.get("enabled"):
        return
    text = (text or "").strip()
    if not text:
        return
    low = text.lower()
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    buyer_key = _buyer_key(chat_id)

    # Обновляем метку «горячести»: покупатель недавно писал — получает ETA-уведомления
    try:
        for r in RENTALS.get(buyer_key, []):
            r["last_activity_ts"] = _now()
    except Exception:
        pass

    if _fp_cmd_seen(chat_id, text):
        logger.debug(f"{LP} command duplicate skipped: chat={chat_id} text={low[:80]!r}")
        return

    arg = parts[1].strip() if len(parts) > 1 else None

    pending_qty = _pending_qty_for_buyer(buyer_key)
    if pending_qty:
        answer = _qty_answer(low)
        if answer:
            _apply_pending_qty(pending_qty, answer)
            return
        if cmd in ("!продлить", "!prod", "!extend") and arg:
            _apply_pending_qty(pending_qty, "combined", explicit_login=arg)
            return
        if cmd not in ("!friend", "!друг", "!кодстим", "!code", "!продлить", "!prod", "!extend"):
            return

    if cmd not in ("!friend", "!друг", "!кодстим", "!code", "!продлить", "!prod", "!extend"):
        return

    if cmd in ("!friend", "!друг"):
        _cmd_friend(chat_id, buyer_key)
    elif cmd in ("!кодстим", "!code"):
        _cmd_code(chat_id, buyer_key, arg)
    elif cmd in ("!продлить", "!prod", "!extend"):
        _cmd_extend(chat_id, buyer_key, arg, buyer_name)


_RX_REVIEW_GIVEN = re.compile(
    r'(Покупатель|The buyer).+(оставил отзыв|написал отзыв|изменил отзыв|has given feedback|has changed feedback)',
    re.IGNORECASE)
_RX_STARS = re.compile(r'(\d)\s*(?:звезд|зірк|star|★)', re.IGNORECASE)


def _handle_review(msg):
    if not SETTINGS.get("review_bonus_enabled"):
        return
    text = str(getattr(msg, "text", "") or "")
    if not _RX_REVIEW_GIVEN.search(text) and "★" not in text:
        return
    dedup = f"{msg.chat_id}:{getattr(msg, 'id', '')}"
    if dedup in _processed_review:
        return
    _processed_review.add(dedup)
    if len(_processed_review) > 500:
        _processed_review.clear()

    stars = None
    m = _RX_STARS.search(text)
    if m:
        try:
            stars = int(m.group(1))
        except Exception:
            stars = None
    if stars is None:
        stars = text.count("★") or None
    min_stars = SETTINGS.get("review_bonus_min_stars", 5)
    if stars is not None and stars < min_stars:
        return

    chat_id = msg.chat_id
    buyer_key = _buyer_key(chat_id)
    rentals = _active_rentals(buyer_key)
    if not rentals:
        return
    target = sorted(rentals, key=lambda r: r.get("expires_ts", 0), reverse=True)[0]
    if target.get("review_bonus_given"):
        return
    api = _get_api()
    if not api:
        return
    bonus_hours = SETTINGS.get("review_bonus_hours", 2)
    currency = SETTINGS.get("currency", "RUB")
    # Идемпотентность бонуса: повторный запрос с тем же ключом не начислит часы дважды.
    _bonus_idem = f"fp-ext-{target['uid']}-bonus"
    data, err = api.extend(target["uid"], bonus_hours, currency, idempotency_key=_bonus_idem)
    if err or not data:
        notify_admins(f"Бонус за отзыв не начислен: {_human_err(err or 'unknown')}",
                      extra=f"uid={target['uid']}", dedup_key=f"revbonus:{err}")
        return
    new_exp = data.get("new_expires_at")
    target["expires_raw"] = new_exp
    target["expires_ts"] = _parse_iso(new_exp)
    target["review_bonus_given"] = True
    save_rentals()
    # Бонус за отзыв — тоже продление: себестоимость по extend-цене (API v1.1)
    cost_rub = _kosell_required_cost_rub(api, target.get("product_id"), bonus_hours,
                                         rental_uid=target.get("uid"))
    _stats_add_kosell(target.get("product_id"), target.get("product_name") or "аккаунт",
                      bonus_hours, cost_rub, "review_bonus")
    _send(chat_id, fmt("text_review_bonus", hours=bonus_hours, expires=_fmt_expires(new_exp)))


def _is_own_message(cardinal: "Cardinal", msg) -> bool:
    if getattr(msg, "by_bot", False):
        return True
    own_id = getattr(cardinal.account, "id", None)
    if own_id is not None and getattr(msg, "author_id", None) == own_id:
        return True
    own_name = getattr(cardinal.account, "username", None)
    if own_name and getattr(msg, "author", None) == own_name:
        return True
    return False


def on_new_message(cardinal: "Cardinal", event: NewMessageEvent):
    msg = event.message
    if not msg:
        return
    try:
        if getattr(msg, "type", None) != MessageTypes.NON_SYSTEM:
            _handle_review(msg)
            return
        if _is_own_message(cardinal, msg) or msg.author_id == 0 or getattr(msg, "i_am_buyer", False):
            return
        if not msg.text:
            return
        chat_name = getattr(msg, "chat_name", None) or getattr(msg, "author", None)
        _handle_buyer_text(msg.chat_id, msg.text, buyer_name=chat_name)
    except Exception as e:
        logger.error(f"{LP} on_new_message error: {e}", exc_info=True)


def on_last_chat_message_changed(cardinal: "Cardinal", event):
    chat = getattr(event, "chat", None)
    if not chat:
        return
    try:
        if getattr(chat, "last_by_bot", False) or not getattr(chat, "unread", False):
            return
        last = getattr(chat, "last_message_text", None)
        if not last:
            return
        _handle_buyer_text(chat.id, last, buyer_name=getattr(chat, "name", None))
    except Exception as e:
        logger.error(f"{LP} on_last_chat_message_changed error: {e}", exc_info=True)


_LOT_RETRY_WAITS = [6.0, 15.0, 20.0]


def _set_lot_active(lot_id, active: bool) -> bool:
    if not cardinal_instance:
        return False
    last_err = None
    attempts = len(_LOT_RETRY_WAITS) + 1
    for i in range(attempts):
        try:
            lf = cardinal_instance.account.get_lot_fields(int(lot_id))
            # LotFields.active is not a reliable indicator here: FunPayAPI
            # forcibly reports it as False when the parsed database amount is
            # zero, even if the "active" checkbox is still enabled on FunPay.
            # Always submit deactivation, otherwise we can log a successful
            # hide without sending any request at all.
            raw_fields = getattr(lf, "fields", {}) or {}
            raw_active = raw_fields.get("active") == "on"
            if active and lf.active and raw_active:
                return True
            # FunPay treats a manual-delivery lot with zero availability as
            # inactive, even when the checkbox is submitted as enabled.  A
            # newly created auto-rental lot must therefore have a positive
            # virtual amount before it can become visible.  Do not alter
            # auto-delivery lots: their amount is derived from secrets.
            if active and not getattr(lf, "auto_delivery", False):
                try:
                    if (getattr(lf, "amount", None) or 0) <= 0:
                        lf.amount = 1
                except Exception:
                    pass
            lf.active = active
            cardinal_instance.account.save_lot(lf)
            time.sleep(0.5)

            # FunPayAPI exposes active=False when the offer has zero internal
            # amount, even when the submitted checkbox is active. A freshly
            # created rental lot intentionally has a virtual amount (99999),
            # but some FunPay responses still carry that stale derived value.
            # The save request itself is authoritative for activation; the
            # follow-up checkbox read is retained for deactivation checks.
            if active:
                check = cardinal_instance.account.get_lot_fields(int(lot_id))
                check_fields = getattr(check, "fields", {}) or {}
                # The edit form is the authoritative result for activation.
                # Require both the checkbox and positive manual stock.
                if check_fields:
                    checked_on = check_fields.get("active") == "on"
                    if not getattr(check, "auto_delivery", False):
                        try:
                            checked_on = checked_on and int(check_fields.get("amount") or 0) > 0
                        except (TypeError, ValueError):
                            checked_on = False
                    if checked_on:
                        return True
                elif bool(check.active):
                    return True
                last_err = RuntimeError(f"FunPay did not apply active={active}")
                raise last_err

            # Do not persist HIDDEN on a merely successful HTTP request. Read
            # the edit form again and confirm the actual checkbox state.
            check = cardinal_instance.account.get_lot_fields(int(lot_id))
            check_fields = getattr(check, "fields", {}) or {}
            saved_active = check_fields.get("active") == "on"
            # Some older FunPayAPI builds do not expose the raw fields dict.
            # In that case the refreshed public property is the best signal.
            if (check_fields and saved_active == active) or (
                not check_fields and bool(check.active) == active
            ):
                return True
            last_err = RuntimeError(
                f"FunPay did not apply active={active}; actual active={saved_active}"
            )
            raise last_err
        except Exception as e:
            last_err = e
            err_text = str(e).lower()
            if any(marker in err_text for marker in
                   ("предложение не найдено", "страница не найдена", "offer not found")):
                lot_key = str(lot_id)
                mappings_before = len(MAPPINGS)
                MAPPINGS[:] = [m for m in MAPPINGS if str(m.get("lot_id")) != lot_key]
                hidden_removed = HIDDEN.pop(lot_key, None) is not None
                if len(MAPPINGS) != mappings_before:
                    save_mappings()
                if hidden_removed:
                    save_hidden()
                logger.warning(f"{LP} lot {lot_id} removed from mappings: offer not found on FunPay")
                return False
            if i < attempts - 1:
                wait = _LOT_RETRY_WAITS[i]
                logger.warning(f"{LP} set_lot_active {lot_id}={active} попытка {i+1}/{attempts} не удалась: {e}. "
                               f"Жду {wait:.0f}с и повторяю.")
                time.sleep(wait)
    logger.error(f"{LP} set_lot_active {lot_id}={active} failed после {attempts} попыток: {last_err}")
    return False


def _hide_lot(lot_id, reason: str):
    if str(lot_id) in HIDDEN:
        return
    if _set_lot_active(lot_id, False):
        HIDDEN[str(lot_id)] = reason
        save_hidden()
        logger.info(f"{LP} lot {lot_id} hidden ({reason})")


def _restore_lot(lot_id):
    if str(lot_id) not in HIDDEN:
        return
    if _set_lot_active(lot_id, True):
        HIDDEN.pop(str(lot_id), None)
        save_hidden()
        logger.info(f"{LP} lot {lot_id} restored")


def _poll_cycle():
    api = _get_api()
    if not api:
        return
    if not SETTINGS.get("lot_sync_enabled", True):
        return
    if _ac_running.get("on"):
        logger.debug(f"{LP} lot sync skipped: plugin task is running")
        return
    mapped_lots = [m for m in MAPPINGS if m.get("lot_id") and not _autolot_block_reason(m)]

    if SETTINGS.get("auto_hide_zero_balance"):
        bal = api.balance()
        if bal is not None:
            cur = SETTINGS.get("currency", "RUB")
            amount = bal.get("balance_rub" if cur == "RUB" else "balance_usd", 0) or 0
            threshold = SETTINGS.get("balance_threshold", 0)
            if amount <= threshold:
                if not _balance_alerted["low"]:
                    notify_admins(f"Баланс KOSell на нуле/ниже порога ({amount} {cur}). "
                                  f"Скрываю лоты - пополните баланс.", dedup_key="balance_low")
                    _balance_alerted["low"] = True
                changed = 0
                for m in mapped_lots:
                    if _ac_running.get("on"):
                        logger.info(f"{LP} lot sync interrupted: plugin task started")
                        return
                    if changed >= LOT_SYNC_BATCH_SIZE:
                        break
                    if str(m["lot_id"]) in HIDDEN:
                        continue
                    _hide_lot(m["lot_id"], "balance")
                    changed += 1
                return
            else:
                if _balance_alerted["low"]:
                    notify_admins_info(f"✅ Баланс KOSell пополнен ({amount} {cur}). Восстанавливаю лоты.")
                _balance_alerted["low"] = False
                changed = 0
                for lid, reason in list(HIDDEN.items()):
                    if reason == "balance":
                        if _ac_running.get("on"):
                            logger.info(f"{LP} lot sync interrupted: plugin task started")
                            return
                        if changed >= LOT_SYNC_BATCH_SIZE:
                            break
                        _restore_lot(lid)
                        changed += 1

    if SETTINGS.get("auto_hide_no_stock"):
        products = api.products()
        if products is None:
            return
        grace = 0
        try:
            grace = max(0, int(SETTINGS.get("eta_grace_sec", 900) or 0))
        except Exception:
            grace = 0
        # Жёсткий потолок: даже если KOSell обещает аккаунты «скоро», но дольше
        # 2×eta_grace_sec — лот прячем как обычно (неточный ETA не должен
        # держать лот продаваемым и провоцировать no_stock-возвраты).
        eta_cap = grace * 2 if grace > 0 else 0
        avail = {}
        eta_next = {}
        for p in products:
            try:
                avail[int(p.get("id"))] = int(p.get("available_accounts", 0) or 0)
            except Exception:
                continue
            # next_available: до 3 ближайших освобождений аккаунтов
            eta = None
            try:
                na = p.get("next_available") or []
                if na and eta_cap > 0:
                    for item in na:
                        secs = int(item.get("available_in_seconds") or 0)
                        if 0 < secs <= eta_cap:
                            eta = secs if eta is None else min(eta, secs)
            except Exception:
                eta = None
            if eta is not None:
                eta_next[int(p.get("id"))] = eta
        changed = 0
        for m in mapped_lots:
            if _ac_running.get("on"):
                logger.info(f"{LP} lot sync interrupted: plugin task started")
                return
            if changed >= LOT_SYNC_BATCH_SIZE:
                break
            pid = m.get("product_id")
            try:
                pid_int = int(pid)
            except Exception:
                continue
            if pid_int not in avail:
                continue
            if avail[pid_int] <= 0:
                if str(m["lot_id"]) not in HIDDEN:
                    # Нет аккаунтов: держим лот продаваемым, если KOSell обещает
                    # освобождение в пределах жёсткого потолка eta_cap (2×grace) —
                    # неточный ETA не должен провоцировать no_stock-возвраты.
                    eta = eta_next.get(pid_int)
                    if eta is not None and eta_cap > 0:
                        # Близкое освобождение (≤ grace) — дополнительно уведомляем покупателей.
                        if grace > 0 and eta <= grace:
                            _eta_notify_buyers(pid_int, m, eta)
                        continue
                    _hide_lot(m["lot_id"], "stock")
                    changed += 1
            else:
                if HIDDEN.get(str(m["lot_id"])) == "stock":
                    _restore_lot(m["lot_id"])
                    changed += 1


def _notify_expirations():
    if not SETTINGS.get("notify_rental_end", True):
        return
    now = _now()
    changed = False
    for bk, lst in list(RENTALS.items()):
        for r in lst:
            exp = r.get("expires_ts") or 0
            if exp and now >= exp and not r.get("end_notified"):
                try:
                    _send(int(bk), fmt("text_rental_ended",
                                       login=r.get("login", ""),
                                       game=r.get("product_name") or "аккаунт"))
                except Exception:
                    pass
                r["end_notified"] = True
                changed = True
    if changed:
        save_rentals()


_eta_notify_ts: dict = {}  # product_id -> ts последнего ETA-уведомления (в памяти)


def _eta_human(secs: int) -> str:
    """Форматирует секунды ETA в человекочитаемый вид: '2ч 30м'."""
    try:
        secs = max(0, int(secs))
    except Exception:
        return "скоро"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}д {h}ч" if h else f"{d}д"
    if h and m:
        return f"{h}ч {m}м"
    if h:
        return f"{h}ч"
    if m >= 1:
        return f"{m}м"
    return "менее минуты"


def _eta_notify_buyers(product_id, mapping, eta_secs: int):
    """
    При no_stock с ближайшим ETA (в пределах eta_grace_sec) не скрываем лот,
    а уведомляем «горячих» покупателей (писавших в последние ~30 мин),
    что аккаунты скоро появятся. Дедуп пер-товарный, в памяти — раз в N секунд.
    """
    try:
        if not SETTINGS.get("auto_hide_no_stock"):
            return
        now = _now()
        pid = str(product_id)
        cooldown = max(60, min(3600, int(SETTINGS.get("eta_notify_cooldown_sec", 600) or 600)))
        try:
            last = float(_eta_notify_ts.get(pid) or 0)
        except Exception:
            last = 0.0
        if now - last < cooldown:
            return
        _eta_notify_ts[pid] = now

        recent_cutoff = now - 1800  # 30 минут
        sent = 0
        seen = set()
        eta_text = _eta_human(eta_secs)
        for bk, rentals in list(RENTALS.items()):
            for r in rentals:
                if str(r.get("product_id")) != pid:
                    continue
                chat_id = r.get("chat_id") or bk
                try:
                    chat_id = int(chat_id)
                except Exception:
                    continue
                if chat_id in seen:
                    continue
                seen.add(chat_id)
                # Уведомляем, только если с покупателем была активность недавно
                last_msg = float(r.get("last_activity_ts") or r.get("created_at") or 0)
                if last_msg < recent_cutoff:
                    continue
                _send(chat_id, fmt("text_eta", eta=eta_text))
                sent += 1
            if sent >= 10:
                break
        if sent:
            logger.info(f"{LP} ETA-уведомление sent to {sent} buyer(s) for product {pid} (ETA {eta_text})")
    except Exception as e:
        logger.debug(f"{LP} eta notify error: {e}")


def _poll_delivery_pending():
    """
    Официальная система «подготовки выдачи»: если delivery_prefetch_enabled,
    периодически проверяем список pending-аренд и логируем статус подготовки.
    """
    if not SETTINGS.get("delivery_prefetch_enabled"):
        return
    api = _get_api()
    if not api:
        return
    try:
        pending = api.delivery_pending()
        if not pending:
            return
        for item in pending:
            try:
                uid = item.get("rental_uid") or item.get("uid") or "?"
                status = item.get("status") or item.get("state") or "?"
                product = item.get("product_name") or item.get("product_id") or "?"
                logger.info(f"{LP} [delivery] pending: uid={uid} product={product} status={status}")
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"{LP} [delivery] pending check error: {e}")


def _ver_tuple(v) -> tuple:
    if isinstance(v, (tuple, list)):
        return tuple(int(x) for x in v)
    parts = re.findall(r"\d+", str(v or ""))
    return tuple(int(x) for x in parts) or (0,)


def _parse_remote_version(code: str) -> "str | None":
    m = re.search(r'^\s*VERSION\s*=\s*["\']([^"\']+)["\']', code, re.M)
    return m.group(1) if m else None


def _download_text(url: str) -> "str | None":
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        logger.debug(f"{LP} update download {url}: {e}")
    return None


def _apply_update(new_code: str, new_ver: str) -> bool:
    if "BIND_TO_PRE_INIT" not in new_code or "VERSION" not in new_code:
        logger.error(f"{LP} update: скачанный файл не похож на плагин — отмена")
        return False
    try:
        bak = SELF_PATH + ".bak"
        try:
            import shutil
            if os.path.exists(bak):
                os.remove(bak)
            shutil.copy2(SELF_PATH, bak)
        except Exception:
            pass
        new_code = new_code.replace("\r\n", "\n").replace("\r", "\n")
        tmp = SELF_PATH + ".new"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_code)
        os.replace(tmp, SELF_PATH)
        logger.info(f"{LP} обновление v{VERSION} → v{new_ver} записано в {SELF_PATH}")
        return True
    except Exception as e:
        logger.error(f"{LP} update apply error: {e}", exc_info=True)
        return False


def _save_update_pending(latest: str, refs: list, notes: str = ""):
    try:
        with open(UPDATE_PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump({"latest": latest, "refs": refs, "notes": notes}, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"{LP} save update_pending: {e}")


def _notify_update_ready(latest: str, notes: str = ""):
    if not bot:
        return
    txt = (f"<b>{LP}</b>\n"
           f"⬆️ Доступна версия <b>v{latest}</b> (у вас v{VERSION}).\n")
    if notes:
        txt += f"\n<b>Что нового:</b>\n{_html.escape(notes)}\n"
    txt += "\nОбновление уже загружено. Нажмите кнопку ниже, чтобы перезапустить FPC и применить его."
    kb = K()
    kb.row(B("♻️ Перезапустить FPC", callback_data=f"{P}_restart"))
    refs = []
    for admin_id in _admins():
        try:
            msg = bot.send_message(admin_id, txt, parse_mode="HTML", reply_markup=kb)
            refs.append([admin_id, msg.message_id])
        except Exception:
            pass
    _save_update_pending(latest, refs, notes)


def _finalize_update_after_restart():
    if not os.path.exists(UPDATE_PENDING_FILE):
        return
    try:
        with open(UPDATE_PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    try:
        os.remove(UPDATE_PENDING_FILE)
    except Exception:
        pass
    if not data or not bot:
        return
    if _ver_tuple(data.get("latest")) != _ver_tuple(VERSION):
        return
    kb = K()
    kb.row(B("🎛 Открыть меню плагина", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    done_txt = f"<b>{LP}</b>\n✅ Обновление применено. Текущая версия: <b>v{VERSION}</b>."
    notes = (data.get("notes") or "").strip()
    if notes:
        done_txt += f"\n\n<b>Что нового:</b>\n{_html.escape(notes)}"
    for admin_id, mid in data.get("refs", []):
        try:
            bot.edit_message_text(done_txt, admin_id, mid, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


def _extract_notes(data: dict) -> str:
    """Достаёт текст «что нового» из ответа API (строка или список строк)."""
    plugin = data.get("plugin") or {}
    raw = (data.get("changelog") or data.get("release_notes") or data.get("notes")
           or data.get("whats_new") or plugin.get("changelog") or plugin.get("release_notes")
           or plugin.get("notes") or "")
    if isinstance(raw, (list, tuple)):
        lines = [str(x).strip() for x in raw if str(x).strip()]
        raw = "\n".join(f"• {ln}" if not ln.startswith(("•", "-", "—")) else ln for ln in lines)
    raw = str(raw).strip()
    if len(raw) > 1500:
        raw = raw[:1500].rstrip() + "…"
    return raw


def _check_update_via_api() -> "tuple[str, str, str] | None":
    from urllib.parse import urljoin
    for info_url in PLUGIN_INFO_URLS:
        try:
            r = requests.get(info_url, timeout=20)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception as e:
            logger.debug(f"{LP} update info {info_url}: {e}")
            continue
        remote = str(data.get("latest") or data.get("version") or "").strip()
        if not remote or _ver_tuple(remote) == _ver_tuple(VERSION):
            return None
        dl = ((data.get("plugin") or {}).get("download_url")) or f"/api/plugin/download/plugin/{remote}"
        dl_abs = urljoin(info_url, dl)
        code = _download_text(dl_abs)
        if code and _parse_remote_version(code):
            return code, remote, _extract_notes(data)
    return None


def check_update():
    if not AUTO_UPDATE_ENABLED:
        logger.debug(f"{LP} автообновление отключено для ребрендинга")
        return
    code = None
    remote = None
    notes = ""
    via_api = _check_update_via_api()
    if via_api:
        code, remote, notes = via_api
    else:
        for url in UPDATE_URLS:
            code = _download_text(url)
            if code:
                break
        if not code:
            logger.warning(f"{LP} проверка обновлений: ни один источник недоступен "
                           f"(API {PLUGIN_INFO_URLS[0]} и статичные URL). Проверьте доступ к kosell.store.")
            return
        remote = _parse_remote_version(code)
    if not remote:
        logger.warning(f"{LP} проверка обновлений: не удалось определить версию на сервере")
        return
    if _ver_tuple(remote) == _ver_tuple(VERSION):
        logger.info(f"{LP} проверка обновлений: актуальная версия v{VERSION} (на сервере v{remote})")
        return
    if _ver_tuple(remote) == _ver_tuple(_update_state.get("latest")) and _update_state.get("pending_restart"):
        return
    logger.info(f"{LP} проверка обновлений: найдена v{remote} (у вас v{VERSION}), загружаю…")
    if _apply_update(code, remote):
        _update_state["pending_restart"] = True
        _update_state["latest"] = remote
        _update_state["notes"] = notes
        _notify_update_ready(remote, notes)
        logger.info(f"{LP} обновление v{remote} загружено, уведомление отправлено админам ({len(_admins())})")


def _poller_loop():
    last_upd = 0.0
    try:
        _poll_stop.wait(8)
        if not _poll_stop.is_set():
            check_update()
            last_upd = time.time()
    except Exception as e:
        logger.error(f"{LP} startup update check error: {e}")
    while not _poll_stop.is_set():
        _poll_stop.wait(max(15, SETTINGS.get("poll_sec", 60)))
        if _poll_stop.is_set():
            break
        try:
            if SETTINGS.get("enabled"):
                _notify_expirations()
                _expire_pending_qty_orders()
                _poll_delivery_pending()
            _cleanup_rentals()
            if SETTINGS.get("enabled") and (SETTINGS.get("auto_hide_no_stock") or SETTINGS.get("auto_hide_zero_balance")):
                _poll_cycle()
            if not _update_state.get("pending_restart") and time.time() - last_upd > UPDATE_CHECK_SEC:
                last_upd = time.time()
                try:
                    check_update()
                except Exception as e:
                    logger.error(f"{LP} update check error: {e}")
        except Exception as e:
            logger.error(f"{LP} poll error: {e}", exc_info=True)


def _start_poller():
    global _poll_thread
    if _poll_thread and _poll_thread.is_alive():
        return
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poller_loop, daemon=True)
    _poll_thread.start()


def _stop_poller(*args, **kwargs):
    _poll_stop.set()


P = "kos"


def _back_btn(cb):
    return B("◀️ Назад", callback_data=cb)


# ============================================================================
#  АВТОСОЗДАНИЕ ЛОТОВ v1
# ============================================================================

def _ac_tpl() -> dict:
    t = SETTINGS.get("ac_template")
    return t if isinstance(t, dict) else {}


def _ac_has_template() -> bool:
    t = _ac_tpl()
    return bool(t.get("summary_ru") and t.get("durations"))


def _norm_game(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\[.*?\]", " ", s)
    s = re.sub(r"[+:&/\\,.\-_!?|™®]", " ", s)
    s = re.sub(r"[^a-z0-9а-яё' ]", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def _format_kostime(hours: int, lang: str = "ru") -> str:
    if lang == "en":
        if hours >= 720 and hours % 720 == 0:
            m = hours // 720
            return f"{m} month{'s' if m != 1 else ''}"
        if hours >= 24 and hours % 24 == 0:
            d = hours // 24
            return f"{d} day{'s' if d != 1 else ''}"
        return f"{hours}h"
    if hours >= 720:
        m = max(1, round(hours / 720))
        if m == 1:
            return "1 месяц"
        if 2 <= m % 10 <= 4 and m % 100 not in (12, 13, 14):
            return f"{m} месяца"
        return f"{m} месяцев"
    if hours >= 24 and hours % 24 == 0:
        d = hours // 24
        if d == 1:
            return "1 день"
        if 2 <= d % 10 <= 4 and d % 100 not in (12, 13, 14):
            return f"{d} дня"
        return f"{d} дней"
    if hours % 10 == 1 and hours % 100 != 11:
        return f"{hours} час"
    if hours % 10 in (2, 3, 4) and hours % 100 not in (12, 13, 14):
        return f"{hours} часа"
    return f"{hours} часов"


def _apply_placeholders(text: str, game_name: str, hours: int, lang: str = "ru") -> str:
    if not text:
        return text
    t = _format_kostime(hours, lang)
    g = game_name or ""
    out = text
    for ph in (PH_TIME, "kostime"):
        out = re.sub(re.escape(ph), t, out, flags=re.I)
    for ph in (PH_GAME, "kosgame"):
        out = re.sub(re.escape(ph), g, out, flags=re.I)
    return out


def _apply_title_placeholders(text: str, game_name: str, hours: int, lang: str = "ru",
                              limit: int = FP_SUMMARY_LIMIT) -> str:
    if not text:
        return ""
    time_text = _format_kostime(hours, lang)
    out = text
    for ph in (PH_TIME, "kostime"):
        out = re.sub(re.escape(ph), time_text, out, flags=re.I)
    game_rx = re.compile(rf"{re.escape(PH_GAME)}|kosgame", re.I)
    game = _strip_rent_code_from_title(game_name or "")
    if game_rx.search(out):
        lo, hi, best = 0, len(game), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            part = game if mid >= len(game) else game[:max(1, mid - 3)].rstrip(" .,;:!?|/-_") + "..."
            cand = re.sub(r"\s+", " ", game_rx.sub(part, out)).strip()
            cand = _strip_rent_code_from_title(cand)
            if len(cand) <= limit:
                best = cand
                lo = mid + 1
            else:
                hi = mid - 1
        if best:
            return best
    return _limit_lot_title_keep_tail(_apply_placeholders(text, game_name, hours, lang), time_text, limit)


def _normalize_short_en(text: str) -> str:
    text = _ascii(_strip_rent_code_from_title(text))
    return _limit_lot_title(text, FP_SUMMARY_LIMIT)


def _kosell_cost_rub(api: "KOSellAPI | None", prod: dict, hours: int) -> float:
    pid = int(prod.get("id") or 0)
    key = (pid, int(hours))
    now = time.time()
    c = _price_cache.get(key)
    if c and now - c["ts"] < AC_CACHE_TTL:
        return c["rub"]
    rub = None
    if api is not None and pid:
        data = api.calculate_price(pid, hours)
        if data and data.get("total_rub") is not None:
            try:
                rub = float(data["total_rub"])
                _dbg(f"calc-price pid={pid} {hours}ч → {rub}₽ "
                     f"(скидка {data.get('discount_percent')}%)")
            except Exception:
                rub = None
    if rub is None:
        pph_rub = float(prod.get("price_per_hour_rub") or prod.get("price_per_hour") or 0)
        rub = pph_rub * hours
        _dbg(f"calc-price fallback pid={pid} {hours}ч → {rub}₽ (price_per_hour_rub×часы)")
    _price_cache[key] = {"ts": now, "rub": rub}
    return rub


def _get_commission(subcat_id: int) -> float:
    sid = int(subcat_id)
    if sid in _commission_cache:
        return _commission_cache[sid]
    k = 1.0
    try:
        acc = cardinal_instance.account
        headers = {"accept": "*/*", "x-requested-with": "XMLHttpRequest"}
        data = {"nodeId": sid, "price": 1000}
        r = acc.method("post", "lots/calc", headers, data)
        j = r.json()
        methods = j.get("methods") or []
        cur = SETTINGS.get("currency", "RUB")
        matching = [m for m in methods if m.get("unit") == cur]
        if matching:
            k = float(min(matching, key=lambda x: float(x.get("price", 1000))).get("price", 1000)) / 1000.0
    except Exception as e:
        logger.debug(f"{LP} [AC] commission sub={sid}: {e}")
    _commission_cache[sid] = k
    return k


def _money(x) -> str:
    try:
        v = round(float(x) + 1e-9, 2)
    except Exception:
        return str(x)
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _money_dash(x) -> str:
    if x is None:
        return "—"
    return _money(x)


def _pct(x) -> str:
    try:
        v = round(float(x), 2)
    except Exception:
        return str(x)
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _min_price_floor(tpl: "dict | None" = None) -> float:
    try:
        v = float(SETTINGS.get("ac_min_price", 15) or 0)
    except Exception:
        v = 15.0
    return v if v >= 1.0 else 1.0


def _public_price_rub(api: "KOSellAPI | None", prod: dict, hours: int, tpl: "dict | None" = None) -> float:
    t = tpl or _ac_tpl()
    floor = _min_price_floor(t)
    if t.get("price_mode") == "fixed":
        fp = t.get("fixed_prices") or {}
        val = fp.get(str(hours), fp.get(hours))
        if val is None:
            val = t.get("fixed_public_rub")
        return round(float(max(floor, float(val or floor))), 2)
    product_markup = (SETTINGS.get("product_markup_percent") or {}).get(str(prod.get("id")))
    markup = float(product_markup if product_markup is not None else t.get("markup_percent", 50) or 0)
    base = _kosell_cost_rub(api, prod, hours)
    return round(float(max(floor, base * (1 + markup / 100.0))), 2)


def _lot_input_price(public_rub: float, subcat_id: int) -> float:
    return float(max(_min_price_floor(), public_rub))


def _map_urls_to_try() -> list:
    custom = (SETTINGS.get("map_url") or "").strip()
    urls = []
    if custom and custom not in MAP_URLS:
        urls.append(custom)
    for u in MAP_URLS:
        if u not in urls:
            urls.append(u)
    return urls


def _merge_map_overrides(data: dict) -> dict:
    overrides = _load_json(MAP_OVERRIDES_FILE, {})
    extra = overrides.get("games") if isinstance(overrides, dict) else None
    if not extra:
        return data
    games = list(data.get("games") or [])
    by_pid = {}
    for idx, game in enumerate(games):
        try:
            by_pid[int(game.get("kosell_product_id"))] = idx
        except (TypeError, ValueError):
            continue
    for game in extra:
        try:
            pid = int(game.get("kosell_product_id"))
        except (TypeError, ValueError):
            continue
        idx = by_pid.get(pid)
        if idx is None:
            by_pid[pid] = len(games)
            games.append(game)
        else:
            games[idx] = game
    data["games"] = games
    return data


def _remember_map_override(product_id: int, product_name: str, route: dict) -> None:
    overrides = _load_json(MAP_OVERRIDES_FILE, {})
    games = overrides.get("games") if isinstance(overrides, dict) else None
    if not isinstance(games, list):
        games = []
    entry = {
        "kosell_product_id": int(product_id),
        "kosell_name": product_name,
        "funpay": {
            "category_id": int(route["category_id"]),
            "subcategory_id": int(route["subcategory_id"]),
            "subcategory_type": "common",
            "fields": dict(route.get("fields") or {}),
        },
    }
    games = [g for g in games if str(g.get("kosell_product_id")) != str(product_id)]
    games.append(entry)
    _save_json(MAP_OVERRIDES_FILE, {"games": games})


def _repair_mojibake_value(value):
    if isinstance(value, str) and any(ch in value for ch in ("Ð", "Ñ", "Â", "â")):
        try:
            fixed = value.encode("latin1").decode("utf-8")
            if fixed and "\ufffd" not in fixed:
                return fixed
        except Exception:
            pass
    if isinstance(value, dict):
        return {k: _repair_mojibake_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_repair_mojibake_value(v) for v in value]
    return value


def _fetch_map(force: bool = False) -> "dict | None":
    now = time.time()
    if not force and _map_cache["data"] and now - _map_cache["ts"] < 600:
        return _map_cache["data"]
    data = None
    ok_url = None
    for url in _map_urls_to_try():
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"Accept": "application/json"})
            ctype = (r.headers.get("Content-Type") or "").lower()
            if r.status_code == 200 and ("json" in ctype or r.text.lstrip()[:1] == "{"):
                candidate = r.json()
                if isinstance(candidate, dict) and candidate.get("games"):
                    candidate = _repair_mojibake_value(candidate)
                    data = candidate
                    ok_url = url
                    _save_json(MAP_FILE, data)
                    break
            _dbg(f"map {url}: HTTP {r.status_code}")
        except Exception as e:
            _dbg(f"map {url}: {e}")
    if data and ok_url:
        logger.info(f"{LP} [AC] карта загружена ({len(data.get('games', []))} игр): {ok_url}")
        _map_cache["warned"] = False
    elif not _map_cache.get("warned"):
        logger.info(f"{LP} [AC] все URL карты недоступны — локальный кэш {MAP_FILE}")
        _map_cache["warned"] = True
    if data is None:
        data = _load_json(MAP_FILE, None)
    if data:
        data = _repair_mojibake_value(data)
        data = _merge_map_overrides(data)
        _save_json(MAP_FILE, data)
        _map_cache["data"] = data
        _map_cache["ts"] = now
    return data


def _diag_add(diagnostics: "list | None", reason: str, **fields) -> None:
    if diagnostics is not None:
        item = {"reason": reason}
        item.update(fields)
        diagnostics.append(item)


def _map_name_candidates(g: dict) -> list[str]:
    names = []
    for key in ("kosell_name", "name", "product_name"):
        val = (g.get(key) or "").strip()
        if val:
            names.append(val)
    for val in (g.get("aliases") or []):
        val = str(val or "").strip()
        if val:
            names.append(val)
    seen, out = set(), []
    for name in names:
        low = name.lower()
        if low not in seen:
            seen.add(low)
            out.append(name)
    return out


def _soft_name_match(map_name: str, product_name: str) -> bool:
    mt = _norm_roman(map_name).split()
    pt = _norm_roman(product_name).split()
    if not mt or not pt:
        return False
    if mt == pt:
        return True
    short, long = (mt, pt) if len(mt) <= len(pt) else (pt, mt)
    if len(short) >= 2 and long[:len(short)] == short:
        return True
    if len(short) == 1 and len(short[0]) >= 6 and long and long[0] == short[0]:
        return True
    return False


def _discover_funpay_game_route(game_name: str) -> dict | None:
    """Find an account subcategory when the remote map does not know a game."""
    account = getattr(cardinal_instance, "account", None)
    categories = getattr(account, "_Account__categories", None)
    subcategories = getattr(account, "_Account__subcategories", None)
    if not categories or not subcategories:
        return None
    for category in categories:
        if not _soft_name_match(game_name, getattr(category, "name", "")):
            continue
        for subcategory in subcategories:
            if getattr(getattr(subcategory, "category", None), "id", None) != getattr(category, "id", None):
                continue
            if str(getattr(subcategory, "name", "")).strip().lower() != "аккаунты":
                continue
            if getattr(getattr(subcategory, "type", None), "name", "") != "COMMON":
                continue
            return {
                "category_id": int(category.id),
                "subcategory_id": int(subcategory.id),
                "fields": {"fields[type]": "Аренда"},
            }
    return None


def _map_games_available(api: "KOSellAPI", diagnostics: "list | None" = None) -> list:
    mp = _fetch_map()
    if not mp:
        _diag_add(diagnostics, "карта недоступна")
        return []
    prods = _products(api) or []
    by_id = {int(p["id"]): p for p in prods if str(p.get("id", "")).isdigit()}
    by_name = {}
    by_norm = {}
    for p in prods:
        nm = str(p.get("name", "")).strip()
        if not nm:
            continue
        by_name[nm.lower()] = p
        nk = _norm_roman(nm)
        if nk:
            by_norm.setdefault(nk, p)
    out = []
    for g in mp.get("games", []):
        try:
            map_pid = int(g.get("kosell_product_id", -1))
        except Exception:
            map_pid = -1
        p = by_id.get(map_pid)
        names = _map_name_candidates(g)
        if not p:
            for nm in names:
                p = by_name.get(str(nm or "").strip().lower())
                if p:
                    break
        if not p:
            for nm in names:
                nk = _norm_roman(str(nm or ""))
                p = by_norm.get(nk) if nk else None
                if p:
                    break
        if not p:
            for nm in names:
                p = next((prod for prod in prods if _soft_name_match(str(nm or ""), str(prod.get("name", "")))), None)
                if p:
                    _diag_add(diagnostics, "мягкое совпадение имени",
                              map_name=nm, product_name=p.get("name"), product_id=p.get("id"))
                    break
        if p:
            out.append((g, p))
        else:
            _diag_add(diagnostics, "товар из карты не найден в KOSell",
                      map_product_id=map_pid if map_pid >= 0 else None,
                      name=(names[0] if names else g.get("name") or "без названия"))
    return out


def _load_misc_games() -> dict:
    if _misc_cache:
        return _misc_cache
    data = _load_json(MISC_GAMES_FILE, {})
    by_name, by_norm = {}, {}
    for g in data.get("games", []):
        nm = (g.get("name") or "").strip()
        if not nm:
            continue
        by_name[nm.lower()] = g
        by_norm[_norm_game(nm)] = g
    _misc_cache.update({"meta": data, "by_name": by_name, "by_norm": by_norm})
    return _misc_cache


def _name_overrides() -> dict:
    data = _load_json(NAME_OVERRIDES_FILE, {})
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if k and v:
                out[str(k).strip().lower()] = str(v)
    return out


def _display_name(kosell_name: str) -> str:
    ov = _name_overrides()
    return ov.get((kosell_name or "").strip().lower(), kosell_name or "")


def _misc_any_game_id() -> "str | None":
    m = _load_misc_games().get("by_name") or {}
    for key in ("любая игра", "любая", "any game", "other"):
        g = m.get(key)
        if g and g.get("id"):
            return str(g["id"])
    return None


def _match_misc_game(kosell_name: str) -> "dict | None":
    m = _load_misc_games()
    kn = (kosell_name or "").strip().lower()
    if kn in m["by_name"]:
        return m["by_name"][kn]
    nn = _norm_game(kosell_name)
    if nn in m["by_norm"]:
        return m["by_norm"][nn]
    kt = nn.split()
    best, best_len = None, 0
    for nm, g in m["by_norm"].items():
        ft = nm.split()
        if not ft:
            continue
        if len(ft) == 1:
            if len(ft[0]) < 4:
                continue
            if ft[0] not in kt:
                continue
        elif kt[: len(ft)] != ft:
            continue
        if len(ft) > best_len:
            best_len, best = len(ft), g
    return best


def _all_sellable_games(api: "KOSellAPI", diagnostics: "list | None" = None) -> list:
    meta = _load_misc_games().get("meta") or {}
    misc_sub = int(meta.get("subcategory_id") or 451)
    misc_field = meta.get("field") or "server_id"
    mapped_pids = set()
    out = []

    def _add_misc(pid, raw_name):
        disp = _display_name(raw_name) or raw_name
        mg = _match_misc_game(disp) or _match_misc_game(raw_name)
        fields = {"fields[type]": "Аренда"}
        if mg:
            fields[misc_field] = str(mg["id"])
        else:
            any_id = _misc_any_game_id()
            if any_id:
                fields[misc_field] = any_id
        out.append({
            "product_id": pid,
            "product_name": raw_name,
            "game_name": (mg.get("name") if mg else None) or disp,
            "subcategory_id": misc_sub,
            "category_id": None,
            "fields": fields,
            "source": "misc",
        })

    for g, prod in _map_games_available(api, diagnostics):
        pid = int(prod["id"])
        gname = prod.get("name") or g.get("kosell_name") or ""
        mapped_pids.add(pid)
        if not _norm_game(gname):
            _dbg(f"игра без латиницы/кириллицы → прочие игры: {gname!r} → {_display_name(gname)!r} (pid={pid})")
            _add_misc(pid, gname)
            continue
        fp = g.get("funpay", {})
        fields = _repair_mojibake_value(dict(fp.get("fields") or {}))
        out.append({
            "product_id": pid,
            "product_name": gname,
            "game_name": gname,
            "subcategory_id": int(fp["subcategory_id"]),
            "category_id": fp.get("category_id"),
            "fields": fields,
            "source": "map",
        })

    for prod in (_products(api) or []):
        pid = int(prod["id"])
        if pid in mapped_pids:
            continue
        raw = prod.get("name", "")
        mg = _match_misc_game(_display_name(raw)) or _match_misc_game(raw)
        if not mg:
            route = _discover_funpay_game_route(raw)
            if route:
                _diag_add(diagnostics, "маршрут найден в категориях FunPay", product_id=pid, name=raw,
                          category_id=route["category_id"], subcategory_id=route["subcategory_id"])
                out.append({
                    "product_id": pid,
                    "product_name": raw,
                    "game_name": raw,
                    "subcategory_id": route["subcategory_id"],
                    "category_id": route["category_id"],
                "fields": route["fields"],
                "source": "discovered",
            })
                _remember_map_override(pid, raw, route)
                continue
            _diag_add(diagnostics, "нет маршрута в карте и категориях FunPay", product_id=pid, name=raw)
            continue
        fields = {"fields[type]": "Аренда", misc_field: str(mg["id"])}
        out.append({
            "product_id": pid,
            "product_name": raw,
            "game_name": mg.get("name") or raw,
            "subcategory_id": misc_sub,
            "category_id": None,
            "fields": fields,
            "source": "misc",
        })
    return out


STEAM_SHOWCASE_SUBCAT_ID = 89
STEAM_SHOWCASE_CATEGORY_ID = 45


def _is_forced_steam_showcase(prod: dict) -> bool:
    try:
        forced = {int(x) for x in (SETTINGS.get("steam_showcase_force_ids") or [343])}
    except (TypeError, ValueError):
        forced = {343}
    if str(prod.get("id", "")).isdigit() and int(prod["id"]) in forced:
        return True
    return _norm_game(prod.get("name", "")) == _norm_game("Assassin's Creed Black Flag Resynced")


def _steam_showcase_top_n() -> int:
    if not SETTINGS.get("steam_showcase_enabled", True):
        return 0
    try:
        return max(0, min(200, int(SETTINGS.get("steam_showcase_top", 30) or 0)))
    except Exception:
        return 30


def _steam_showcase_items(api: "KOSellAPI", diagnostics: "list | None" = None) -> list:
    n = _steam_showcase_top_n()
    if n <= 0:
        return []
    prods = _products(api) or []

    def score(prod: dict):
        try:
            stock = int(prod.get("available_accounts", 0) or 0)
        except Exception:
            stock = 0
        try:
            price = float(prod.get("price_per_hour_rub") or prod.get("price_per_hour") or 999999)
        except Exception:
            price = 999999.0
        name = _norm_game(prod.get("name", ""))
        return (-stock, price, name)

    ranked = sorted((p for p in prods if _norm_game(p.get("name", ""))), key=score)
    forced = [p for p in ranked if _is_forced_steam_showcase(p)]
    ranked = forced + [p for p in ranked if p not in forced]
    out = []
    for prod in ranked[:n]:
        try:
            pid = int(prod["id"])
        except Exception:
            continue
        raw = prod.get("name", "")
        out.append({
            "product_id": pid,
            "product_name": raw,
            "game_name": _display_name(raw) or raw,
            "subcategory_id": STEAM_SHOWCASE_SUBCAT_ID,
            "category_id": STEAM_SHOWCASE_CATEGORY_ID,
            "fields": {
                "fields[type]": "Аренда",
                "fields[region]": "Любой",
                "fields[level]": "",
            },
            "source": "steam_showcase",
            "placement": "steam",
        })
    _diag_add(diagnostics, "Steam-витрина: кандидаты",
              count=len(out), cap=n, subcategory_id=STEAM_SHOWCASE_SUBCAT_ID)
    return out


def _existing_lot_keys() -> set:
    keys = set()
    for m in MAPPINGS:
        pid, hrs = m.get("product_id"), m.get("hours")
        if pid is not None and hrs is not None:
            try:
                keys.add((int(pid), int(hrs), m.get("placement") or "main"))
            except Exception:
                continue
    return keys


def _mapping_key(product_id, hours, placement: str = "main") -> "tuple[int, int, str] | None":
    try:
        return int(product_id), int(hours), placement or "main"
    except Exception:
        return None


def _mapping_exists(product_id, hours, placement: str = "main") -> bool:
    key = _mapping_key(product_id, hours, placement)
    if key is None:
        return False
    return key in _existing_lot_keys()


def _rent_code_exists(code: str, ignore_idx: "int | None" = None) -> bool:
    code = (code or "").strip().lower()
    if not code:
        return False
    for idx, m in enumerate(MAPPINGS):
        if ignore_idx is not None and idx == ignore_idx:
            continue
        if code in _mapping_rent_codes(m):
            return True
    return False


def _lot_title_norm(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_rent_code_from_title(text)).strip().casefold()


def _my_subcat_lots(subcat_id: int, cache: "dict | None" = None) -> list:
    sid = int(subcat_id)
    if cache is not None and sid in cache:
        return cache[sid]
    lots = []
    try:
        lots = cardinal_instance.account.get_my_subcategory_lots(sid)
    except Exception as e:
        logger.debug(f"{LP} [AC] my lots fail sub={sid}: {_short_err(e)}")
        try:
            all_lots = cardinal_instance.profile.get_lots()
            lots = [
                lot for lot in (all_lots or [])
                if str(getattr(getattr(lot, "subcategory", None), "id", "")) == str(sid)
                or f"/lots/{sid}/" in (getattr(lot, "html", "") or "")
            ]
        except Exception as e2:
            logger.debug(f"{LP} [AC] profile lots fallback fail sub={sid}: {_short_err(e2)}")
    if cache is not None:
        cache[sid] = lots
    return lots


def _find_existing_own_lot(subcat_id: int, summary_ru: str, cache: "dict | None" = None) -> "int | None":
    want = _lot_title_norm(summary_ru)

    if not want:
        return None
    for lot in _my_subcat_lots(subcat_id, cache):
        if _lot_title_norm(getattr(lot, "description", "") or getattr(lot, "title", "")) == want:
            try:
                return int(lot.id)
            except Exception:
                return lot.id
    return None


def _find_existing_own_lot_with_code(subcat_id: int, summary_ru: str, rent_code: str,
                                     cache: "dict | None" = None) -> "int | None":
    want = _lot_title_norm(summary_ru)
    code = (rent_code or "").strip().lower()
    if not want or not code:
        return None
    for lot in _my_subcat_lots(subcat_id, cache):
        title = getattr(lot, "description", "") or getattr(lot, "title", "")
        text = "\n".join(str(x or "") for x in (title, getattr(lot, "html", ""))).lower()
        if _lot_title_norm(title) == want and code in text:
            try:
                return int(lot.id)
            except Exception:
                return lot.id
    return None


def _append_mapping_for_lot(lot_id, item: dict) -> bool:
    if not lot_id:
        return False
    lid = str(lot_id)
    if any(str(m.get("lot_id")) == lid for m in MAPPINGS if m.get("lot_id")):
        return False
    placement = item.get("placement") or "main"
    key = _mapping_key(item.get("product_id"), item.get("hours"), placement)
    if key is not None and key in _existing_lot_keys():
        return False
    rent_code = item.get("rent_code") or _make_rent_code(item.get("product_id"), item.get("hours"), "" if placement == "main" else placement)
    if _rent_code_exists(rent_code):
        return False
    MAPPINGS.append({
        "lot_id": lid,
        "subcategory_id": item.get("subcategory_id"),
        "title_keyword": (item.get("game_name") or "")[:40],
        "product_id": item.get("product_id"),
        "product_name": item.get("product_name"),
        "hours": item.get("hours"),
        "rent_code": rent_code,
        "placement": placement,
        "currency": SETTINGS.get("currency", "RUB"),
        "auto_created": True,
    })
    save_mappings()
    return True


def _fp_public_lots(subcat_id: int) -> list:
    sid = int(subcat_id)
    c = _pub_lots_cache.get(sid)
    now = time.time()
    if c and now - c["ts"] < AC_CACHE_TTL:
        return c["lots"]
    out = []
    try:
        lots = cardinal_instance.account.get_subcategory_public_lots(SubCategoryTypes.COMMON, sid)
        for l in lots:
            pr = float(getattr(l, "price", 0) or 0)
            if pr <= 0:
                continue
            desc = (getattr(l, "description", "") or "").lower()
            server = (getattr(l, "server", "") or "")
            server = server.lower() if isinstance(server, str) else ""
            out.append((pr, desc, server))
        _pub_lots_cache[sid] = {"ts": now, "lots": out}
        _dbg(f"FP подкат {sid}: загружено {len(out)} лотов (кэш 15 мин)")
    except Exception as e:
        logger.debug(f"{LP} [AC] public lots fail sub={sid}: {_short_err(e)}")
    return out


_RX_MINORDER = re.compile(r"от\s*\d+\s*(?:час\w*|ч\b|дн\w*|д\b|сут\w*|нед\w*|мес\w*)", re.I)
_RX_BONUS = re.compile(r"[+(]?\s*\d+\s*(?:час\w*|ч\b|дн\w*|д\b)?\s*за\s*отзыв", re.I)


def _duration_regex(hours: int) -> "re.Pattern":
    pats = []
    h = hours
    pats.append(rf"(?<!\d){h}\s*ч(?:ас(?:а|ов)?)?\b")
    if h % 24 == 0:
        d = h // 24
        pats.append(rf"(?<!\d){d}\s*д(?:\b|ень|ня|ней|н\.)")
        pats.append(rf"(?<!\d){d}\s*сут")
        if h == 24:
            pats.append(r"\bсуток\b|\bсутки\b")
    if h % 168 == 0:
        w = h // 168
        pats.append(rf"(?<!\d){w}\s*недел")
    if h % 720 == 0:
        mo = h // 720
        pats.append(rf"(?<!\d){mo}\s*мес(?:яц|яца|яцев)?\b")
    return re.compile("|".join(pats), re.I)


def _fp_lowest_prices_for(subcat_id: int, hours: int, game_name: "str | None" = None, n: int = 5) -> list:
    lots = _fp_public_lots(subcat_id)
    if not lots:
        return []
    rx = _duration_regex(hours)
    gtok = None
    if game_name:
        gtok = _norm_game(game_name).split()
        gtok = gtok[0] if gtok else None
    matched = []
    for pr, desc, server in lots:
        if game_name and gtok and gtok not in desc and gtok not in server:
            continue
        clean = _RX_MINORDER.sub(" ", desc)
        clean = _RX_BONUS.sub(" ", clean)
        if rx.search(clean):
            matched.append(pr)
    if not matched:
        return []
    return sorted(matched)[:n]


def _fp_lowest_prices(subcat_id: int, n: int = 5) -> list:
    lots = _fp_public_lots(subcat_id)
    return sorted(p for p, _, _ in lots)[:n]


def _ascii(text: str, keep_formatting: bool = False) -> str:
    if not text:
        return ""
    text = re.sub(r"[\u0400-\u04FF]", "", text)
    text = (text.replace("•", "-")
            .replace("—", "-")
            .replace("–", "-")
            .replace("→", "->")
            .replace("“", '"')
            .replace("”", '"')
            .replace("’", "'"))
    if keep_formatting:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[^\x09\x0A\x20-\x7E]", " ", text)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
    text = re.sub(r"[^\x20-\x7E]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compose_limited_title(prefix: str, suffix: str, limit: int = FP_SUMMARY_LIMIT) -> str:
    prefix = re.sub(r"\s+", " ", str(prefix or "")).strip()
    suffix = re.sub(r"\s+", " ", str(suffix or "")).strip()
    if suffix:
        suffix = " " + suffix
    if len(prefix) + len(suffix) <= limit:
        return (prefix + suffix).strip()
    keep = max(1, limit - len(suffix) - 3)
    if keep < 8:
        return _limit_lot_title((prefix + suffix).strip(), limit)
    return (prefix[:keep].rstrip(" .,;:!?|/-_") + "..." + suffix).strip()


def _en_title_ok(text: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return len(compact) >= 8 and bool(re.search(r"[a-z]", compact))


def _default_summary_en(game_name: str, hours: int) -> str:
    game = _ascii(game_name) or "Game"
    return _compose_limited_title(f"{game} rental account", f"| Steam | {_format_kostime(hours, 'en')}")


def _default_desc_en(game_name: str, hours: int) -> str:
    game = _ascii(game_name) or "game"
    return (
        f"Rent a {game} account for {_format_kostime(hours, 'en')}.\n"
        "Instant automatic delivery after payment.\n"
        "Steam Guard code: use !code login in chat."
    )


def _ensure_full_game_line(text: str, game_name: str, lang: str = "ru") -> str:
    game = re.sub(r"\s+", " ", str(game_name or "")).strip()
    if not game:
        return text or ""
    if lang == "en":
        game_line_value = _ascii(game).strip() or "the selected game"
        line = f"Game: {game_line_value}"
    else:
        line = f"Игра: {game}"
    base = (text or "").strip()
    if line.lower() in base.lower():
        return base
    return f"{line}\n\n{base}" if base else line


def _durations_for(prod: dict, tpl: "dict | None" = None) -> list:
    t = tpl or _ac_tpl()
    want = t.get("durations") or [3, 24, 72, 168]
    mn = int(prod.get("min_hours") or 1)
    mx = int(prod.get("max_hours") or 720)
    return sorted((h for h in want if mn <= h <= mx), reverse=True)


def _build_lot_texts(game_name: str, hours: int, tpl: "dict | None" = None,
                     product_id=None, placement: str = ""):
    t = tpl or _ac_tpl()
    sru = _apply_title_placeholders(t.get("summary_ru", ""), game_name, hours, "ru")
    sen = _apply_title_placeholders(t.get("summary_en", ""), game_name, hours, "en")
    sen = _normalize_short_en(sen)
    fru = _apply_placeholders(t.get("desc_ru", ""), game_name, hours, "ru")
    fen = _ascii(_apply_placeholders(t.get("desc_en", ""), game_name, hours, "en"), keep_formatting=True)
    fru = _strip_public_contacts(fru)
    fen = _strip_public_contacts(fen)
    if not _en_title_ok(sen):
        sen = _default_summary_en(game_name, hours)
    if not fen:
        fen = _default_desc_en(game_name, hours)
    if not sru:
        sru = _compose_limited_title(game_name, _format_kostime(hours))
    if not sen:
        sen = "RENTAL ACCOUNT INSTANT AUTO DELIVERY"
    fru = _ensure_full_game_line(fru, game_name, "ru")
    fen = _ensure_full_game_line(fen, game_name, "en")
    if product_id is not None:
        code = _make_rent_code(product_id, hours, "" if not placement or placement == "main" else placement)
        fru = _append_rent_code_full(fru, code)
        fen = _append_rent_code_full(fen, code)
    return sru, sen, fru, fen


def _node_form(subcat_id: int) -> dict:
    sid = int(subcat_id)
    c = _node_form_cache.get(sid)
    now = time.time()
    if c and now - c["ts"] < AC_CACHE_TTL:
        return c["form"]
    account = cardinal_instance.account
    form = {"defaults": {}, "selects": {}, "required": set()}
    try:
        from bs4 import BeautifulSoup
        r = account.method("get", f"lots/offerEdit?node={sid}", {}, {}, raise_not_200=True)
        bs = BeautifulSoup(r.content.decode(), "lxml")
        f = bs.find("form", class_="form-offer-editor") or bs
        for inp in f.find_all("input"):
            name = inp.get("name")
            if not name or name == "query":
                continue
            if inp.get("type") == "checkbox":
                if inp.has_attr("checked"):
                    form["defaults"][name] = "on"
            else:
                form["defaults"][name] = inp.get("value") or ""
            if inp.get("required") is not None or "required" in (inp.get("class") or []):
                form["required"].add(name)
        for ta in f.find_all("textarea"):
            if ta.get("name"):
                form["defaults"][ta["name"]] = ta.text or ""
        for sel in f.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            parent = sel.find_parent(class_="form-group")
            pcls = (parent.get("class") or []) if parent else []
            if "hidden" in pcls and "lot-field" not in pcls:
                continue
            opts = []
            selected_val = ""
            for o in sel.find_all("option"):
                val = o.get("value") or ""
                opts.append((val, (o.text or "").strip()))
                if o.get("selected") is not None and val:
                    selected_val = val
            form["selects"][name] = opts
            form["defaults"][name] = selected_val
            if sel.get("required") is not None or "required" in (sel.get("class") or []):
                form["required"].add(name)
        csrf = f.find("input", {"name": "csrf_token"})
        if csrf and csrf.get("value"):
            account.csrf_token = csrf["value"]
        _dbg(f"_node_form sub={sid}: selects={list(form['selects'].keys())} required={sorted(form['required'])}")
    except Exception as e:
        logger.debug(f"{LP} [AC] node_form sub={sid}: {_short_err(e)}")
    _node_form_cache[sid] = {"ts": now, "form": form}
    return form


_PLATFORM_WORDS = {"pc", "пк", "ps", "ps3", "ps4", "ps5", "xbox", "switch", "steam",
                   "origin", "epic", "windows", "win", "mac", "computer", "компьютер"}
_PLATFORM_DETECT = _PLATFORM_WORDS | {"playstation", "плейстейшн", "macos", "linux", "линукс",
                                      "android", "андроид", "ios", "айос", "mobile", "мобил",
                                      "телефон", "nintendo", "нинтендо", "ps2"}
_PLATFORM_AVOID = ("android", "андроид", "ios", "айос", "mobile", "мобил", "телефон",
                   "ps", "xbox", "switch", "nintendo", "playstation", "плейстейшн")


def _looks_platform(nonempty: list) -> bool:
    if not nonempty:
        return False
    hits = 0
    for _v, t in nonempty:
        tl = (t or "").strip().lower()
        if any(p == tl or p in tl for p in _PLATFORM_DETECT):
            hits += 1
    return hits >= max(1, (len(nonempty) + 1) // 2)
_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
          "viii": "8", "ix": "9", "x": "10"}
_EDITIONS = [
    ("ultimate", "ультимейт", "ультиматив"),
    ("collector", "коллекцион"),
    ("legendary", "легендар"),
    ("definitive", "дефинитив"),
    ("complete", "complet", "комплит", "полное", "полная"),
    ("game of the year", "goty", "года"),
    ("premium", "премиум"),
    ("deluxe", "делюкс", "делюх"),
    ("gold", "голд", "золот"),
    ("anniversary", "юбилейн", "годовщин"),
    ("enhanced", "энханс", "улучшен"),
    ("special", "спешл", "специальн"),
    ("standard", "standart", "стандарт", "обычн"),
]


def _norm_roman(s: str) -> str:
    s = _norm_game(s)
    return " ".join(_ROMAN.get(tok, tok) for tok in s.split())


def _pick_option(name: str, opts: list, game_name: str = "") -> str:
    nonempty = [(v, (t or "").strip()) for v, t in opts if v]
    if not nonempty:
        return ""
    low = name.lower()
    if "platform" in low or _looks_platform(nonempty):
        for v, t in nonempty:
            if "steam" in t.lower():
                return v
        for v, t in nonempty:
            tl = t.lower()
            if ("windows" in tl or "win" in tl) and "epic" not in tl:
                return v
        for v, t in nonempty:
            tl = t.lower()
            if ("pc" in tl or "пк" in tl or "computer" in tl or "компьютер" in tl) and "epic" not in tl:
                return v
        for v, t in nonempty:
            tl = t.lower()
            if "epic" not in tl and "origin" not in tl and not any(a in tl for a in _PLATFORM_AVOID):
                return v
        return nonempty[0][0]
    if "server" in low or low.endswith("[game]") or low == "game":
        g = set(_norm_roman(game_name).split())
        if g:
            best, best_key = None, (0, 0.0, 0)
            for v, t in nonempty:
                tt = set(_norm_roman(t).split())
                if not tt:
                    continue
                inter = len(g & tt)
                sc = inter / len(tt)
                tl = t.lower()
                if "steam" in tl:
                    plat = 3
                elif ("pc" in tl or "пк" in tl or "windows" in tl) and "epic" not in tl:
                    plat = 2
                elif any(b in tl for b in ("ps", "xbox", "switch", "epic", "playstation", "плейстейшн")):
                    plat = -1
                else:
                    plat = 1
                key = (inter, sc, plat)
                if inter and key > best_key:
                    best_key, best = key, v
            if best and best_key[0] >= 1 and best_key[1] >= 0.5:
                return best
        for key in ("steam",):
            for v, t in nonempty:
                if key in t.lower():
                    return v
        for key in ("pc", "пк"):
            for v, t in nonempty:
                if key in t.lower():
                    return v
        return nonempty[0][0]
    if "offer" in low or "type" in low:
        for v, t in nonempty:
            tl = t.lower()
            if "аренд" in tl or "rent" in tl:
                return v
        gl = (game_name or "").lower()
        for variants in _EDITIONS:
            if any(k in gl for k in variants):
                for v, t in nonempty:
                    if any(k in t.lower() for k in variants):
                        return v
                break
        for key in ("стандарт", "standart", "standard", "обычн", "basic", "base", "common"):
            for v, t in nonempty:
                if key in t.lower():
                    return v
        return nonempty[0][0]
    return nonempty[0][0]


def _field_hours_from_form(nf: dict) -> int:
    text = " ".join(str(nf.get(key) or "") for key in (
        "fields[summary][ru]",
        "fields[summary][en]",
        "fields[desc][ru]",
        "fields[desc][en]",
    ))
    match = re.search(r"\b(1|3|6|12|24|72|168)\s*(?:h|hour|hours|ч|час)", text, re.I)
    return int(match.group(1)) if match else 1


def _fill_missing_from_error(nf: dict, node_form: dict, err_text: str, game_name: str) -> bool:
    pairs = re.findall(r'\[\s*"([^"]+)"\s*,\s*"([^"]*)"\s*\]', err_text or "")
    changed = False
    selects = node_form.get("selects") or {}
    for fname, _msg in pairs:
        cur = nf.get(fname)
        msg_low = str(_msg or "").lower()
        too_long = "слишком длин" in msg_low or "too long" in msg_low or "long text" in msg_low
        bad_english = (
            "некорректного английского" in msg_low
            or ("слишком коротк" in msg_low and "английск" in msg_low)
            or "incorrect english" in msg_low
            or "invalid english" in msg_low
            or ("too short" in msg_low and "english" in msg_low)
        )
        if bad_english and "desc][en" in fname:
            nf[fname] = _default_desc_en(game_name, _field_hours_from_form(nf))
            changed = True
            _dbg(f"  заменил некорректное английское описание {fname}")
            continue
        if bad_english and "summary][en" in fname:
            nf[fname] = _default_summary_en(game_name, _field_hours_from_form(nf))
            changed = True
            _dbg(f"  заменил некорректное английское название {fname}")
            continue
        if cur and too_long:
            if "summary][en" in fname:
                val = _limit_lot_title(_normalize_short_en(cur), FP_SUMMARY_RETRY_LIMIT)
                if not _en_title_ok(val):
                    val = _default_summary_en(game_name, 1)
                nf[fname] = val
                changed = True
            elif "summary][ru" in fname:
                nf[fname] = _limit_lot_title(cur, FP_SUMMARY_RETRY_LIMIT)
                changed = True
            elif "desc]" in fname:
                nf[fname] = str(cur)[:3500].rstrip()
                changed = True
            if changed:
                _dbg(f"  укоротил слишком длинное поле {fname}")
            continue
        if cur:
            continue
        if fname in selects:
            chosen = _pick_option(fname, selects[fname], game_name)
            if chosen:
                nf[fname] = chosen
                changed = True
                _dbg(f"  дозаполнил select {fname}={chosen} :D")
        else:
            low = fname.lower()
            if "level" in low or "уровен" in low:
                val = "1"
            elif "summary][en" in fname or "desc][en" in fname:
                val = "RENTAL ACCOUNT INSTANT AUTO DELIVERY"
            else:
                val = "1"
            nf[fname] = val
            changed = True
            _dbg(f"  дозаполнил input {fname}={val} :D")
    return changed


class _RateLimited(Exception):
    pass


_FP_FIELD_NAMES = {
    "fields[summary][ru]": "Название (RU)",
    "fields[summary][en]": "Название (EN)",
    "fields[desc][ru]": "Описание (RU)",
    "fields[desc][en]": "Описание (EN)",
    "fields[platform]": "Платформа",
    "fields[type]": "Тип",
    "server_id": "Сервер/игра",
    "price": "Цена",
}


def _is_funpay_rate_limit(err: str) -> bool:
    low = str(err or "").lower()
    markers = (
        "слишком много предложений",
        "много предложений",
        "too many offers",
        "rate limit",
        "rate_limited",
        "лимит создания лотов",
    )
    return any(marker in low for marker in markers)


def _humanize_funpay_err(err: str) -> str:
    if not err:
        return "неизвестная ошибка"
    s = str(err)
    low = s.lower()
    if _is_funpay_rate_limit(s):
        return "Лимит создания лотов FunPay (кулдаун)"
    try:
        m = re.search(r'\{.*\}', s, re.S)
        if m:
            data = json.loads(m.group(0))
            main = (data.get("error") or "").strip()
            parts = []
            for pair in (data.get("errors") or []):
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    fld = _FP_FIELD_NAMES.get(pair[0], pair[0])
                    parts.append(f"{fld}: {pair[1]}")
            if parts:
                return (main + " — " if main and main != "Заполните все поля." else "") + "; ".join(parts)
            if main:
                return main
    except Exception:
        pass
    s = s.strip()
    return s[-160:] if len(s) > 160 else s


def _make_lot_fields(lot_id: int, fields: dict, subcat_obj=None):
    try:
        return LotFields(lot_id, fields, subcat_obj, getattr(cardinal_instance.account, "currency", None))
    except TypeError:
        return LotFields(lot_id, fields)


def _get_subcategory_obj(subcat_id: int, category_id=None, game_name: str = ""):
    account = cardinal_instance.account
    try:
        subcat = account.get_subcategory(SubCategoryTypes.COMMON, int(subcat_id))
        if subcat:
            return subcat
    except Exception:
        pass
    try:
        from FunPayAPI.types import Category, SubCategory
        cat = None
        if category_id:
            try:
                cat = account.get_category(int(category_id))
            except Exception:
                cat = None
        if not cat:
            cat = Category(int(category_id or 0), game_name or str(category_id or subcat_id), [])
        return SubCategory(int(subcat_id), game_name or str(subcat_id), SubCategoryTypes.COMMON, cat)
    except Exception as e:
        logger.debug(f"{LP} [AC] subcategory fallback failed sub={subcat_id}: {e}")
        return None


def _create_one_lot(subcat_id: int, subcat_obj, map_fields: dict, summary_ru: str,
                    summary_en: str, full_ru: str, full_en: str, input_price: float,
                    game_name: str = "", product_id=None, hours=None) -> "int | None":
    account = cardinal_instance.account
    lots_before = _my_subcat_lots(int(subcat_id))
    before = {int(l.id) for l in lots_before if str(getattr(l, "id", "")).isdigit()}

    template_fields = None
    try:
        my = lots_before
        if my:
            template_fields = account.get_lot_fields(int(my[0].id))
    except Exception as e:
        logger.debug(f"{LP} [AC] no template for sub={subcat_id}: {e}")

    node_form = _node_form(subcat_id)

    if template_fields:
        nf = dict(template_fields.fields)
    else:
        nf = dict(node_form.get("defaults") or {})
        nf.setdefault("query", "")
        nf.setdefault("location", "")
        nf.setdefault("deleted", "")
        nf.setdefault("auto_delivery", "")
        nf.setdefault("secrets", "")
        nf.setdefault("fields[payment_msg][ru]", "")
        nf.setdefault("fields[payment_msg][en]", "")

    nf["offer_id"] = "0"
    nf["node_id"] = str(subcat_id)
    nf["deleted"] = ""
    nf["fields[summary][ru]"] = summary_ru
    nf["fields[summary][en]"] = summary_en
    nf["fields[desc][ru]"] = full_ru
    nf["fields[desc][en]"] = full_en
    _sanitize_summary_fields(nf)
    nf["price"] = str(input_price)
    nf["amount"] = "99999"
    nf["active"] = "on"
    nf["offer_type_id"] = "1"
    nf["csrf_token"] = account.csrf_token
    nf["form_created_at"] = str(int(time.time()))
    for k, v in (map_fields or {}).items():
        if v:
            nf[k] = v

    for k in list(nf.keys()):
        kl = k.lower()
        if "secret" in kl or "auto_delivery" in kl or "autodelivery" in kl or kl == "secrets":
            nf.pop(k, None)
    nf["auto_delivery"] = ""
    nf["secrets"] = ""

    selects = node_form.get("selects") or {}
    for name, opts in selects.items():
        valid = {v for v, _ in opts if v}
        cur = nf.get(name)
        low = name.lower()
        ne = [(v, t) for v, t in opts if v]
        is_platform = ("platform" in low) or _looks_platform(ne)
        is_game_select = ("server" in low or low.endswith("[game]") or low == "game"
                          or "type" in low or "offer" in low) and not is_platform
        if is_game_select or is_platform or (not cur) or (valid and cur not in valid):
            chosen = _pick_option(name, opts, game_name)
            if chosen and chosen != cur:
                nf[name] = chosen
                _dbg(f"sub={subcat_id} подобрал {name}={chosen} (игра: {game_name})")
            elif chosen and not cur:
                nf[name] = chosen
    if "fields[type]" not in selects and not nf.get("fields[type]"):
        nf["fields[type]"] = "Аренда"

    if AC_DEBUG or SETTINGS.get("ac_debug"):
        dump = {k: v for k, v in nf.items() if k not in ("csrf_token",)}
        _dbg(f"_create_one_lot sub={subcat_id} поля лота:\n{json.dumps(dump, ensure_ascii=False, indent=1)[:1500]}")

    saved = False
    last_err = ""
    attempt = 0
    while attempt < 6:
        nf["csrf_token"] = account.csrf_token
        lf = _make_lot_fields(0, dict(nf), subcat_obj)
        lf.active = True
        # save_lot() calls LotFields.renew_fields(), which rebuilds the raw
        # request from object properties. Set the amount property explicitly
        # so the initial creation request cannot be rewritten to zero stock.
        try:
            lf.amount = 99999
        except Exception:
            pass
        try:
            account.save_lot(lf)
            saved = True
            _dbg(f"save_lot OK sub={subcat_id} (попытка {attempt + 1})")
            break
        except Exception as e:
            msg = str(e)
            last_err = msg
            low_msg = msg.lower()
            ctx = f"pid={product_id} game={game_name} sub={subcat_id} hours={hours}"
            logger.warning(f"{LP} [AC] save_lot response {ctx}: {msg[-800:]}")
            if _is_funpay_rate_limit(msg):
                _dbg(f"rate-limit FunPay sub={subcat_id}: лимит создания, остановка прогона")
                raise _RateLimited(msg)
            _dbg(f"save_lot ошибка {ctx} (попытка {attempt + 1}): {msg[-300:]}")
            changed = _fill_missing_from_error(nf, node_form, msg, game_name)
            if ("fields[summary][en]" in msg or "fields[desc][en]" in msg) and not nf.get("fields[summary][en]"):
                nf["fields[summary][en]"] = "RENTAL ACCOUNT INSTANT AUTO DELIVERY"
                nf["fields[desc][en]"] = ("Rent a licensed account with instant automatic delivery "
                                          "right after payment. Steam Guard code on request in chat.")
                changed = True
            attempt += 1
            if not changed and "подождите" in low_msg and attempt < 3:
                time.sleep(5 * attempt)
                continue
            if not changed:
                logger.warning(f"{LP} [AC] save_lot failed pid={product_id} game={game_name} "
                               f"sub={subcat_id} hours={hours}: {msg[-200:]}")
                break

    if not saved:
        return None, _humanize_funpay_err(last_err)

    want = _lot_title_norm(summary_ru)

    def finalize_new_lot(lot_id):
        lot_id = int(lot_id)
        # Re-read the created form and force a non-zero manual stock before
        # activation.  Without this FunPay accepts the request but keeps the
        # offer hidden, while the plugin would still bind its ID.
        if not _set_lot_active(lot_id, True):
            logger.warning(f"{LP} [AC] lot {lot_id} created, but activation request failed; mapping is not saved")
            return None, "лот создан, но FunPay оставил его скрытым"
        return lot_id, None

    try:
        after = _my_subcat_lots(int(subcat_id))
        new = [l for l in after if int(l.id) not in before]
        if len(new) == 1:
            return finalize_new_lot(new[0].id)
        if len(new) > 1:
            for l in new:
                if _lot_title_norm(getattr(l, "description", "") or getattr(l, "title", "")) == want:
                    return finalize_new_lot(l.id)
            _dbg(f"sub={subcat_id}: >1 новых лота, точного совпадения по названию нет — беру первый")
            return finalize_new_lot(new[0].id)
        for l in after:
            if _lot_title_norm(getattr(l, "description", "") or getattr(l, "title", "")) == want:
                _dbg(f"sub={subcat_id}: lot_id найден по названию (листинг с задержкой)")
                return finalize_new_lot(l.id)
    except Exception as e:
        logger.debug(f"{LP} [AC] post-list fail sub={subcat_id}: {e}")
    return None, "лот создан, но не найден в списке (задержка FunPay)"


def _build_plan_items(missing_only: bool = True, bind_existing: bool = False,
                      diagnostics: "list | None" = None, product_id=None,
                      durations_override=None) -> list:
    api = _get_api()
    if not api or not _ac_has_template():
        if not api:
            _diag_add(diagnostics, "нет API ключа KOSell")
        elif not _ac_has_template():
            _diag_add(diagnostics, "шаблон автолотов не настроен")
        return []
    tpl = _ac_tpl()
    have = _existing_lot_keys() if missing_only else set()
    items = []
    own_lots_cache = {}
    prods_by_id = {int(p["id"]): p for p in (_products(api) or [])}
    sellable = _all_sellable_games(api, diagnostics) + _steam_showcase_items(api, diagnostics)
    _dbg(f"_build_plan_items: sellable={len(sellable)} missing_only={missing_only} have={len(have)}")
    for sg in sellable:
        if product_id is not None and str(sg.get("product_id")) != str(product_id):
            continue
        block_reason = _autolot_block_reason(sg)
        if block_reason:
            _diag_add(diagnostics, "товар в стоп-листе автолотов",
                      product_id=sg.get("product_id"), name=sg.get("product_name") or sg.get("game_name"),
                      reason_detail=block_reason)
            continue
        prod = prods_by_id.get(sg["product_id"])
        if not prod:
            _diag_add(diagnostics, "товар KOSell пропал из каталога",
                      product_id=sg.get("product_id"), name=sg.get("product_name") or sg.get("game_name"))
            continue
        block_reason = _autolot_block_reason(prod, sg)
        if block_reason:
            _diag_add(diagnostics, "товар в стоп-листе автолотов",
                      product_id=sg.get("product_id"), name=sg.get("product_name") or sg.get("game_name"),
                      reason_detail=block_reason)
            continue
        durations = _durations_for(prod, tpl)
        if product_id is not None and durations_override is not None:
            mn = int(prod.get("min_hours") or 1)
            mx = int(prod.get("max_hours") or 720)
            durations = sorted({h for h in durations_override if mn <= h <= mx}, reverse=True)
        if not durations:
            _diag_add(diagnostics, "нет подходящих длительностей",
                      product_id=sg.get("product_id"), name=sg.get("game_name"),
                      min_hours=prod.get("min_hours"), max_hours=prod.get("max_hours"))
            continue
        for h in durations:
            placement = sg.get("placement") or "main"
            key = _mapping_key(sg["product_id"], h, placement)
            if missing_only and key in have:
                _diag_add(diagnostics, "уже есть привязка",
                          product_id=sg.get("product_id"), name=sg.get("game_name"), hours=h,
                          placement=placement)
                continue
            item = {
                "product_id": sg["product_id"],
                "product_name": sg["product_name"],
                "game_name": sg["game_name"],
                "subcategory_id": sg["subcategory_id"],
                "category_id": sg.get("category_id"),
                "fields": sg["fields"],
                "hours": h,
                "source": sg.get("source", "map"),
                "placement": placement,
            }
            if missing_only and bind_existing:
                sru, _sen, _fru, _fen = _build_lot_texts(item["game_name"], h, tpl,
                                                         item["product_id"], item.get("placement") or "main")
                if item.get("placement") == "steam":
                    rent_code = _make_rent_code(item["product_id"], h, "steam")
                    existing_id = _find_existing_own_lot_with_code(item["subcategory_id"], sru, rent_code, own_lots_cache)
                else:
                    existing_id = _find_existing_own_lot(item["subcategory_id"], sru, own_lots_cache)
                if existing_id:
                    if bind_existing and _append_mapping_for_lot(existing_id, item):
                        _dbg(f"auto-bind existing lot={existing_id}: {item['game_name']} {h}ч")
                    if key is not None:
                        have.add(key)
                    continue
            items.append(item)
    return sorted(items, key=lambda x: int(x.get("hours") or 0), reverse=True)


def _build_plan(user_id: int, missing_only: bool = True, product_id=None, durations=None) -> list:
    items = _build_plan_items(missing_only=missing_only, bind_existing=False,
                              product_id=product_id, durations_override=durations)
    _ac_plan[user_id] = {"items": items, "product_id": product_id,
                         "durations": durations, "ts": time.time()}
    _dbg(f"_build_plan: план из {len(items)} лотов (цены считаются лениво :D)")
    return items


def _fmt_eta(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"~{max(s, 1)} сек"
    m = s // 60
    if m < 60:
        rem = s % 60
        return f"~{m} мин" + (f" {rem} сек" if rem and m < 5 else "")
    h = m // 60
    mm = m % 60
    return f"~{h} ч" + (f" {mm} мин" if mm else "")


def _ac_progress_edit(chat_id, mid, done, total, created, skipped, failed,
                      title="Создание лотов…", verb="Создано", started=None):
    _ac_running.update({"title": title, "verb": verb, "done": done, "total": total,
                        "created": created, "skipped": skipped, "failed": failed,
                        "started": started})
    if not (chat_id and mid):
        return
    skip_part = f"   ⏭ Пропущено/уже было: {skipped}" if skipped else ""
    eta_line = ""
    if started and done < total:
        if done >= 3:
            elapsed = time.time() - started
            remaining = max(0.0, elapsed / done * (total - done))
            eta_line = f"\n⏳ Осталось: <b>{_fmt_eta(remaining)}</b>"
        else:
            eta_line = "\n⏳ Осталось: <b>подсчёт…</b>"
    txt = (f"🚀 <b>{title}</b>\n\n"
           f"<code>{_bar(done, total)}</code>\n\n"
           f"✅ {verb}: {created}{skip_part}   ❌ Ошибок: {failed}{eta_line}\n"
           f"<i>Можно свернуть — процесс идёт в фоне.</i>")
    kb = None
    # Пока процесс выполняется и не запрошена отмена — показываем кнопку «Отменить»
    if _ac_running.get("on") and not _ac_running.get("cancel"):
        kb = K()
        kb.row(B("🛑 Отменить", callback_data=f"{P}_ac_cancel"))
    try:
        bot.edit_message_text(txt, chat_id, mid, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


def _subcategory_cap(subcat_id: int, category_id=None) -> int:
    """Return FunPay's offer cap for the target subcategory."""
    try:
        sid = int(subcat_id)
    except (TypeError, ValueError):
        sid = 0
    # FunPay allows 20 offers in the shared Steam account section and 15 in
    # game-specific account sections.
    if sid == 89:
        return 20
    try:
        return int(SETTINGS.get("ac_subcat_cap", 15) or 15)
    except (TypeError, ValueError):
        return 15


def _autocreate_run(user_id: int, chat_id=None, mid=None):
    created = skipped = failed = bound_existing = 0
    rate_limited = False
    err_counter = Counter()
    err_samples: dict = {}
    auto_raise_prev = _pause_cardinal_autoraise("autocreate")
    try:
        plan = (_ac_plan.get(user_id) or {}).get("items") or []
        total = len(plan)
        existing_lots = {str(m.get("lot_id")) for m in MAPPINGS if m.get("lot_id")}
        sub_counts: dict = {}
        own_lots_cache = {}
        tpl = _ac_tpl()
        api = _get_api()
        prods_by_id = {int(p["id"]): p for p in (_products(api) or [])}
        _dbg(f"_autocreate_run: старт, лотов={total}, caps=20/15")
        started = time.time()
        _ac_progress_edit(chat_id, mid, 0, total, 0, 0, 0, started=started)

        for i, it in enumerate(plan, 1):
            # Запросили отмену — останавливаемся после текущего лота
            if _ac_running.get("cancel"):
                break
            block_reason = _autolot_block_reason(it)
            if block_reason:
                skipped += 1
                _dbg(f"skip {it.get('game_name')} {it.get('hours')}ч — стоп-лист автолотов ({block_reason})")
                if i <= 10 or i % 3 == 0 or i == total:
                    _ac_progress_edit(chat_id, mid, i, total, created, skipped, failed, started=started)
                continue
            sub_id = it["subcategory_id"]
            placement = it.get("placement") or "main"
            if sub_id not in sub_counts:
                sub_counts[sub_id] = len(_my_subcat_lots(int(sub_id), own_lots_cache))
            if _mapping_exists(it["product_id"], it["hours"], placement):
                skipped += 1
                _dbg(f"skip {it['game_name']} {it['hours']}ч — привязка уже есть")
            else:
                sru, sen, fru, fen = _build_lot_texts(it["game_name"], it["hours"], tpl,
                                                      it["product_id"], placement)
                if placement == "steam":
                    rent_code = _make_rent_code(it["product_id"], it["hours"], "steam")
                    existing_id = _find_existing_own_lot_with_code(int(sub_id), sru, rent_code, own_lots_cache)
                else:
                    existing_id = _find_existing_own_lot(int(sub_id), sru, own_lots_cache)
                if existing_id:
                    if _append_mapping_for_lot(existing_id, it):
                        bound_existing += 1
                        existing_lots.add(str(existing_id))
                        _dbg(f"bind existing lot={existing_id}: {it['game_name']} {it['hours']}ч")
                    skipped += 1
                    if i <= 10 or i % 3 == 0 or i == total:
                        _ac_progress_edit(chat_id, mid, i, total, created, skipped, failed, started=started)
                    continue
                cap = _subcategory_cap(sub_id, it.get("category_id"))
                if sub_counts[sub_id] >= cap:
                    skipped += 1
                    _dbg(f"skip {it['game_name']} {it['hours']}ч — лимит раздела {sub_id}")
                    if i <= 10 or i % 3 == 0 or i == total:
                        _ac_progress_edit(chat_id, mid, i, total, created, skipped, failed, started=started)
                    continue
                subcat_obj = _get_subcategory_obj(int(sub_id), it.get("category_id"), it["game_name"])
                prod = prods_by_id.get(it["product_id"], {"id": it["product_id"]})
                public_price = _public_price_rub(api, prod, it["hours"], tpl)
                input_price = _lot_input_price(public_price, sub_id)
                _dbg(f"create {it['game_name']} {it['hours']}ч sub={sub_id} "
                     f"pub={public_price} input={input_price}")
                try:
                    lot_id, err = _create_one_lot(int(sub_id), subcat_obj, it.get("fields") or {},
                                                  sru, sen, fru, fen, input_price, it["game_name"],
                                                  it.get("product_id"), it.get("hours"))
                except _RateLimited:
                    rate_limited = True
                    break
                if lot_id and str(lot_id) not in existing_lots:
                    ok_map = _append_mapping_for_lot(lot_id, it)
                    existing_lots.add(str(lot_id))
                    sub_counts[sub_id] += 1
                    if ok_map:
                        created += 1
                    else:
                        failed += 1
                        reason = "лот создан, но привязка уже существовала"
                        err_counter[reason] += 1
                        err_samples.setdefault(reason, f"{it['game_name']} pid={it['product_id']} sub={sub_id} ({it['hours']}ч)")
                else:
                    failed += 1
                    reason = err or "неизвестная ошибка"
                    err_counter[reason] += 1
                    err_samples.setdefault(reason, f"{it['game_name']} pid={it['product_id']} sub={sub_id} ({it['hours']}ч)")
                    _dbg(f"FAILED {it['game_name']} {it['hours']}ч sub={sub_id}: {reason}")
                time.sleep(AC_CREATE_DELAY)
            if i <= 10 or i % 3 == 0 or i == total:
                _ac_progress_edit(chat_id, mid, i, total, created, skipped, failed, started=started)
    except Exception as e:
        logger.error(f"{LP} [AC] run error: {e}", exc_info=True)
    finally:
        _restore_cardinal_autoraise(auto_raise_prev)
        _ac_running["on"] = False
        _ac_plan.pop(user_id, None)

    if rate_limited:
        done_txt = (
            f"⛔️ <b>FunPay включил лимит на создание лотов.</b>\n\n"
            f"✅ Успели создать: <b>{created}</b>\n"
            f"🔗 Привязано существующих: <b>{bound_existing}</b>\n"
            f"⏭ Пропущено/уже было: <b>{skipped}</b>\n"
            f"Привязок всего: <b>{len(MAPPINGS)}</b>\n\n"
            f"⏳ Это кулдаун FunPay (обычно <b>~8 часов</b>). Дальше создавать сейчас бессмысленно — "
            f"вернитесь позже и снова нажмите «Создать недостающие»: плагин дозальёт только оставшиеся."
        )
    elif _ac_running.get("cancel"):
        done_txt = (
            f"🛑 <b>Создание лотов остановлено вами.</b>\n\n"
            f"✅ Успели создать: <b>{created}</b>\n"
            f"🔗 Привязано существующих: <b>{bound_existing}</b>\n"
            f"⏭ Пропущено/уже было: <b>{skipped}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>\n\n"
            f"Созданные лоты остались на месте. В любой момент нажмите "
            f"«Создать недостающие» — плагин продолжит с того места, где остановился."
        )
    else:
        done_txt = (
            f"🤖 <b>Создание лотов завершено.</b>\n\n"
            f"✅ Создано: <b>{created}</b>\n"
            f"🔗 Привязано существующих: <b>{bound_existing}</b>\n"
            f"⏭ Пропущено/уже было: <b>{skipped}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>\n"
            f"Привязок всего: <b>{len(MAPPINGS)}</b>"
        )
        if failed and err_counter:
            done_txt += "\n\n⚠️ <b>Причины ошибок:</b>"
            for reason, cnt in err_counter.most_common(6):
                sample = err_samples.get(reason, "")
                done_txt += f"\n• <b>{cnt}×</b> {_html.escape(reason)}"
                if sample:
                    done_txt += f"\n   <i>напр.: {_html.escape(sample)}</i>"
            extra = len(err_counter) - 6
            if extra > 0:
                done_txt += f"\n…и ещё {extra} тип(ов) ошибок."
    if chat_id and mid:
        try:
            kb = K()
            kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
            bot.edit_message_text(done_txt, chat_id, mid, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        notify_admins_info(done_txt)


def _reprice_run(user_id: int, chat_id=None, mid=None, product_id=None):
    account = cardinal_instance.account
    api = _get_api()
    tpl = _ac_tpl()
    targets = [m for m in MAPPINGS
               if m.get("auto_created") and m.get("lot_id")
               and m.get("product_id") is not None and m.get("hours") is not None
               and (product_id is None or str(m.get("product_id")) == str(product_id))]
    total = len(targets)
    updated = unchanged = failed = 0
    rate_limited = False
    started = time.time()
    auto_raise_prev = _pause_cardinal_autoraise("reprice")

    def _is_rate_limit(msg: str) -> bool:
        low = (msg or "").lower()
        return _is_funpay_rate_limit(low) or "слишком часто" in low

    def _is_missing_offer(msg: str) -> bool:
        low = (msg or "").lower()
        return ("предложение не найдено" in low or "страница не найдена" in low
                or "not found" in low or "404" in low)

    try:
        logger.info(f"{LP} [AC] reprice start: targets={total}, chat_id={chat_id}, mid={mid}")
        _ac_progress_edit(chat_id, mid, 0, total, 0, 0, 0, "Пересчёт цен…", "Обновлено", started=started)
        logger.info(f"{LP} [AC] reprice loading KOSell products")
        prods_by_id = {int(p["id"]): p for p in (_products(api) or [])}
        logger.info(f"{LP} [AC] reprice products loaded: {len(prods_by_id)}")
        for i, m in enumerate(targets, 1):
            # Запросили отмену — останавливаемся после текущего лота
            if _ac_running.get("cancel"):
                break
            did_write = False
            try:
                prod = prods_by_id.get(int(m["product_id"]), {"id": m["product_id"]})
                public = _public_price_rub(api, prod, int(m["hours"]), tpl)
                inp = _lot_input_price(public, int(m["subcategory_id"]))
                lf = account.get_lot_fields(int(m["lot_id"]))
                old = lf.fields.get("price")
                try:
                    old_val = float(str(old).replace(",", ".")) if old not in (None, "") else None
                except Exception:
                    old_val = None
                title_changed = _sanitize_summary_fields(lf.fields)
                price_changed = old_val is None or abs(round(old_val, 2) - round(inp, 2)) >= 0.005
                if not price_changed and not title_changed:
                    unchanged += 1
                    _dbg(f"reprice SKIP lot={m['lot_id']} {m['hours']}ч цена не изменилась ({old_val})")
                else:
                    if price_changed:
                        lf.price = inp
                    lf.fields["csrf_token"] = account.csrf_token
                    account.save_lot(lf)
                    did_write = True
                    updated += 1
                    _dbg(f"reprice lot={m['lot_id']} {m['hours']}ч {old}→{inp}")
            except Exception as e:
                msg = str(e)
                if _is_rate_limit(msg):
                    _dbg(f"reprice rate-limit FunPay на lot={m.get('lot_id')}: остановка прогона")
                    rate_limited = True
                    break
                if _is_missing_offer(msg):
                    failed += 1
                    _dbg(f"reprice MISS lot={m.get('lot_id')}: offer not found")
                    continue
                failed += 1
                _dbg(f"reprice FAIL lot={m.get('lot_id')}: {msg[-200:]}")
            finally:
                _ac_progress_edit(chat_id, mid, i, total, updated, unchanged, failed,
                                  "Пересчёт цен…", "Обновлено", started=started)
            if did_write:
                time.sleep(AC_CREATE_DELAY + random.uniform(0.0, 1.0))
            else:
                time.sleep(0.4 + random.uniform(0.0, 0.4))
    except Exception as e:
        logger.error(f"{LP} [AC] reprice error: {e}", exc_info=True)
    finally:
        _restore_cardinal_autoraise(auto_raise_prev)
        _ac_running["on"] = False

    floor = _money(_min_price_floor(tpl))
    if rate_limited:
        done = (f"⛔️ <b>FunPay лимит на изменение цен.</b>\n\n"
                f"✅ Обновлено: <b>{updated}</b>\n"
                f"➖ Без изменений: <b>{unchanged}</b>\n"
                f"❌ Ошибок: <b>{failed}</b>\n\n"
                f"⏳ Это кулдаун FunPay — вернитесь позже и снова нажмите «Пересчитать цены»: "
                f"уже выставленные цены пропустятся, плагин до-обновит только оставшиеся.")
    else:
        done = (f"♻️ <b>Пересчёт цен завершён.</b>\n\n"
                f"✅ Обновлено: <b>{updated}</b>\n"
                f"➖ Без изменений: <b>{unchanged}</b>\n"
                f"❌ Ошибок: <b>{failed}</b>\n\n"
                f"Наценка: <b>{_pct(tpl.get('markup_percent', 0))}%</b>, мин. цена: <b>{floor}₽</b>")
    if _ac_running.get("cancel") and not rate_limited:
        done = (f"🛑 <b>Пересчёт цен остановлен вами.</b>\n\n"
                f"✅ Уже обновлено: <b>{updated}</b>\n"
                f"➖ Без изменений: <b>{unchanged}</b>\n"
                f"❌ Ошибок: <b>{failed}</b>\n\n"
                f"Обновлённые лоты оставили свои цены. Нажмите «Пересчитать цены» позже — "
                f"уже обновленные пропустятся, плагин до-обновит только оставшиеся.")
    if chat_id and mid:
        try:
            kb = K()
            kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
            bot.edit_message_text(done, chat_id, mid, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        notify_admins_info(done)


def _reprice_thread_entry(user_id: int, chat_id=None, mid=None, product_id=None):
    logger.info(f"{LP} [AC] reprice thread entered: user_id={user_id}, chat_id={chat_id}, mid={mid}")
    try:
        _reprice_run(user_id, chat_id, mid, product_id)
    except Exception as e:
        logger.error(f"{LP} [AC] reprice thread crashed: {e}", exc_info=True)
        _ac_running["on"] = False
        if chat_id and mid:
            try:
                kb = K()
                kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
                bot.edit_message_text(
                    f"❌ <b>Пересчёт упал.</b>\n\n<code>{_html.escape(str(e)[:800])}</code>",
                    chat_id,
                    mid,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                pass


def _delete_plugin_run(user_id: int, chat_id=None, mid=None):
    account = cardinal_instance.account
    deleted = failed = 0
    targets = [m for m in MAPPINGS if m.get("lot_id")]
    total = len(targets)
    _dbg(f"delete_plugin: к удалению {total} привязанных лотов")
    started = time.time()
    try:
        _ac_progress_edit(chat_id, mid, 0, total, 0, 0, failed, "Удаление лотов…", "Удалено", started=started)
        done_ids = set()
        for i, m in enumerate(targets, 1):
            # Запросили отмену — останавливаемся после текущего лота
            if _ac_running.get("cancel"):
                break
            lot_id = m.get("lot_id")
            try:
                account.delete_lot(int(lot_id))
                deleted += 1
                done_ids.add(str(lot_id))
                _dbg(f"delete OK lot={lot_id} ({m.get('product_name')} {m.get('hours')}ч)")
            except Exception as e:
                msg = str(e)
                if "не найден" in msg.lower() or "not found" in msg.lower() or "offer" in msg.lower():
                    done_ids.add(str(lot_id))
                    _dbg(f"delete lot={lot_id}: уже нет на FunPay, чищу привязку")
                else:
                    failed += 1
                    _dbg(f"delete FAIL lot={lot_id}: {msg[-160:]}")
            time.sleep(AC_DELETE_DELAY)
            if i % 10 == 0 or i == total:
                _ac_progress_edit(chat_id, mid, i, total, deleted, 0, failed,
                                  "Удаление лотов…", "Удалено", started=started)
        MAPPINGS[:] = [m for m in MAPPINGS if str(m.get("lot_id")) not in done_ids]
        save_mappings()
        if done_ids:
            for lid in done_ids:
                HIDDEN.pop(str(lid), None)
            save_hidden()
    except Exception as e:
        logger.error(f"{LP} [AC] delete_plugin error: {e}", exc_info=True)
    finally:
        _ac_running["on"] = False

    done = (f"🗑 <b>Удаление лотов завершено.</b>\n\n"
            f"✅ Удалено: <b>{deleted}</b>\n"
            f"❌ Ошибок: <b>{failed}</b>\n"
            f"🔗 Осталось привязок: <b>{len(MAPPINGS)}</b>")
    if _ac_running.get("cancel"):
        done = (f"🛑 <b>Удаление лотов остановлено вами.</b>\n\n"
                f"✅ Удалено: <b>{deleted}</b>\n"
                f"❌ Ошибок: <b>{failed}</b>\n"
                f"🔗 Осталось привязок: <b>{len(MAPPINGS)}</b>\n\n"
                f"Удалённые лоты убраны с FunPay. Оставшиеся можно удалить позже.")
    if chat_id and mid:
        try:
            kb = K()
            kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
            bot.edit_message_text(done, chat_id, mid, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        notify_admins_info(done)


PLUGIN_HELP_TEXT = (
    f"<b>ℹ️ Справка {NAME}</b>\n\n"
    "<b>Команды покупателя (в чат FunPay)</b>\n"
    "Команда должна быть точным первым словом сообщения: <code>!продл</code> не сработает.\n"
    "• <code>!кодстим логин</code> — Steam Guard код для выданного аккаунта\n"
    "• <code>!продлить логин</code> — продление определенного аккаунта если у покупателя несколько арендованных аккаунтов (после повторной оплаты)\n"
    "• <code>!друг</code> — режим «для друга»; <code>!friend</code> тоже понимается как алиас\n\n"
    "<b>Обычная выдача (режим «для друга» выключен)</b>\n"
    "• Первый заказ по игре — новый аккаунт.\n"
    "• Повторный заказ по той же игре — продление того же аккаунта.\n"
    "• Если покупатель берёт <b>1 шт</b>, время за заказ: часы в привязке лота.\n"
    "• Если покупатель берёт <b>несколько шт</b>, бот сначала спросит: нужны отдельные аккаунты или один аккаунт на суммарное время.\n"
    "  Ответ <b>Да</b> → отдельные аккаунты по времени из привязки. Ответ <b>Нет</b> → один аккаунт/продление на часы × qty.\n"
    "  Пример: в привязке 3 ч, покупатель взял 1 шт → <b>3 ч</b> на одном аккаунте.\n"
    "• Лимит KOSell на одну операцию: от 1 до 720 ч. Повторными продлениями суммарно можно больше 720 ч.\n\n"
    "<b>Режим <code>!друг</code> (для друга)</b>\n"
    "• Покупатель пишет <code>!друг</code>, затем оплачивает лот.\n"
    "• Следующая оплата выдаёт <b>новый аккаунт</b>, а не продлевает текущий.\n"
    "• При qty > 1 бот всё равно спросит покупателя: несколько отдельных аккаунтов или один новый аккаунт на суммарное время.\n"
    "• После этой оплаты режим выключается автоматически.\n\n"
    "<b>Коды привязки</b>\n"
    "• В подробном описании автолотов есть код вида <code>rent:ID-HOURS</code>; в название он не добавляется.\n"
    "• Для ручного лота можно добавить код из карточки привязки или <code>rent:12345678</code> с ID лота.\n"
    "• Если FunPay не передал ID лота, бот ищет этот код в описании заказа.\n\n"
    "<b>Привязки лотов</b>\n"
    "• Привязка по ID лота — переименование лота не ломает выдачу.\n"
    "• «Часы за 1 шт» в привязке — сколько часов аренды за одну штуку в заказе.\n\n"
    "<b>Частые вопросы</b>\n"
    "• Лот «цена за 1 час», qty=3 → бот спрашивает: 3 аккаунта по 1 ч или 1 аккаунт на 3 ч.\n"
    "• Один аккаунт на 3 ч → qty=3 в часовом лоте или лот с 3 ч в привязке.\n"
    "• Несколько активных аккаунтов одной игры — бот спросит, какой продлить (!продлить логин)."
)


def tg_info(call):
    kb = K()
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, PLUGIN_HELP_TEXT, kb)


def _update_banner() -> str:
    st = _update_state
    if st.get("pending_restart"):
        return (f"⬆️ <b>Доступна версия v{st.get('latest')}</b> — обновление загружено.\n"
                f"Нажмите «♻️ Перезапустить FPC», чтобы применить.\n\n")
    return ""


def tg_restart(call):
    try:
        bot.answer_callback_query(call.id, "Перезапуск FPC…")
    except Exception:
        pass
    try:
        bot.edit_message_text(f"<b>{LP}</b>\n♻️ Перезапускаю FPC, применяю обновление…",
                              call.message.chat.id, call.message.id, parse_mode="HTML")
    except Exception:
        pass
    try:
        from Utils.cardinal_tools import restart_program
        restart_program()
    except Exception as e:
        logger.error(f"{LP} restart error: {e}")
        try:
            bot.answer_callback_query(call.id, f"Не удалось перезапустить: {e}", show_alert=True)
        except Exception:
            pass


def _run_status_line() -> str:
    if not _ac_running.get("on"):
        return ""
    rt = _ac_running.get("title", "Процесс")
    rd, rtot = _ac_running.get("done", 0), _ac_running.get("total", 0)
    pct = int(rd * 100 / rtot) if rtot else 0
    rv, rc = _ac_running.get("verb", "Готово"), _ac_running.get("created", 0)
    return f"⏳ <b>{rt}</b> {pct}% · {rv}: {rc}/{rtot}"


def _menu_balance_line() -> str:
    """Значение баланса для шапки меню (кэш 60с, без блокировки при сбоях API)."""
    try:
        now = time.time()
        if now - _bal_cache["ts"] > 60:
            _bal_cache["data"] = None
        if _bal_cache["data"] is None:
            api = _get_api()
            if api:
                bal = api.balance()
                # Обновляем ts даже при ошибке, чтобы недоступность API
                # не задерживала каждое открытие меню.
                _bal_cache["ts"] = now
                if bal:
                    _bal_cache["data"] = bal
        bal = _bal_cache["data"]
        if not bal:
            return "—"
        cur = SETTINGS.get("currency", "RUB")
        if cur == "RUB":
            sym, amount = "₽", bal.get("balance_rub", 0) or 0
        else:
            sym, amount = "$", bal.get("balance_usd", 0) or 0
        try:
            amount = float(amount)
        except Exception:
            amount = 0
        return f"{amount:g} {sym}"
    except Exception:
        return "—"


def _menu_active_rentals_count() -> int:
    try:
        total = 0
        for bk, lst in RENTALS.items():
            total += len([r for r in lst if (r.get("expires_ts") or 0) > _now() - 60])
        return total
    except Exception:
        return 0


def tg_main(call):
    s = SETTINGS
    en = s.get("enabled")
    _ui_page["menu_view"] = "main"
    kb = K()
    if _ac_running.get("on"):
        kb.row(B(f"⏳ {_ac_running.get('title','Процесс')} — обновить", callback_data=f"{P}_main"))

    # ---- Главный переключатель ----
    kb.row(B(f"{'🟢 Автовыдача: ВКЛ' if en else '🔴 Автовыдача: ВЫКЛ'}",
             callback_data=f"{P}_toggle:enabled"))

    # ---- Быстрые действия ----
    kb.row(B("📊 Статистика", callback_data=f"{P}_stats"),
           B("🔗 Привязки", callback_data=f"{P}_maps:{_page_of('maps')}"))
    kb.row(B("📦 Создание лотов", callback_data=f"{P}_ac"),
           B("⚙️ Параметры", callback_data=f"{P}_params"))
    kb.row(B("✏️ Тексты", callback_data=f"{P}_texts"),
           B("ℹ️ Справка", callback_data=f"{P}_info"))

    # ---- Настройки выдачи и уведомлений (главные) ----
    kb.row(B(f"{_chk('auto_refund')} Авто-возврат", callback_data=f"{P}_toggle:auto_refund"),
           B(f"{_chk('notify_sales')} О продажах", callback_data=f"{P}_toggle:notify_sales"))
    kb.row(B(f"{_chk('price_guard_enabled')} Защита цены", callback_data=f"{P}_toggle:price_guard_enabled"),
           B(f"{_chk('rotate_password_on_delivery')} Смена пароля", callback_data=f"{P}_toggle:rotate_password_on_delivery"))
    kb.row(B(f"{_chk('full_logout_on_delivery')} Разлогин Steam", callback_data=f"{P}_toggle:full_logout_on_delivery"),
           B(f"{_chk('delivery_prefetch_enabled')} Подготовка (delivery)", callback_data=f"{P}_toggle:delivery_prefetch_enabled"))
    kb.row(B(f"{_chk('review_bonus_enabled')} Бонус за отзыв", callback_data=f"{P}_toggle:review_bonus_enabled"),
           B(f"{_chk('notify_rental_end')} Конец аренды", callback_data=f"{P}_toggle:notify_rental_end"))

    # ---- Все настройки / лоты ----
    kb.row(B("🎛 Все тумблеры и лоты", callback_data=f"{P}_full"))

    if _update_state.get("pending_restart"):
        kb.row(B(f"♻️ Перезапустить FPC (обновление v{_update_state.get('latest')})", callback_data=f"{P}_restart"))

    # ---- Сервис ----
    kb.row(B("🔑 API ключ", callback_data=f"{P}_apikey"))
    kb.row(B("💰 Баланс", callback_data=f"{P}_balance"))
    kb.row(_back_btn(f"{CBT.EDIT_PLUGIN}:{UUID}:0"))

    key_set = "✅" if s.get("api_key") else "❌ не задан"
    status = _run_status_line()
    text = (
        f"<b>⚙️ {NAME}</b> <i>v{VERSION}</i>\n\n"
        + (_update_banner() or "")
        + (status + "\n\n" if status else "")
        + f"🔑 API ключ: {key_set}\n"
        f"💰 Баланс KOSell: <b>{_menu_balance_line()}</b>\n"
        f"💱 Валюта: <b>{s.get('currency')}</b>\n"
        f"🔗 Привязок: <b>{len(MAPPINGS)}</b> · 🧊 Скрыто: <b>{len(HIDDEN)}</b>\n"
        f"🎮 Активных аренд: <b>{_menu_active_rentals_count()}</b>\n\n"
        f"Автовыдача и ключевые режимы — кнопками выше, остальное — «🎛 Все тумблеры и лоты»."
    )
    _edit(call, text, kb)


def _full_toggles_kb(page: int = 0) -> dict:
    """Все тумблеры, сгруппированные по секциям, постранично (до 8 в ряду)."""
    sections = [
        ("ЛОТЫ НА FUNPAY", [
            ("lot_sync_enabled", "Синхронизация лотов KOSell"),
            ("auto_hide_no_stock", "Скрывать: нет аккаунтов"),
            ("auto_hide_zero_balance", "Скрывать: нет баланса"),
            ("steam_showcase_enabled", "Steam-витрина lots/89"),
        ]),
        ("ВЫДАЧА И БЕЗОПАСНОСТЬ", [
            ("multi_qty_confirm_enabled", "Спрашивать при >1 шт"),
            ("price_guard_enabled", "Защита от минусовой цены"),
            ("auto_refund", "Авто-возврат при сбое"),
            ("rotate_password_on_delivery", "Смена пароля при выдаче"),
            ("full_logout_on_delivery", "Разлогин Steam при выдаче"),
            ("delivery_prefetch_enabled", "Подготовка выдачи (delivery)"),
        ]),
        ("УВЕДОМЛЕНИЯ И БОНУСЫ", [
            ("notify_rental_end", "Об окончании аренды"),
            ("notify_sales", "О продажах"),
            ("review_bonus_enabled", "Бонус за отзыв"),
        ]),
    ]
    per = 8
    items = []
    for title, toggles in sections:
        items.append(("__section__", title))
        for key, label in toggles:
            items.append((key, label))
    total = len(items)
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1)) if pages else 0
    start = page * per
    chunk = items[start:start + per]

    kb = K()
    for item, label in chunk:
        if item == "__section__":
            kb.row(B(f"— {label} —", callback_data=f"{P}_noop"))
        else:
            kb.row(B(f"{_chk(item)} {label}", callback_data=f"{P}_toggle:{item}"))
    nav, _ = _nav(f"{P}_fullpage:", page, total, per)
    if nav:
        kb.row(*nav)
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    return kb


def _full_toggles_text() -> str:
    return ("<b>🎛 Все тумблеры и лоты</b>\n\n"
            "Включённые функции — <b>🟢</b>, выключенные — <b>🔴</b>. Нажмите, чтобы переключить.\n\n"
            "Лоты: синхронизация и авто-скрытие при отсутствии аккаунтов/баланса.\n"
            "Выдача: защита от минусовой цены, авто-возврат, смена пароля/разлогин, delivery.\n"
            "Уведомления: продажи, конец аренды, бонус за отзыв.")


def tg_full(call):
    _ui_page["menu_view"] = "full"
    _ui_page["full_page"] = 0
    _edit(call, _full_toggles_text(), _full_toggles_kb(0))


def tg_fullpage(call):
    try:
        page = int(call.data.split(":", 1)[1])
    except Exception:
        page = 0
    _ui_page["menu_view"] = "full"
    _ui_page["full_page"] = page
    _edit(call, _full_toggles_text(), _full_toggles_kb(page))


def _nav(prefix: str, page: int, total: int, per: int):
    pages = max(1, (total + per - 1) // per)
    row = []
    if page > 0:
        row.append(B("‹", callback_data=f"{prefix}{page-1}"))
    row.append(B(f"{page+1}/{pages}", callback_data=f"{P}_noop"))
    if (page + 1) * per < total:
        row.append(B("›", callback_data=f"{prefix}{page+1}"))
    return (row if len(row) > 1 else []), pages


def tg_noop(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _chk(key) -> str:
    return "🟢" if SETTINGS.get(key) else "🔴"


def _edit(call, text, kb):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.id, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        msg = str(e).lower()
        if "not modified" not in msg:
            try:
                bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                pass
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def _wiz_msg(chat_id, uid, text, kb):
    w = _ac_wizard.setdefault(uid, {})
    mid = w.get("message_id")
    if mid:
        try:
            bot.edit_message_text(text, chat_id, mid, reply_markup=kb, parse_mode="HTML")
            return mid
        except Exception:
            pass
    m = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML")
    w["message_id"] = m.message_id
    return m.message_id


def tg_toggle(call):
    key = call.data.split(":", 1)[1]
    SETTINGS[key] = not SETTINGS.get(key, False)
    save_settings()
    # Возвращаемся на тот экран, откуда нажата кнопка
    # (главное меню или «Все тумблеры и лоты» с сохранением страницы)
    if _ui_page.get("menu_view") == "full":
        page = int(_ui_page.get("full_page", 0) or 0)
        _edit(call, _full_toggles_text(), _full_toggles_kb(page))
    else:
        tg_main(call)


def tg_apikey(call):
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_set_apikey")
    cur = SETTINGS.get("api_key", "")
    masked = f"{cur[:10]}...{cur[-4:]}" if len(cur) > 16 else ("(не задан)" if not cur else cur)
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, f"<b>🔑 API ключ KOSell</b>\n\nТекущий: <code>{_html.escape(masked)}</code>\n\nОтправьте новый ключ:", kb)


def tg_balance(call):
    api = _get_api()
    if not api:
        bot.answer_callback_query(call.id, "❌ API ключ не задан", show_alert=True)
        return
    bal = api.balance()
    if not bal:
        bot.answer_callback_query(call.id, "❌ Ошибка получения баланса", show_alert=True)
        return
    kb = K()
    kb.add(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    text = (
        f"<b>💰 Баланс KOSell</b>\n\n"
        f"Пользователь: <b>{_html.escape(str(bal.get('username', '—')))}</b>\n"
        f"USD: <b>{bal.get('balance_usd', 0)}</b>\n"
        f"RUB: <b>{bal.get('balance_rub', 0)}</b>\n"
    )
    _edit(call, text, kb)


def tg_stats(call):
    earned = float(STATS.get("funpay_earned_rub", 0) or 0)
    spent = float(STATS.get("kosell_spent_rub", 0) or 0)
    profit = earned - spent
    margin = (profit / earned * 100) if earned > 0 else 0
    active = sum(len(_active_rentals(str(k))) for k in RENTALS)
    products = []
    for item in (STATS.get("by_product") or {}).values():
        try:
            p = float(item.get("earned_rub", 0) or 0) - float(item.get("spent_rub", 0) or 0)
        except Exception:
            p = 0.0
        products.append((p, item))
    products.sort(key=lambda x: x[0], reverse=True)

    lines = [
        "<b>📊 Статистика KOSellRent</b>",
        "",
        f"Заказов FunPay: <b>{int(STATS.get('orders_total', 0) or 0)}</b>",
        f"Выдач аккаунтов: <b>{int(STATS.get('rentals_total', 0) or 0)}</b>",
        f"Продлений: <b>{int(STATS.get('extensions_total', 0) or 0)}</b>",
        f"Бонусов за отзыв: <b>{int(STATS.get('review_bonuses_total', 0) or 0)}</b>",
        f"Активных аренд сейчас: <b>{active}</b>",
        f"Выдано часов: <b>{_money(STATS.get('hours_total', 0))}</b>",
        "",
        f"Заработано на FunPay: <b>{_money(earned)}₽</b>",
        f"Потрачено на KOSell: <b>{_money(spent)}₽</b>",
        f"Чистый заработок: <b>{_money(profit)}₽</b>",
        f"Маржа: <b>{_money(margin)}%</b>",
    ]

    if products:
        lines.append("")
        lines.append("<b>Топ товаров:</b>")
        for profit_value, item in products[:8]:
            name = _html.escape(str(item.get("name") or "аккаунт")[:42])
            earned_i = float(item.get("earned_rub", 0) or 0)
            spent_i = float(item.get("spent_rub", 0) or 0)
            lines.append(
                f"• {name}: <b>{_money(profit_value)}₽</b> "
                f"(FP {_money(earned_i)}₽ / KOSell {_money(spent_i)}₽)"
            )

    kb = K()
    kb.add(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, "\n".join(lines), kb)


_ui_page: dict = {}


def _save_page(key: str, page):
    try:
        _ui_page[key] = max(0, int(page))
    except Exception:
        _ui_page[key] = 0


def _page_of(key: str) -> int:
    return _ui_page.get(key, 0)


def _lot_link(lot_id) -> str:
    return f"https://funpay.com/lots/offer?id={lot_id}" if lot_id else ""


def tg_maps(call):
    page = int(call.data.split(":")[1]) if ":" in call.data else 0
    if page * 6 >= len(MAPPINGS):
        page = max(0, (len(MAPPINGS) - 1) // 6)
    _save_page("maps", page)
    per = 6
    start = page * per
    chunk = MAPPINGS[start:start + per]
    kb = K()
    for i, m in enumerate(chunk):
        idx = start + i
        label = f"{m.get('product_name', '?')} · {m.get('hours', '?')}ч · лот {m.get('lot_id', '?')}"
        kb.row(B(label[:60], callback_data=f"{P}_map:{idx}"))
    nav, _ = _nav(f"{P}_maps:", page, len(MAPPINGS), per)
    if nav:
        kb.row(*nav)
    kb.row(B("➕ Добавить привязку", callback_data=f"{P}_addmap:0"))
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, f"<b>🔗 Привязки лотов ({len(MAPPINGS)})</b>\n\n"
                f"Каждая привязка: лот FunPay → товар KOSell + кол-во часов за 1 шт.\n"
                f"Один товар можно привязать к нескольким лотам — при отсутствии аккаунтов "
                f"скроются все эти лоты.", kb)


def tg_map_detail(call):
    idx = int(call.data.split(":")[1])
    if idx >= len(MAPPINGS):
        bot.answer_callback_query(call.id, "Не найдено", show_alert=True)
        return
    m = MAPPINGS[idx]
    link = _lot_link(m.get("lot_id"))
    kb = K()
    kb.row(B("⏱ Часы", callback_data=f"{P}_map_hours:{idx}"), B("💱 Валюта", callback_data=f"{P}_map_cur:{idx}"))
    kb.row(B("🗑 Удалить", callback_data=f"{P}_map_del:{idx}"))
    kb.row(_back_btn(f"{P}_maps:{_page_of('maps')}"))
    text = (
        f"<b>🔗 Привязка</b>\n\n"
        f"Товар KOSell: <b>{_html.escape(str(m.get('product_name', '?')))}</b> (id {m.get('product_id')})\n"
        f"Лот FunPay: <code>{m.get('lot_id', '—')}</code>"
        + (f" · <a href=\"{link}\">открыть</a>\n" if link else "\n")
        + f"Подкатегория: <code>{m.get('subcategory_id', '—')}</code>\n"
        f"Ключевое слово: <code>{_html.escape(str(m.get('title_keyword', '—')))}</code>\n"
        f"Код привязки: <code>{_html.escape(_mapping_rent_code(m))}</code>\n"
        f"Часов за 1 шт: <b>{m.get('hours', '?')}</b>\n"
        f"Валюта: <b>{m.get('currency') or SETTINGS.get('currency')}</b>\n"
    )
    _edit(call, text, kb)


def tg_map_hours(call):
    idx = int(call.data.split(":")[1])
    if idx >= len(MAPPINGS):
        bot.answer_callback_query(call.id, "Не найдено", show_alert=True)
        return
    m = MAPPINGS[idx]
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_set_hours:{idx}")
    kb = K()
    link = _lot_link(m.get("lot_id"))
    if link:
        kb.row(B("🔗 Открыть лот на FunPay", url=link))
    kb.add(B("❌ Отмена", callback_data=f"{P}_map:{idx}"))
    name = _html.escape(str(m.get("product_name", "?")))
    lot_line = f"<code>{m.get('lot_id', '—')}</code>" + (f" · <a href=\"{link}\">открыть</a>" if link else "")
    _edit(call, f"<b>⏱ Часы аренды</b>\n\n"
                f"Товар: <b>{name}</b>\n"
                f"Лот FunPay: {lot_line}\n"
                f"Текущее значение: <b>{m.get('hours', '?')} ч</b>\n\n"
                f"Сколько часов аренды выдавать за покупку 1 шт этого лота?\n"
                f"Введите число от 3 до 720:", kb)


def tg_map_cur(call):
    idx = int(call.data.split(":")[1])
    if idx >= len(MAPPINGS):
        bot.answer_callback_query(call.id, "Не найдено", show_alert=True)
        return
    m = MAPPINGS[idx]
    m["currency"] = "USD" if (m.get("currency") or SETTINGS.get("currency")) == "RUB" else "RUB"
    save_mappings()
    tg_map_detail(call)


def tg_map_del(call):
    idx = int(call.data.split(":")[1])
    kb = K()
    kb.row(B("✅ Удалить", callback_data=f"{P}_map_dely:{idx}"), B("❌ Нет", callback_data=f"{P}_map:{idx}"))
    _edit(call, "Удалить эту привязку?", kb)


def tg_map_del_yes(call):
    idx = int(call.data.split(":")[1])
    if idx < len(MAPPINGS):
        MAPPINGS.pop(idx)
        save_mappings()
    bot.answer_callback_query(call.id, "✅ Удалено")
    call.data = f"{P}_maps:{_page_of('maps')}"
    tg_maps(call)


_addmap_tmp: dict = {}


def tg_addmap(call):
    page = int(call.data.split(":")[1]) if ":" in call.data else 0
    lots = []
    try:
        if cardinal_instance and cardinal_instance.profile:
            lots = cardinal_instance.profile.get_lots()
    except Exception as e:
        logger.error(f"{LP} get_lots failed: {e}")
    per = 7
    start = page * per
    chunk = lots[start:start + per]
    kb = K()
    for lot in chunk:
        title = (lot.description or str(lot.id))[:45]
        kb.row(B(f"{title} (id {lot.id})"[:60], callback_data=f"{P}_picklot:{lot.id}:{page}"))
    nav, _ = _nav(f"{P}_addmap:", page, len(lots), per)
    if nav:
        kb.row(*nav)
    kb.row(B("✍️ Ввести Lot ID / ссылку вручную", callback_data=f"{P}_addmap_manual"))
    kb.row(_back_btn(f"{P}_maps:{_page_of('maps')}"))
    if not lots:
        _edit(call, "Лоты FunPay не загружены. "
                    "Нажмите «Ввести Lot ID / ссылку вручную».", kb)
    else:
        _edit(call, "<b>➕ Шаг 1/3 — лот FunPay</b>\nВыберите свой лот, к которому привяжем товар KOSell:", kb)


def _load_products_cache(user_id):
    api = _get_api()
    products = api.products() if api else None
    info = _addmap_tmp.setdefault(user_id, {})
    info["_all"] = products if products else []
    info.pop("_filtered", None)
    return products


def _product_list_kb(user_id, page: int):
    info = _addmap_tmp.get(user_id, {})
    products = info.get("_filtered")
    if products is None:
        products = info.get("_all", [])
    per = 8
    start = page * per
    chunk = products[start:start + per]
    kb = K()
    for p in chunk:
        kb.row(B(f"{p.get('name')} · {p.get('available_accounts', 0)} шт"[:60],
                 callback_data=f"{P}_pickprod:{p.get('id')}"))
    nav, _ = _nav(f"{P}_prodlist:", page, len(products), per)
    if nav:
        kb.row(*nav)
    kb.row(B("🔍 Поиск по названию", callback_data=f"{P}_prodsearch"))
    kb.row(B("✍️ Ввести ID товара вручную", callback_data=f"{P}_prodmanual"))
    kb.row(B("❌ Отмена", callback_data=f"{P}_maps:{_page_of('maps')}"))
    if not products:
        if info.get("_filtered") is not None:
            text = ("<b>➕ Шаг 2/3 — товар KOSell</b>\n\nПо запросу ничего не найдено. "
                    "Нажмите «Поиск» снова или введите ID товара вручную.")
        else:
            text = ("<b>➕ Шаг 2/3 — товар KOSell</b>\n\nСписок товаров пуст "
                    "(проверьте API ключ) — можно ввести ID товара вручную.")
    else:
        flt = " (фильтр)" if info.get("_filtered") is not None else ""
        text = (f"<b>➕ Шаг 2/3 — товар KOSell{flt}</b>\n\n"
                f"Это привязка игры с KOSell к лоту FunPay.\n"
                f"Выберите товар из списка или задайте ID вручную:")
    return text, kb


def tg_pick_lot(call):
    parts = call.data.split(":")
    lot_id = parts[1]
    lots = []
    try:
        lots = cardinal_instance.profile.get_lots()
    except Exception:
        pass
    subcat, kw, rent_code = None, "", ""
    for lot in lots:
        if str(lot.id) == str(lot_id):
            subcat = getattr(getattr(lot, "subcategory", None), "id", None)
            desc = lot.description or ""
            kw = desc[:40]
            codes = _extract_rent_codes(desc)
            rent_code = codes[0] if codes else ""
            break
    _addmap_tmp[call.from_user.id] = {"lot_id": lot_id, "subcategory_id": subcat,
                                      "title_keyword": kw, "rent_code": rent_code}
    _load_products_cache(call.from_user.id)
    text, kb = _product_list_kb(call.from_user.id, 0)
    _edit(call, text, kb)


def tg_addmap_manual(call):
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_manual_lot")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_maps:{_page_of('maps')}"))
    _edit(call, "<b>➕ Шаг 1/3 — лот FunPay</b>\n\nОтправьте ID лота или ссылку на лот FunPay\n"
                "(напр. <code>63635527</code> или <code>https://funpay.com/lots/offer?id=63635527</code>):", kb)


def tg_prodlist(call):
    page = int(call.data.split(":")[1]) if ":" in call.data else 0
    text, kb = _product_list_kb(call.from_user.id, page)
    _edit(call, text, kb)


def tg_prodsearch(call):
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_prodsearch")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_maps:{_page_of('maps')}"))
    _edit(call, "<b>🔍 Поиск товара KOSell</b>\n\nВведите часть названия игры (напр. <code>counter</code>):", kb)


def tg_prodmanual(call):
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_prodmanual")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_maps:{_page_of('maps')}"))
    _edit(call, "<b>✍️ ID товара KOSell</b>\n\nОтправьте числовой ID товара (из каталога KOSell):", kb)


def _resolve_product_name(user_id, pid) -> str:
    for p in (_addmap_tmp.get(user_id, {}).get("_all") or []):
        if str(p.get("id")) == str(pid):
            return p.get("name") or f"#{pid}"
    return f"#{pid}"


def tg_pick_product(call):
    pid = call.data.split(":")[1]
    info = _addmap_tmp.setdefault(call.from_user.id, {})
    info["product_id"] = int(pid) if str(pid).isdigit() else pid
    info["product_name"] = _resolve_product_name(call.from_user.id, pid)
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_set_newhours")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_maps:{_page_of('maps')}"))
    _edit(call, f"<b>➕ Шаг 3/3 — часы</b>\n\nТовар: <b>{_html.escape(str(info['product_name']))}</b>\n\n"
                f"Сколько часов аренды выдавать за покупку 1 шт лота? Введите число 3-720:", kb)


TEXT_LABELS = {
    "text_delivery": "Выдача аккаунта",
    "text_extension": "Продление аренды",
    "text_friend_activated": "Режим друга включён",
    "text_friend_already": "Режим друга уже активен",
    "text_code": "Steam Guard код",
    "text_code_ask_login": "Код: запрос логина",
    "text_code_not_found": "Код: не найдено",
    "text_ask_which_account": "Какой аккаунт продлить",
    "text_extend_applied": "Продление применено",
    "text_extend_no_pending": "Продление: нет ожидающих",
    "text_review_bonus": "Бонус за отзыв",
    "text_partial": "Частичная выдача (друг)",
    "text_qty_choice": "Вопрос при покупке >1 шт",
    "text_qty_timeout_admin": "Таймаут ответа qty (админу)",
    "text_problem": "Сообщение при сбое",
    "text_eta": "ETA: аккаунты скоро появятся",
    "text_delivery_pending": "Выдача: пароль пришлём отдельно (ротация)",
    "text_password_changed": "Пароль обновлён (после смены)",
    "text_rental_ended": "Окончание аренды (отзыв)",
}

PARAM_DESC = {
    "friend_minutes": "Сколько минут действует режим «Для друга» после команды !друг (по истечении — снова продление).",
    "review_bonus_hours": "Сколько часов аренды добавлять покупателю за оставленный отзыв (списывается с баланса KOSell).",
    "balance_threshold": "Минимальный баланс KOSell (в выбранной валюте), ниже которого лоты автоматически скрываются.",
    "poll_sec": "Как часто (в секундах) проверять наличие аккаунтов и баланс KOSell для авто-скрытия лотов.",
    "ac_min_price": "Минимальная цена авто-лота в рублях («пол»). Если себестоимость × наценка выходит дешевле — ставится это значение. Применяется при создании и пересчёте цен. Минимум 1₽.",
    "price_guard_margin_rub": "Минимальный запас к себестоимости KOSell. Если заказ дешевле себестоимости + запас — выдача не запускается.",
    "multi_qty_confirm_ttl_min": "Сколько минут ждать ответ покупателя при покупке нескольких штук одного лота.",
    "steam_showcase_top": "Сколько самых ликвидных товаров KOSell дополнительно создавать в разделе Аккаунты Steam / lots/89.",
    "tz_offset_hours": "Часовой пояс для показа времени покупателям (смещение от UTC). 3 = МСК. Допустимы отрицательные (напр. -3). Влияет только на отображение «Действует до», расчёты идут в UTC.",
    "eta_grace_sec": "Сколько секунд «до появления аккаунта» считать лот доступным (next_available из каталога). Если аккаунты будут позже этого окна — лот скрывается как обычно. 0 = не использовать ETA, всегда скрывать при no_stock.",
    "eta_notify_cooldown_sec": "Как часто (в секундах) уведомлять покупателей об ETA появления аккаунтов (антиспам). 600 = раз в 10 минут.",
}


def tg_params(call):
    s = SETTINGS
    kb = K()
    kb.row(B(f"💱 Валюта оплаты KOSell: {s.get('currency')}", callback_data=f"{P}_p_cur"))
    kb.row(B(f"👥 Режим «друг», мин: {s.get('friend_minutes')}", callback_data=f"{P}_p_set:friend_minutes"))
    kb.row(B(f"🎁 Бонус за отзыв, ч: {s.get('review_bonus_hours')}", callback_data=f"{P}_p_set:review_bonus_hours"))
    kb.row(B(f"⭐ Мин. звёзд для бонуса: {s.get('review_bonus_min_stars')}", callback_data=f"{P}_p_stars"))
    kb.row(B(f"💸 Порог баланса: {_money(s.get('balance_threshold', 0))}", callback_data=f"{P}_p_set:balance_threshold"))
    kb.row(B(f"💰 Мин. цена лота, ₽: {_money(s.get('ac_min_price', 15))}", callback_data=f"{P}_p_set:ac_min_price"))
    kb.row(B(f"🛡 Запас к себестоимости, ₽: {_money(s.get('price_guard_margin_rub', 0))}", callback_data=f"{P}_p_set:price_guard_margin_rub"))
    kb.row(B(f"🔢 Таймаут qty, мин: {s.get('multi_qty_confirm_ttl_min', 30)}", callback_data=f"{P}_p_set:multi_qty_confirm_ttl_min"))
    kb.row(B(f"🟦 Steam-витрина, топ игр: {s.get('steam_showcase_top', 30)}", callback_data=f"{P}_p_set:steam_showcase_top"))
    kb.row(B(f"⏲ Проверка склада/баланса, сек: {s.get('poll_sec')}", callback_data=f"{P}_p_set:poll_sec"))
    kb.row(B(f"🕒 Часовой пояс ({_tz_label(int(s.get('tz_offset_hours', 3) or 0))}): {s.get('tz_offset_hours', 3)}",
             callback_data=f"{P}_p_set:tz_offset_hours"))
    kb.row(B(f"⏳ ETA-окно появления аккаунтов, сек: {s.get('eta_grace_sec', 900)}", callback_data=f"{P}_p_set:eta_grace_sec"))
    kb.row(B(f"🔁 Антиспам ETA, сек: {s.get('eta_notify_cooldown_sec', 600)}", callback_data=f"{P}_p_set:eta_notify_cooldown_sec"))
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, "<b>⚙️ Параметры</b>\n\n"
                "💱 <b>Валюта</b> — в какой валюте списывать с вашего баланса KOSell за аренду.\n"
                "⭐ <b>Мин. звёзд</b> - минимальное количество звезд для получения бонуса за отзыв.\n"
                "Остальное — нажмите для ввода значения.", kb)


def tg_p_cur(call):
    SETTINGS["currency"] = "USD" if SETTINGS.get("currency") == "RUB" else "RUB"
    save_settings()
    tg_params(call)


def tg_p_stars(call):
    cur = int(SETTINGS.get("review_bonus_min_stars", 5) or 5)
    SETTINGS["review_bonus_min_stars"] = (cur % 5) + 1
    save_settings()
    tg_params(call)


def tg_p_set(call):
    key = call.data.split(":")[1]
    state = f"{P}_pset:{key}"
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, state)
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_params"))
    desc = PARAM_DESC.get(key, "")
    cur = SETTINGS.get(key)
    _edit(call, f"<b>✏️ Изменение параметра</b>\n\n{desc}\n\nТекущее значение: <b>{cur}</b>\n\nОтправьте новое число:", kb)


def tg_texts(call):
    page = int(call.data.split(":")[1]) if ":" in call.data else 0
    keys = list(DEFAULT_TEXTS.keys())
    if page * 7 >= len(keys):
        page = max(0, (len(keys) - 1) // 7)
    _save_page("texts", page)
    per = 7
    start = page * per
    chunk = keys[start:start + per]
    kb = K()
    for key in chunk:
        kb.row(B(f"✏️ {TEXT_LABELS.get(key, key)}", callback_data=f"{P}_text:{key}"))
    nav, _ = _nav(f"{P}_texts:", page, len(keys), per)
    if nav:
        kb.row(*nav)
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    _edit(call, "<b>✏️ Тексты для FunPay</b>\n\nВсе сообщения покупателям можно изменить.", kb)


def tg_text_edit(call):
    key = call.data.split(":", 1)[1]
    _set_tg_state(call.message.chat.id, call.message.id, call.from_user.id, f"{P}_settext:{key}")
    cur = get_text(key)
    placeholders = re.findall(r"\{(\w+)\}", DEFAULT_TEXTS.get(key, ""))
    ph = ", ".join(f"<code>{{{p}}}</code>" for p in dict.fromkeys(placeholders)) or "нет"
    kb = K()
    kb.row(B("♻️ Сбросить по умолчанию", callback_data=f"{P}_textreset:{key}"))
    kb.row(B("❌ Отмена", callback_data=f"{P}_texts:{_page_of('texts')}"))
    _edit(call, f"<b>✏️ {TEXT_LABELS.get(key, key)}</b>\n\n"
                f"Текущий текст:\n<code>{_html.escape(cur)}</code>\n\n"
                f"Доступные переменные: {ph}\n\nОтправьте новый текст:", kb)


def tg_text_reset(call):
    key = call.data.split(":", 1)[1]
    SETTINGS["texts"][key] = DEFAULT_TEXTS.get(key, "")
    save_settings()
    bot.answer_callback_query(call.id, "♻️ Сброшено")
    call.data = f"{P}_texts:{_page_of('texts')}"
    tg_texts(call)


def tg_msg_dispatch(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = (message.text or "").strip()
    sd = _get_tg_state(chat_id, user_id)
    if not sd:
        return _continue_tg_handling()
    state = sd.get("state", "") if isinstance(sd, dict) else ""
    if not state.startswith(P):
        return _continue_tg_handling()
    if text.casefold() in {"/start", "start", "/menu", "menu", "меню", "главное меню"}:
        _clear_tg_state(chat_id, user_id, True)
        return _continue_tg_handling()
    mid = getattr(message, "message_id", None)
    dkey = f"{chat_id}:{user_id}:{mid}:{state}"
    if dkey in _tg_processed_msg_ids:
        return
    _tg_processed_msg_ids.add(dkey)
    if len(_tg_processed_msg_ids) > 500:
        _tg_processed_msg_ids.clear()

    try:
        bot.delete_message(chat_id, message.message_id)
    except Exception:
        pass

    def done_kb(cb):
        kb = K()
        kb.row(B("◀️ Назад", callback_data=cb))
        return kb

    if state == f"{P}_set_apikey":
        _clear_tg_state(chat_id, user_id, True)
        key = _sanitize_key(text)
        back_kb = done_kb(f"{CBT.PLUGIN_SETTINGS}:{UUID}")
        if not key:
            try:
                bot.send_message(chat_id, "❌ Пустой ключ. Отправьте API-ключ ещё раз.", reply_markup=back_kb)
            except Exception:
                pass
            return
        SETTINGS["api_key"] = key
        save_settings()
        bal = None
        for _ in range(2):
            try:
                api = _get_api()
                bal = api.balance() if api else None
            except Exception as e:
                logger.warning(f"{LP} apikey balance check error: {e}")
                bal = None
            if bal:
                break
            time.sleep(1.5)
        if bal:
            extra = f"\nБаланс: {bal.get('balance_rub', 0)} RUB / {bal.get('balance_usd', 0)} USD"
        else:
            extra = ("\n⚠️ Ключ сохранён, но баланс получить не удалось. "
                     "Это может быть временный сбой сети/KOSell — проверьте позже. "
                     "Если повторяется — проверьте правильность ключа.")
        try:
            bot.send_message(chat_id, f"✅ API ключ сохранён (длина {len(key)}).{extra}", reply_markup=back_kb)
        except Exception as e:
            logger.error(f"{LP} apikey send_message error: {e}")

    elif state.startswith(f"{P}_set_hours:"):
        idx = int(state.split(":")[1])
        _clear_tg_state(chat_id, user_id, True)
        if text.isdigit() and idx < len(MAPPINGS):
            new_hours = max(KOSELL_MIN_HOURS, min(720, int(text)))
            pid = MAPPINGS[idx].get("product_id")
            new_key = _mapping_key(pid, new_hours)
            duplicate = any(
                j != idx and new_key is not None and _mapping_key(m.get("product_id"), m.get("hours")) == new_key
                for j, m in enumerate(MAPPINGS)
            )
            if duplicate:
                bot.send_message(chat_id, "❌ Такая пара товар KOSell + часы уже есть в привязках.",
                                 reply_markup=done_kb(f"{P}_map:{idx}"))
                return
            MAPPINGS[idx]["hours"] = new_hours
            MAPPINGS[idx]["rent_code"] = _make_rent_code(pid, new_hours)
            save_mappings()
            bot.send_message(chat_id, f"✅ Часы: {MAPPINGS[idx]['hours']}\n"
                                      f"Код привязки: <code>{MAPPINGS[idx]['rent_code']}</code>",
                             reply_markup=done_kb(f"{P}_map:{idx}"), parse_mode="HTML")
        else:
            bot.send_message(chat_id, "❌ Нужно число 3-720.", reply_markup=done_kb(f"{P}_map:{idx}"))

    elif state == f"{P}_manual_lot":
        _clear_tg_state(chat_id, user_id, True)
        m = re.search(r"id=(\d+)", text) or re.search(r"(\d{4,})", text)
        if not m:
            bot.send_message(chat_id, "❌ Не нашёл ID. Отправьте число или ссылку вида .../offer?id=12345.",
                             reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"))
            return
        lot_id = m.group(1)
        codes = _extract_rent_codes(text)
        _addmap_tmp[user_id] = {"lot_id": lot_id, "subcategory_id": None,
                                "title_keyword": "", "rent_code": codes[0] if codes else ""}
        _load_products_cache(user_id)
        text_pl, kb_pl = _product_list_kb(user_id, 0)
        bot.send_message(chat_id, f"✅ Лот {lot_id} принят.\n\n" + text_pl, reply_markup=kb_pl, parse_mode="HTML")

    elif state == f"{P}_prodsearch":
        _clear_tg_state(chat_id, user_id, True)
        info = _addmap_tmp.setdefault(user_id, {})
        q = text.lower()
        info["_filtered"] = [p for p in (info.get("_all") or []) if q in str(p.get("name", "")).lower()]
        text_pl, kb_pl = _product_list_kb(user_id, 0)
        bot.send_message(chat_id, text_pl, reply_markup=kb_pl, parse_mode="HTML")

    elif state == f"{P}_prodmanual":
        _clear_tg_state(chat_id, user_id, True)
        info = _addmap_tmp.setdefault(user_id, {})
        m = re.search(r"\d+", text)
        if not m:
            bot.send_message(chat_id, "❌ Нужен числовой ID товара.", reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"))
            return
        pid = m.group()
        info["product_id"] = int(pid)
        info["product_name"] = _resolve_product_name(user_id, pid)
        msg = bot.send_message(
            chat_id,
            f"Товар: <b>{_html.escape(str(info['product_name']))}</b>\n\n"
            f"Сколько часов аренды выдавать за покупку 1 шт лота? Введите число 3-720:",
            parse_mode="HTML")
        _set_tg_state(chat_id, msg.message_id, user_id, f"{P}_set_newhours")

    elif state == f"{P}_set_newhours":
        _clear_tg_state(chat_id, user_id, True)
        info = _addmap_tmp.get(user_id, {})
        if not text.isdigit() or "product_id" not in info:
            bot.send_message(chat_id, "❌ Нужно число 3-720.", reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"))
            return
        for k in ("_all", "_filtered"):
            info.pop(k, None)
        info["hours"] = max(KOSELL_MIN_HOURS, min(720, int(text)))
        info["currency"] = SETTINGS.get("currency", "RUB")
        lot_id = info.get("lot_id")
        if lot_id and any(str(m.get("lot_id")) == str(lot_id) for m in MAPPINGS if m.get("lot_id")):
            bot.send_message(chat_id, "❌ Этот лот уже привязан. Удалите старую привязку или измените её часы.",
                             reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"))
            return
        if _mapping_exists(info.get("product_id"), info["hours"]):
            bot.send_message(chat_id, "❌ Такая пара товар KOSell + часы уже привязана. "
                                      "Повторную привязку не создаю, чтобы не было дублей.",
                             reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"))
            return
        rent_code = info.get("rent_code") or _make_rent_code(info.get("product_id"), info["hours"])
        if _rent_code_exists(rent_code):
            bot.send_message(chat_id, f"❌ Код <code>{_html.escape(rent_code)}</code> уже используется другой привязкой.",
                             reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"), parse_mode="HTML")
            return
        MAPPINGS.append({
            "lot_id": lot_id,
            "subcategory_id": info.get("subcategory_id"),
            "title_keyword": info.get("title_keyword", ""),
            "product_id": info.get("product_id"),
            "product_name": info.get("product_name", ""),
            "hours": info["hours"],
            "rent_code": rent_code,
            "currency": info["currency"],
        })
        save_mappings()
        _addmap_tmp.pop(user_id, None)
        bot.send_message(chat_id, f"✅ Привязка создана: {MAPPINGS[-1]['product_name']} · "
                                  f"{MAPPINGS[-1]['hours']}ч · лот {MAPPINGS[-1]['lot_id']}\n"
                                  f"Код для описания лота: <code>{MAPPINGS[-1]['rent_code']}</code>",
                         reply_markup=done_kb(f"{P}_maps:{_page_of('maps')}"), parse_mode="HTML")

    elif state.startswith(f"{P}_pset:"):
        key = state.split(":")[1]
        _clear_tg_state(chat_id, user_id, True)
        cur_sym = {"USD": "$", "RUB": "₽"}.get(SETTINGS.get("currency", "RUB"), SETTINGS.get("currency", ""))
        labels = {
            "friend_minutes": "Окно режима «друг» (мин)",
            "review_bonus_hours": "Бонус за отзыв (ч)",
            "balance_threshold": f"Порог баланса ({cur_sym})",
            "poll_sec": "Проверка склада/баланса (сек)",
            "ac_min_price": "Мин. цена лота (₽)",
            "price_guard_margin_rub": "Запас к себестоимости (₽)",
            "multi_qty_confirm_ttl_min": "Таймаут qty (мин)",
            "steam_showcase_top": "Steam-витрина, топ игр",
            "tz_offset_hours": "Часовой пояс (ч)",
            "eta_grace_sec": "ETA-окно появления аккаунтов (сек)",
            "eta_notify_cooldown_sec": "Антиспам ETA (сек)",
        }
        nice = labels.get(key, key)
        float_keys = {"balance_threshold", "ac_min_price", "price_guard_margin_rub"}
        try:
            num = float(text.replace(",", "."))
            if key in float_keys:
                val = round(num, 2)
                if key == "ac_min_price" and val < 1:
                    val = 1.0
                if key == "price_guard_margin_rub" and val < 0:
                    val = 0.0
            else:
                val = int(num)
                if num != val:
                    raise ValueError("only_int")
                if key == "tz_offset_hours":
                    val = max(-12, min(14, val))
                if key == "multi_qty_confirm_ttl_min":
                    val = max(1, min(1440, val))
                if key == "steam_showcase_top":
                    val = max(0, min(200, val))
                if key == "eta_grace_sec":
                    val = max(0, min(86400, val))
                if key == "eta_notify_cooldown_sec":
                    val = max(60, min(86400, val))
            SETTINGS[key] = val
            save_settings()
            if key == "ac_min_price":
                t = _ac_tpl()
                if t:
                    t["min_price"] = val
                    SETTINGS["ac_template"] = t
                    save_settings()
                try:
                    _price_cache.clear()
                except Exception:
                    pass
            bot.send_message(chat_id, f"✅ <b>{nice}</b>: {_money(val)}",
                             reply_markup=done_kb(f"{P}_params"), parse_mode="HTML")
        except ValueError as ve:
            if str(ve) == "only_int":
                bot.send_message(chat_id, "❌ Здесь нужно целое число.", reply_markup=done_kb(f"{P}_params"))
            else:
                bot.send_message(chat_id, "❌ Нужно число.", reply_markup=done_kb(f"{P}_params"))
        except Exception:
            bot.send_message(chat_id, "❌ Нужно число.", reply_markup=done_kb(f"{P}_params"))

    elif state.startswith(f"{P}_settext:"):
        key = state.split(":", 1)[1]
        _clear_tg_state(chat_id, user_id, True)
        SETTINGS["texts"][key] = text
        save_settings()
        bot.send_message(chat_id, f"✅ Текст «{key}» обновлён.", reply_markup=done_kb(f"{P}_texts:{_page_of('texts')}"))

    elif state == f"{P}_ac_w_dcustom":
        _clear_tg_state(chat_id, user_id, True)
        hrs = []
        for tok in re.split(r"[,\s]+", text.strip()):
            if tok.isdigit():
                h = max(KOSELL_MIN_HOURS, min(720, int(tok)))
                if h not in hrs:
                    hrs.append(h)
        if hrs:
            _ac_wizard.setdefault(user_id, {})["durations"] = sorted(hrs)
            _ac_w_goto(chat_id, user_id, f"{P}_ac_w_sum_ru",
                       f"<b>Шаг 2/6 — краткое описание (RU)</b>\n\n{AC_PLACEHOLDER_HINT}\n\n"
                       f"Пример:\n<code>🟩 АВТОВЫДАЧА {PH_GAME} STEAM {PH_TIME}</code>")
        else:
            _wiz_msg(chat_id, user_id, "❌ Пример: 3, 24, 72, 168\nОтправьте часы ещё раз.",
                     done_kb(f"{P}_ac"))
            _set_tg_state(chat_id, _ac_wizard.get(user_id, {}).get("message_id", message.message_id),
                          user_id, f"{P}_ac_w_dcustom")

    elif state == f"{P}_ac_w_sum_ru":
        _clear_tg_state(chat_id, user_id, True)
        _ac_wizard.setdefault(user_id, {})["summary_ru"] = text
        _ac_w_goto(chat_id, user_id, f"{P}_ac_w_sum_en",
                   "<b>Шаг 3/6 — краткое описание (EN)</b>\n\n"
                   f"Тот же смысл на английском. {PH_TIME} / {PH_GAME} работают так же.\n"
                   f"До ~70 символов.")

    elif state == f"{P}_ac_w_sum_en":
        _clear_tg_state(chat_id, user_id, True)
        _ac_wizard.setdefault(user_id, {})["summary_en"] = text
        _ac_w_goto(chat_id, user_id, f"{P}_ac_w_desc_ru",
                   f"<b>Шаг 4/6 — подробное описание (RU)</b>\n\n{AC_PLACEHOLDER_HINT}")

    elif state == f"{P}_ac_w_desc_ru":
        _clear_tg_state(chat_id, user_id, True)
        _ac_wizard.setdefault(user_id, {})["desc_ru"] = text
        _ac_w_goto(chat_id, user_id, f"{P}_ac_w_desc_en",
                   "<b>Шаг 5/6 — подробное описание (EN)</b>\n\nАнглийская версия описания.")

    elif state == f"{P}_ac_w_desc_en":
        _clear_tg_state(chat_id, user_id, True)
        _ac_wizard.setdefault(user_id, {})["desc_en"] = text
        kb = K()
        kb.row(B("📈 Наценка % от себестоимости KOSell", callback_data=f"{P}_ac_w_pmode:markup"))
        kb.row(B("💰 Своя цена ₽ на каждое время", callback_data=f"{P}_ac_w_pmode:fixed"))
        kb.row(B("❌ Отмена", callback_data=f"{P}_ac"))
        _wiz_msg(chat_id, user_id,
                 "<b>Шаг 6/6 — цены</b>\n\n"
                 "• <b>Наценка</b> — берём себестоимость KOSell по каждому времени и добавляем ваш %.\n"
                 "• <b>Своя цена</b> — вы вручную вписываете цену в ₽ для покупателя отдельно "
                 "на каждое время (3 часа, 1 день, …).\n\n"
                 "В обоих случаях комиссия FunPay учитывается автоматически — "
                 "покупатель видит ровно вашу цену.", kb)

    elif state == f"{P}_ac_w_markup":
        _clear_tg_state(chat_id, user_id, True)
        try:
            _ac_wizard.setdefault(user_id, {})["markup_percent"] = round(float(text.replace(",", ".")), 2)
        except Exception:
            _wiz_msg(chat_id, user_id, "❌ Нужно число (процент). Отправьте ещё раз, напр. <code>50</code>.",
                     done_kb(f"{P}_ac"))
            _set_tg_state(chat_id, _ac_wizard.get(user_id, {}).get("message_id", message.message_id),
                          user_id, f"{P}_ac_w_markup")
            return
        _ac_finish(chat_id, user_id)

    elif state == f"{P}_ac_game_miss_input":
        _clear_tg_state(chat_id, user_id, True)
        parts = text.strip().split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            _wiz_msg(chat_id, user_id, "❌ Отправьте числовой ID товара KOSell, например <code>343</code>.",
                     done_kb(f"{P}_ac"))
            return
        pid = int(parts[0])
        durations = None
        if len(parts) > 1:
            try:
                durations = sorted({int(x) for x in re.split(r"[,;\s]+", parts[1]) if x}, reverse=True)
            except ValueError:
                durations = None
            if not durations or any(h < 1 or h > 720 for h in durations):
                _wiz_msg(chat_id, user_id, "❌ Часы укажите числами от 1 до 720: <code>1,3,24,72</code>.", done_kb(f"{P}_ac"))
                return
        if not _build_plan(user_id, missing_only=True, product_id=pid, durations=durations):
            _wiz_msg(chat_id, user_id, f"✅ Для товара KOSell <code>{pid}</code> недостающих лотов нет.",
                     done_kb(f"{P}_ac"))
            return
        kb = K()
        kb.row(B("👁 Предпросмотр лотов", callback_data=f"{P}_ac_prev:0"))
        kb.row(_back_btn(f"{P}_ac"))
        _wiz_msg(chat_id, user_id, f"✅ План для товара KOSell <code>{pid}</code> готов.", kb)

    elif state == f"{P}_ac_game_markup_input":
        _clear_tg_state(chat_id, user_id, True)
        parts = text.split()
        if len(parts) != 2 or not parts[0].isdigit():
            _wiz_msg(chat_id, user_id, "❌ Формат: <code>ID процент</code>, например <code>343 30</code>.",
                     done_kb(f"{P}_ac"))
            return
        pid = str(int(parts[0]))
        overrides = SETTINGS.setdefault("product_markup_percent", {})
        if parts[1].lower() == "default":
            overrides.pop(pid, None)
            result = f"Наценка для товара {pid} сброшена на общую из шаблона."
        else:
            try:
                pct = round(float(parts[1].replace(",", ".")), 2)
            except Exception:
                _wiz_msg(chat_id, user_id, "❌ Процент должен быть числом, например <code>30</code>.",
                         done_kb(f"{P}_ac"))
                return
            if not 0 <= pct <= 1000:
                _wiz_msg(chat_id, user_id, "❌ Укажите процент от 0 до 1000.", done_kb(f"{P}_ac"))
                return
            overrides[pid] = pct
            result = f"Наценка для товара {pid}: {pct:g}%."
        save_settings()
        _price_cache.clear()
        targets = [m for m in MAPPINGS
                   if m.get("auto_created") and str(m.get("product_id")) == pid
                   and m.get("lot_id") and m.get("hours") is not None]
        if not _ac_begin("Пересчёт цен игры"):
            _wiz_msg(chat_id, user_id, f"✅ {result}\n\nДругой пересчёт уже выполняется.", done_kb(f"{P}_ac"))
            return
        mid = _wiz_msg(chat_id, user_id,
                       f"✅ {result}\n\nЗапускаю пересчёт только этой игры: {len(targets)} лотов...\n"
                       f"<code>{_bar(0, len(targets))}</code>", None)
        th = threading.Thread(target=_reprice_thread_entry,
                               args=(user_id, chat_id, mid, int(pid)), daemon=True)
        th.start()

    elif state == f"{P}_ac_reprice_pct":
        _clear_tg_state(chat_id, user_id, True)
        try:
            pct = round(float(text.replace(",", ".")), 2)
        except Exception:
            _wiz_msg(chat_id, user_id, "❌ Нужно число (процент). Напр. <code>8</code>.", done_kb(f"{P}_ac"))
            _set_tg_state(chat_id, _ac_wizard.get(user_id, {}).get("message_id", message.message_id),
                          user_id, f"{P}_ac_reprice_pct")
            return
        t = _ac_tpl()
        t["price_mode"] = "markup"
        t["markup_percent"] = pct
        SETTINGS["ac_template"] = t
        save_settings()
        _price_cache.clear()
        mid = _ac_wizard.get(user_id, {}).get("message_id", message.message_id)
        reprice_total = len([m for m in MAPPINGS
                             if m.get("auto_created") and m.get("lot_id")
                             and m.get("product_id") is not None and m.get("hours") is not None])
        if not _ac_begin("Пересчёт цен…"):
            _wiz_msg(chat_id, user_id, "Уже идёт другой процесс — дождитесь завершения.", done_kb(f"{P}_ac"))
            return
        mid = _wiz_msg(chat_id, user_id,
                       f"♻️ Запускаю пересчёт по наценке <b>{_pct(pct)}%</b>…\n\n<code>{_bar(0, reprice_total)}</code>", None)
        logger.info(f"{LP} [AC] reprice thread starting: user_id={user_id}, chat_id={chat_id}, mid={mid}, total={reprice_total}")
        th = threading.Thread(target=_reprice_thread_entry, args=(user_id, chat_id, mid), daemon=True)
        th.start()
        logger.info(f"{LP} [AC] reprice thread started: alive={th.is_alive()}")

    elif state == f"{P}_ac_e_markup":
        _clear_tg_state(chat_id, user_id, True)
        try:
            pct = round(float(text.replace(",", ".")), 2)
        except Exception:
            _wiz_msg(chat_id, user_id, "❌ Нужно число (процент). Напр. <code>5</code>.", done_kb(f"{P}_ac_edit"))
            _set_tg_state(chat_id, _ac_wizard.get(user_id, {}).get("message_id", message.message_id),
                          user_id, f"{P}_ac_e_markup")
            return
        t = _ac_tpl()
        t["price_mode"] = "markup"
        t["markup_percent"] = pct
        SETTINGS["ac_template"] = t
        save_settings()
        _price_cache.clear()
        _wiz_msg(chat_id, user_id,
                 f"✅ Наценка обновлена: <b>{_pct(pct)}%</b>.\n\nДля уже созданных лотов нажмите "
                 f"«♻️ Пересчитать цены».", done_kb(f"{P}_ac_edit"))

    elif state == f"{P}_ac_e_title":
        _clear_tg_state(chat_id, user_id, True)
        ru, en = (text.split("|||", 1) + [""])[:2]
        ru = ru.strip()
        en = en.strip() or _ascii(ru, keep_formatting=True)
        t = _ac_tpl()
        t["summary_ru"] = ru
        t["summary_en"] = en
        SETTINGS["ac_template"] = t
        save_settings()
        _wiz_msg(chat_id, user_id,
                 f"✅ Название обновлено.\n\nRU: <code>{_html.escape(ru)}</code>\n"
                 f"EN: <code>{_html.escape(en)}</code>", done_kb(f"{P}_ac_edit"))

    elif state == f"{P}_ac_e_desc":
        _clear_tg_state(chat_id, user_id, True)
        ru, en = (text.split("|||", 1) + [""])[:2]
        ru = ru.strip()
        en = en.strip() or _ascii(ru)
        t = _ac_tpl()
        t["desc_ru"] = ru
        t["desc_en"] = en
        SETTINGS["ac_template"] = t
        save_settings()
        _wiz_msg(chat_id, user_id, "✅ Описание обновлено.", done_kb(f"{P}_ac_edit"))

    elif state == f"{P}_ac_w_fixprice":
        _clear_tg_state(chat_id, user_id, True)
        w = _ac_wizard.setdefault(user_id, {})
        durs = w.get("durations") or [3, 24, 72, 168]
        idx = int(w.get("fix_idx", 0))
        try:
            price = float(text.replace(",", "."))
        except Exception:
            _wiz_msg(chat_id, user_id,
                     f"❌ Нужно число. Цена для <b>{_format_kostime(durs[idx])}</b> (₽):",
                     done_kb(f"{P}_ac"))
            _set_tg_state(chat_id, w.get("message_id", message.message_id), user_id, f"{P}_ac_w_fixprice")
            return
        w.setdefault("fixed_prices", {})[str(durs[idx])] = price
        w["fix_idx"] = idx + 1
        _ac_ask_fixprice(chat_id, user_id)

    elif state == f"{P}_ac_w_usd":
        _clear_tg_state(chat_id, user_id, True)
        try:
            rate = float(text.replace(",", "."))
        except Exception:
            _wiz_msg(chat_id, user_id, "❌ Нужно число (курс). Напр. <code>95</code>.", done_kb(f"{P}_ac"))
            _set_tg_state(chat_id, _ac_wizard.get(user_id, {}).get("message_id", message.message_id),
                          user_id, f"{P}_ac_w_usd")
            return
        _ac_wizard.setdefault(user_id, {})["usd_rub"] = rate
        SETTINGS["ac_usd_rub"] = rate
        save_settings()
        _ac_finish(chat_id, user_id)


# ============================================================================
#  АВТОСОЗДАНИЕ ЛОТОВ
# ============================================================================

AC_PLACEHOLDER_HINT = (
    f"<b>Плейсхолдеры</b> (подставляются в каждый лот автоматически):\n"
    f"• <code>{PH_TIME}</code> — время аренды со склонением (3 часа, 1 день, 2 дня…)\n"
    f"• <code>{PH_GAME}</code> — название игры\n\n"
    f"В названии лота длинная игра/DLC будет сокращена так, чтобы <code>{PH_TIME}</code> не пропал. "
    f"Полное название автоматически добавляется первой строкой в подробное описание.\n"
    f"Не указывайте игру и время вручную — только плейсхолдеры.\n\n"
    f"<b>Команды покупателя</b> (добавьте их в подробное описание):\n"
    f"Команда должна быть точным первым словом: <code>!продл</code> не считается командой.\n"
    f"• <code>!кодстим логин</code> — прислать актуальный код Steam Guard для выданного аккаунта.\n"
    f"• <code>!продлить логин</code> — продлить аренду этого же аккаунта (после повторной оплаты лота).\n"
    f"• <code>!друг</code> — режим «для друга»: см. ниже. <code>!friend</code> тоже работает как алиас.\n\n"
    f"<b>Как работает <code>!друг</code></b> (объясните это покупателю):\n"
    f"По умолчанию, если покупатель оплачивает лот повторно, бот <b>продлевает</b> его текущий "
    f"аккаунт (то же время на том же аккаунте). Команда <code>!друг</code> включает на несколько "
    f"минут режим, при котором следующая оплата выдаёт <b>новый отдельный аккаунт</b>, а не продлевает "
    f"старый. Если покупатель берёт несколько штук, бот спросит: выдать несколько отдельных аккаунтов "
    f"или один аккаунт на суммарное время.\n"
    f"Порядок: написать <code>!друг</code> → оплатить лот в течение окна (по умолчанию ~10 мин). "
    f"После этой оплаты режим выключается автоматически.\n\n"
    f"<b>Код привязки</b>\n"
    f"Плагин автоматически добавляет в подробное описание автолота код вида <code>rent:ID-HOURS</code>. "
    f"В название код не добавляется. Не удаляйте его из подробного описания: по нему бот точно определяет товар, если FunPay не передал ID лота.\n\n"
    f"<i>{FP_LIMITS_HINT}</i>"
)


def _ac_durations_label(tpl: "dict | None" = None) -> str:
    t = tpl or _ac_tpl()
    ds = t.get("durations") or []
    return ", ".join(f"{h}ч" if h < 48 else f"{h//24}д" for h in ds) or "—"


def _ac_price_label(tpl: "dict | None" = None) -> str:
    t = tpl or _ac_tpl()
    if t.get("price_mode") == "fixed":
        fp = t.get("fixed_prices") or {}
        if fp:
            parts = []
            for h in (t.get("durations") or []):
                v = fp.get(str(h), fp.get(h))
                if v is not None:
                    lbl = f"{h}ч" if h < 48 else f"{h//24}д"
                    parts.append(f"{lbl}={int(v)}₽")
            return "фикс. " + ", ".join(parts) if parts else "фикс."
        return f"фикс. {int(t.get('fixed_public_rub') or 0)}₽"
    return f"наценка {_pct(t.get('markup_percent', 0))}%"


def _ac_count_missing() -> int:
    return len(_build_plan_items(missing_only=True, bind_existing=False))


def _diag_example(d: dict) -> str:
    name = d.get("name") or d.get("product_name") or d.get("map_name") or d.get("product_name") or "—"
    bits = []
    for key, label in (("product_id", "pid"), ("map_product_id", "map_pid"),
                       ("subcategory_id", "sub"), ("hours", "ч")):
        val = d.get(key)
        if val not in (None, ""):
            bits.append(f"{label}={val}")
    if d.get("placement"):
        bits.append(f"place={d.get('placement')}")
    if d.get("min_hours") is not None or d.get("max_hours") is not None:
        bits.append(f"лимит={d.get('min_hours', '?')}-{d.get('max_hours', '?')}ч")
    if d.get("count") is not None and d.get("cap") is not None:
        bits.append(f"{d.get('count')}/{d.get('cap')} лотов")
    return f"{name}" + (f" ({', '.join(bits)})" if bits else "")


def _ac_diagnostics_text() -> str:
    api = _get_api()
    if not api:
        return "⛔️ <b>Диагностика карты</b>\n\nAPI ключ KOSell не задан."
    if _products(api) is None:
        return "⛔️ <b>Диагностика карты</b>\n\nKOSell API не отвечает или ключ неверный."

    diagnostics = []
    plan = _build_plan_items(missing_only=True, bind_existing=False, diagnostics=diagnostics)

    try:
        cap = None
    except Exception:
        cap = None
    own_lots_cache = {}
    sub_counts = {}
    for it in plan:
        try:
            sub_id = int(it["subcategory_id"])
        except Exception:
            continue
        if sub_id not in sub_counts:
            sub_counts[sub_id] = len(_my_subcat_lots(sub_id, own_lots_cache))
        cap = _subcategory_cap(sub_id, it.get("category_id"))
        if sub_counts[sub_id] >= cap:
            _diag_add(diagnostics, "лимит подкатегории",
                      product_id=it.get("product_id"), name=it.get("game_name"),
                      subcategory_id=sub_id, hours=it.get("hours"),
                      count=sub_counts[sub_id], cap=cap)

    counts = Counter(d.get("reason", "другое") for d in diagnostics)
    lines = [
        "<b>🧭 Диагностика карты автолотов</b>",
        "",
        f"К созданию по текущему шаблону: <b>{len(plan)}</b>",
        f"Диагностических записей: <b>{len(diagnostics)}</b>",
    ]
    if counts:
        lines.append("")
        lines.append("<b>Причины:</b>")
        for reason, cnt in counts.most_common(8):
            lines.append(f"• <b>{cnt}×</b> {_html.escape(reason)}")
    else:
        lines.append("")
        lines.append("✅ Явных проблем не найдено.")

    examples = [d for d in diagnostics if d.get("reason") != "уже есть привязка"]
    if not examples:
        examples = diagnostics
    if examples:
        lines.append("")
        lines.append("<b>Примеры:</b>")
        for d in examples[:25]:
            lines.append(f"• {_html.escape(d.get('reason', 'другое'))}: "
                         f"<code>{_html.escape(_diag_example(d)[:160])}</code>")
        if len(examples) > 25:
            lines.append(f"…и ещё {len(examples) - 25}.")

    return "\n".join(lines)[:3900]


def tg_ac_menu(call):
    try:
        bot.answer_callback_query(call.id, "⏳ Загружаю каталог…")
    except Exception:
        pass
    api = _get_api()
    mp = _fetch_map()
    api_err = ""
    sellable = 0
    if not api:
        api_err = "🔑 <b>API-ключ KOSell не задан.</b> Откройте «🔑 API ключ» и вставьте ключ."
    else:
        prods = _products(api)
        if prods is None:
            api_err = ("⛔️ <b>KOSell API не отвечает или ключ неверный</b> (401 invalid_api_key).\n"
                       "Проверьте ключ в «🔑 API ключ» — без него список игр пуст.")
        else:
            sellable = len(_all_sellable_games(api))
    missing = _ac_count_missing() if (_ac_has_template() and not api_err) else 0
    run_banner = ""
    if _ac_running.get("on"):
        rt = _ac_running.get("title", "Процесс")
        rd, rtot = _ac_running.get("done", 0), _ac_running.get("total", 0)
        rv, rc = _ac_running.get("verb", "Готово"), _ac_running.get("created", 0)
        eta = ""
        st = _ac_running.get("started")
        if st and rd and rd < rtot:
            rem = max(0.0, (time.time() - st) / rd * (rtot - rd))
            eta = f" · осталось {_fmt_eta(rem)}"
        run_banner = (f"⏳ <b>Идёт: {rt}</b>\n<code>{_bar(rd, rtot)}</code>\n"
                      f"{rv}: {rc} из {rtot}{eta}\n\n")
    kb = K()
    if _ac_running.get("on"):
        kb.row(B("🔄 Обновить статус", callback_data=f"{P}_ac"))
    if _ac_has_template():
        tpl = _ac_tpl()
        kb.row(B(f"➕ Создать недостающие ({missing})", callback_data=f"{P}_ac_miss"))
        kb.row(B("🎮 Создать для одной игры", callback_data=f"{P}_ac_game_miss"))
        kb.row(B("🎯 Наценка для игры", callback_data=f"{P}_ac_game_markup"))
        kb.row(B("✏️ Изменить шаблон", callback_data=f"{P}_ac_edit"),
               B("⚙️ Заново", callback_data=f"{P}_ac_w_start"))
        kb.row(B("👁 Предпросмотр цен", callback_data=f"{P}_ac_prev:0"),
               B("♻️ Пересчитать цены", callback_data=f"{P}_ac_reprice"))
        kb.row(B("🧭 Диагностика карты", callback_data=f"{P}_ac_diag"))
        text = (
            f"<b>📦 Создание лотов KOSell</b>\n\n"
            + (api_err + "\n\n" if api_err else "")
            + f"Шаблон сохранён. Длительности: <b>{_ac_durations_label(tpl)}</b>\n"
            f"Цены: <b>{_ac_price_label(tpl)}</b>\n\n"
            f"Игр на KOSell (карта + «Прочие»): <b>{sellable}</b>\n"
            f"Steam-витрина lots/89: <b>{'вкл, топ ' + str(_steam_showcase_top_n()) if SETTINGS.get('steam_showcase_enabled', True) else 'выкл'}</b>\n"
            f"Недостающих лотов: <b>{missing}</b>\n\n"
            f"Повторный запуск создаёт только то, чего ещё нет в привязках."
        )
    else:
        kb.row(B("▶️ Начать мастер настройки", callback_data=f"{P}_ac_w_start"))
        text = (
            f"<b>📦 Создание лотов KOSell</b>\n\n"
            + (api_err + "\n\n" if api_err else "")
            + f"Разовый мастер: задаёте шаблон текстов ({PH_TIME}, {PH_GAME}), "
            f"длительности и цены — затем бот создаёт лоты на FunPay.\n\n"
            f"Игр доступно: <b>{sellable}</b> (своя категория + «Прочие игры»)\n"
            f"Карта игр: <b>{len((mp or {}).get('games', []))}</b> записей"
        )
    dbg = "🟢" if SETTINGS.get("ac_debug") else "⚪️"
    kb.row(B("🔄 Обновить карту", callback_data=f"{P}_ac_refresh"),
           B(f"{dbg} Debug", callback_data=f"{P}_ac_dbg"))
    kb.row(B("🗑 Удалить ВСЕ лоты", callback_data=f"{P}_ac_delall"))
    kb.row(_back_btn(f"{CBT.PLUGIN_SETTINGS}:{UUID}"))
    text = run_banner + text
    text += "\n\n<i>Данные кэшируются на 15 мин — после «Обновить карту» первый расчёт дольше.</i>"
    _edit(call, text, kb)


def tg_ac_dbg(call):
    SETTINGS["ac_debug"] = not SETTINGS.get("ac_debug")
    save_settings()
    state = "включён" if SETTINGS["ac_debug"] else "выключен"
    bot.answer_callback_query(call.id, f"Debug-режим {state}")
    tg_ac_menu(call)


def tg_ac_w_start(call):
    uid = call.from_user.id
    _ac_wizard[uid] = {"message_id": call.message.message_id}
    tg_ac_w_dur(call)


def tg_ac_w_dur(call):
    kb = K()
    for name, ds in DURATION_PRESETS.items():
        lbl = ", ".join(f"{h}ч" if h < 48 else f"{h//24}д" for h in ds)
        kb.row(B(f"{name}: {lbl}", callback_data=f"{P}_ac_w_dpre:{name}"))
    kb.row(B("✍️ Свой список", callback_data=f"{P}_ac_w_dcustom"))
    kb.row(_back_btn(f"{P}_ac"))
    _edit(call, "<b>Шаг 1/6 — длительности</b>\n\nСколько лотов на одну игру (по одному на каждое время):\n"
                "выберите пресет или введите часы через запятую.", kb)


def tg_ac_w_dpre(call):
    uid = call.from_user.id
    name = call.data.split(":", 1)[1]
    if name in DURATION_PRESETS:
        _ac_wizard.setdefault(uid, {})["durations"] = list(DURATION_PRESETS[name])
    _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_w_sum_ru")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
    _edit(call, f"<b>Шаг 2/6 — краткое описание (RU)</b>\n\n{AC_PLACEHOLDER_HINT}\n\n"
                f"Пример:\n<code>🟩 АВТОВЫДАЧА 🌿 АРЕНДА {PH_GAME} STEAM 🌿 {PH_TIME}</code>", kb)


def tg_ac_w_dcustom(call):
    _set_tg_state(call.message.chat.id, call.message.message_id, call.from_user.id, f"{P}_ac_w_dcustom")
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
    _edit(call, "<b>✍️ Свой список длительностей</b>\n\nЧасы через запятую: <code>3, 24, 72, 168</code>", kb)


def _ac_w_goto(chat_id, uid, state, text):
    kb = K()
    kb.row(B("❌ Отмена", callback_data=f"{P}_ac"))
    mid = _wiz_msg(chat_id, uid, text, kb)
    _set_tg_state(chat_id, mid, uid, state)


def _ac_ask_fixprice(chat_id, uid):
    w = _ac_wizard.setdefault(uid, {})
    durs = w.get("durations") or [3, 24, 72, 168]
    idx = int(w.get("fix_idx", 0))
    if idx >= len(durs):
        _ac_finish(chat_id, uid)
        return
    fp = w.get("fixed_prices") or {}
    done = "\n".join(
        f"  • {_format_kostime(d)} → {int(fp[str(d)])}₽"
        for d in durs[:idx] if str(d) in fp
    )
    h = durs[idx]
    txt = (f"<b>💰 Своя цена — {idx + 1}/{len(durs)}</b>\n\n"
           + (f"Уже задано:\n{done}\n\n" if done else "")
           + f"Цена <b>для покупателя</b> за <b>{_format_kostime(h)}</b> в рублях "
             f"(напр. <code>99</code>).\nКомиссия FunPay учтётся сама.")
    _ac_w_goto(chat_id, uid, f"{P}_ac_w_fixprice", txt)


def _ac_finish(chat_id, uid):
    mid = _ac_wizard.get(uid, {}).get("message_id")
    _ac_save_template_from_wizard(uid)
    n = len(_build_plan(uid, missing_only=True))
    kb = K()
    kb.row(B("👁 Предпросмотр цен", callback_data=f"{P}_ac_prev:0"))
    kb.row(B(f"➕ Создать недостающие ({n})", callback_data=f"{P}_ac_miss"))
    kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
    txt = (f"✅ <b>Шаблон сохранён.</b>\n\n"
           f"Доступно к созданию: <b>{n}</b> недостающих лотов.\n"
           f"Откройте предпросмотр цен или сразу «Создать недостающие».\n\n"
           f"<i>Если игр 0 — проверьте API-ключ KOSell в настройках плагина.</i>")
    if mid:
        try:
            bot.edit_message_text(txt, chat_id, mid, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    bot.send_message(chat_id, txt, reply_markup=kb, parse_mode="HTML")


def tg_ac_w_price_mode(call):
    mode = call.data.split(":")[-1]
    uid = call.from_user.id
    w = _ac_wizard.setdefault(uid, {})
    w["price_mode"] = mode
    w["message_id"] = call.message.message_id
    if mode == "markup":
        _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_w_markup")
        kb = K()
        kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
        _edit(call, "<b>Наценка</b>\n\nБерём себестоимость KOSell по каждому времени и добавляем ваш %.\n"
                    "Отправьте процент, напр. <code>50</code> (= +50% к себестоимости):", kb)
    else:
        w["fix_idx"] = 0
        w["fixed_prices"] = {}
        bot.answer_callback_query(call.id)
        _ac_ask_fixprice(call.message.chat.id, uid)


def _ac_save_template_from_wizard(uid: int):
    w = _ac_wizard.get(uid, {})
    tpl = {
        "durations": w.get("durations") or [3, 24, 72, 168],
        "summary_ru": w.get("summary_ru", ""),
        "summary_en": w.get("summary_en", ""),
        "desc_ru": w.get("desc_ru", ""),
        "desc_en": w.get("desc_en", ""),
        "price_mode": w.get("price_mode", "markup"),
        "markup_percent": w.get("markup_percent", 50),
        "fixed_public_rub": w.get("fixed_public_rub"),
        "fixed_prices": w.get("fixed_prices") or {},
        "usd_rub": w.get("usd_rub", SETTINGS.get("ac_usd_rub", 95)),
        "min_price": SETTINGS.get("ac_min_price", 15),
    }
    SETTINGS["ac_template"] = _sanitize_ac_template_public(tpl)[0]
    save_settings()
    _ac_wizard.pop(uid, None)


def tg_ac_refresh(call):
    try:
        bot.answer_callback_query(call.id, "⏳ Обновляю карту и каталог…")
    except Exception:
        pass
    _fetch_map(force=True)
    _misc_cache.clear()
    _products_cache.update({"data": None, "ts": 0, "err": False})
    _pub_lots_cache.clear()
    _commission_cache.clear()
    _node_form_cache.clear()
    _price_cache.clear()
    _map_cache["warned"] = False
    _products(_get_api(), force=True)
    tg_ac_menu(call)


def tg_ac_diag(call):
    try:
        bot.answer_callback_query(call.id, "⏳ Собираю диагностику…")
    except Exception:
        pass
    kb = K()
    kb.row(B("🔄 Обновить", callback_data=f"{P}_ac_diag"))
    kb.row(_back_btn(f"{P}_ac"))
    _edit(call, _ac_diagnostics_text(), kb)


def tg_ac_preview(call, missing_only: bool = True):
    parts = call.data.split(":")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if not _ac_has_template():
        bot.answer_callback_query(call.id, "Сначала пройдите мастер", show_alert=True)
        return
    try:
        bot.answer_callback_query(call.id, "⏳ Считаю цены, подождите…")
    except Exception:
        pass
    api = _get_api()
    if not api or _products(api) is None:
        kb = K()
        kb.row(B("🔑 API ключ", callback_data=f"{P}_apikey"))
        kb.row(_back_btn(f"{P}_ac"))
        _edit(call, "⛔️ <b>Нет связи с KOSell.</b>\n\n"
                    "Список игр пуст — API-ключ не задан или неверный (401). "
                    "Проверьте ключ, затем повторите предпросмотр.", kb)
        return
    saved_plan = _ac_plan.get(call.from_user.id) or {}
    plan = saved_plan.get("items")
    if "items" not in saved_plan:
        plan = _build_plan(call.from_user.id, missing_only=missing_only)
    tpl = _ac_tpl()
    if not plan:
        kb = K()
        kb.row(_back_btn(f"{P}_ac"))
        _edit(call, "✅ <b>Всё уже создано.</b>\n\n"
                    "Недостающих лотов нет — все пары «игра + время» из шаблона уже есть в привязках.\n"
                    "Появятся новые игры на KOSell или обновите карту — тогда здесь снова будут лоты.", kb)
        return
    games = {}
    for it in plan:
        games.setdefault((it["product_id"], it["game_name"], it["subcategory_id"]), []).append(it)
    keys = list(games.keys())
    per = 4
    start = page * per
    chunk = keys[start:start + per]
    mode_s = _ac_price_label(tpl)
    lines = [
        f"<b>👁 Предпросмотр</b> · лотов: <b>{len(plan)}</b> · стр. {page + 1}/{max(1, (len(keys)+per-1)//per)} · {mode_s}\n",
        f"<i>По каждому времени: себестоимость KOSell → ваша цена покупателю → "
        f"конкуренты на FunPay (5 низших).</i>\n",
    ]
    prods_by_id = {int(p["id"]): p for p in (_products(api) or [])}
    for k in chunk:
        pid, gname, sub_id = k
        prod = prods_by_id.get(pid, {"id": pid})
        its = sorted(games[k], key=lambda x: x["hours"], reverse=True)
        src = its[0].get("source", "")
        src_label = " · Steam-витрина" if src == "steam_showcase" else (" · прочие" if src == "misc" else "")
        lines.append(f"\n🎮 <b>{_html.escape(gname[:42])}</b>{src_label}")
        for it in its:
            ht = _format_kostime(it["hours"])
            cost = _kosell_cost_rub(api, prod, it["hours"])
            pub = _public_price_rub(api, prod, it["hours"], tpl)
            low = _fp_lowest_prices_for(sub_id, it["hours"],
                                        gname if src == "misc" else None, 5)
            low_s = (" / ".join(_money(p) for p in low) + "₽") if low else "нет такого времени"
            lines.append(
                f"  <b>{ht}</b>\n"
                f"    KOSell: <b>{_money(cost)}₽</b>  →  "
                f"ваша: <b>{_money(pub)}₽</b>  ·  FP: {low_s}"
            )
    kb = K()
    nav, _ = _nav(f"{P}_ac_prev:", page, len(keys), per)
    if nav:
        kb.row(*nav)
    if plan:
        kb.row(B(f"✅ Создать {len(plan)} лотов", callback_data=f"{P}_ac_confirm"))
    kb.row(_back_btn(f"{P}_ac"))
    _edit(call, "\n".join(lines), kb)


def tg_ac_game_miss(call):
    uid = call.from_user.id
    _ac_wizard[uid] = {"message_id": call.message.message_id}
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
    _wiz_msg(call.message.chat.id, uid,
             "<b>🎮 Создание лотов для одной игры</b>\n\n"
             "Отправьте ID товара KOSell и, при необходимости, свои часы.\n"
             "Примеры: <code>343</code> или <code>343 1,3,24,72,168</code>\n\n"
             "Свои часы применятся только к этому запуску и не изменят глобальный мастер.", kb)
    _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_game_miss_input")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def tg_ac_miss(call):
    if not _ac_has_template():
        bot.answer_callback_query(call.id, "Нет шаблона — пройдите мастер", show_alert=True)
        return
    try:
        bot.answer_callback_query(call.id, "⏳ Считаю недостающие лоты…")
    except Exception:
        pass
    if not _build_plan(call.from_user.id, missing_only=True):
        kb = K()
        kb.row(_back_btn(f"{P}_ac"))
        _edit(call, "✅ <b>Всё уже создано.</b>\n\nНедостающих лотов нет.", kb)
        return
    tg_ac_preview(call)


def tg_ac_confirm(call):
    uid = call.from_user.id
    saved_plan = _ac_plan.get(uid) or {}
    if "items" not in saved_plan:
        _build_plan(uid, missing_only=True)
    plan = (_ac_plan.get(uid) or {}).get("items") or []
    if not plan:
        bot.answer_callback_query(call.id, "Нечего создавать", show_alert=True)
        return
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    kb = K()
    kb.row(B("🚀 Да, создать", callback_data=f"{P}_ac_go"))
    kb.row(_back_btn(f"{P}_ac_prev:0"))
    _edit(call, f"<b>Подтверждение</b>\n\nСоздать <b>{len(plan)}</b> лотов?\n"
                "Лимит: 20 в общем разделе, 15 в игровых категориях.", kb)


def tg_ac_go(call):
    uid = call.from_user.id
    plan = (_ac_plan.get(uid) or {}).get("items") or []
    if not plan:
        bot.answer_callback_query(call.id, "Нет плана", show_alert=True)
        return
    if not _ac_begin("Создание лотов…"):
        bot.answer_callback_query(call.id, "Уже идёт другой процесс", show_alert=True)
        return
    eta = max(1, len(plan) * 3 // 60)
    _edit(call, f"🚀 <b>Запускаю создание {len(plan)} лотов</b>\n\n"
                f"<code>{_bar(0, len(plan))}</code>\n\n"
                f"Примерно ~{eta} мин. Прогресс обновляется здесь.", None)
    chat_id = call.message.chat.id
    mid = call.message.id
    threading.Thread(target=_autocreate_run, args=(uid, chat_id, mid), daemon=True).start()


def tg_ac_cancel(call):
    """Отмена текущего фонового процесса (создание/пересчёт/удаление лотов)."""
    if not _ac_running.get("on"):
        bot.answer_callback_query(call.id, "Сейчас нет активного процесса", show_alert=True)
        return
    if _ac_running.get("cancel"):
        bot.answer_callback_query(call.id, "Отмена уже запрошена…", show_alert=True)
        return
    _ac_running["cancel"] = True
    try:
        bot.answer_callback_query(call.id, "⏳ Останавливаю после текущего лота…")
    except Exception:
        pass
    txt = (f"🛑 <b>{_ac_running.get('title', 'Процесс')} — остановка…</b>\n\n"
           f"Завершу текущий лот и остановлюсь. Уже созданное останется, "
           f"недостающее можно до-создать позже кнопкой «Создать недостающие».")
    kb = K()
    # Показываем прогресс без кнопки отмены (она уже нажата)
    done = _ac_running.get("done", 0)
    total = _ac_running.get("total", 0) or 1
    kb.row(B(f"⏳ {done}/{total} — ждём остановки…", callback_data=f"{P}_noop"))
    try:
        bot.edit_message_text(txt, call.message.chat.id, call.message.id,
                              parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


def tg_ac_delall(call):
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    linked = len([m for m in MAPPINGS if m.get("lot_id")])
    kb = K()
    kb.row(B(f"🗑 Удалить лоты плагина + привязки ({linked})", callback_data=f"{P}_ac_del_lots"))
    kb.row(B(f"🔗 Удалить только привязки ({len(MAPPINGS)})", callback_data=f"{P}_ac_del_links"))
    kb.row(B("◀️ Отмена", callback_data=f"{P}_ac"))
    _edit(call,
          "<b>🗑 Удаление лотов / привязок</b>\n\n"
          f"Привязанных лотов: <b>{linked}</b> · всего привязок: <b>{len(MAPPINGS)}</b>\n\n"
          "• <b>Лоты плагина + привязки</b> — удаляет с FunPay все привязанные/созданные плагином "
          "лоты и очищает привязки. Чужие/ручные лоты не трогает.\n"
          "• <b>Только привязки</b> — оставляет лоты на FunPay, но очищает связи в плагине "
          "(после этого автосоздание будет считать их «отсутствующими»).\n\n"
          "Выберите действие:", kb)


def tg_ac_del_links(call):
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    kb = K()
    kb.row(B(f"✅ Да, очистить привязки ({len(MAPPINGS)})", callback_data=f"{P}_ac_del_links_yes"))
    kb.row(B("◀️ Отмена", callback_data=f"{P}_ac_delall"))
    _edit(call, f"🔗 <b>Подтверждение</b>\n\nОчистить <b>{len(MAPPINGS)}</b> привязок в плагине?\n"
                f"Лоты на FunPay <b>останутся</b>, но плагин будет считать их «отсутствующими».", kb)


def tg_ac_del_links_yes(call):
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    n = len(MAPPINGS)
    MAPPINGS.clear()
    save_mappings()
    if HIDDEN:
        HIDDEN.clear()
        save_hidden()
    bot.answer_callback_query(call.id, f"Очищено привязок: {n}")
    kb = K()
    kb.row(B("◀️ В меню создания", callback_data=f"{P}_ac"))
    _edit(call, f"🔗 <b>Привязки очищены.</b>\n\nУдалено связей: <b>{n}</b>. "
                f"Лоты на FunPay остались на месте.", kb)


def tg_ac_del_lots(call):
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    linked = len([m for m in MAPPINGS if m.get("lot_id")])
    if not linked:
        bot.answer_callback_query(call.id, "Нет привязанных лотов", show_alert=True)
        return
    kb = K()
    kb.row(B(f"✅ Да, удалить {linked} лотов", callback_data=f"{P}_ac_del_lots_yes"))
    kb.row(B("◀️ Отмена", callback_data=f"{P}_ac_delall"))
    _edit(call, f"🗑 <b>Подтверждение</b>\n\nБезвозвратно удалить с FunPay <b>{linked}</b> привязанных лотов "
                f"и очистить их привязки?\nЧужие/ручные лоты не затрагиваются.", kb)


def tg_ac_del_lots_yes(call):
    if not any(m.get("lot_id") for m in MAPPINGS):
        bot.answer_callback_query(call.id, "Нет привязанных лотов", show_alert=True)
        return
    if not _ac_begin("Удаление лотов…"):
        bot.answer_callback_query(call.id, "Уже идёт другой процесс", show_alert=True)
        return
    uid = call.from_user.id
    _edit(call, f"🗑 <b>Удаляю лоты плагина…</b>\n\n<code>{_bar(0, 1)}</code>\n\nПрогресс обновляется здесь.", None)
    threading.Thread(target=_delete_plugin_run, args=(uid, call.message.chat.id, call.message.id),
                     daemon=True).start()


def tg_ac_reprice(call):
    uid = call.from_user.id
    if not _ac_has_template():
        bot.answer_callback_query(call.id, "Сначала настройте шаблон", show_alert=True)
        return
    if _ac_running["on"]:
        bot.answer_callback_query(call.id, "Уже выполняется", show_alert=True)
        return
    n = len([m for m in MAPPINGS if m.get("auto_created") and m.get("lot_id")])
    if n == 0:
        bot.answer_callback_query(call.id, "Нет авто-созданных лотов для пересчёта", show_alert=True)
        return
    _ac_wizard[uid] = {"message_id": call.message.message_id}
    t = _ac_tpl()
    cur = _pct(t.get("markup_percent", 0))
    floor = _money(_min_price_floor(t))
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
    _wiz_msg(call.message.chat.id, uid,
             f"<b>♻️ Пересчёт цен</b>\n\n"
             f"Авто-лотов под управлением: <b>{n}</b>\n"
             f"Текущая наценка: <b>{cur}%</b> · мин. цена: <b>{floor}₽</b>\n\n"
             f"Отправьте <b>новый процент наценки</b> (напр. <code>80</code>) — плагин пересчитает "
             f"себестоимость×наценку и обновит цену во всех этих лотах на FunPay.\n"
             f"<i>Мин. цена остаётся «полом»: всё, что выходит дешевле, ставится в {floor}₽.</i>", kb)
    _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_reprice_pct")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def tg_ac_game_markup(call):
    uid = call.from_user.id
    _ac_wizard[uid] = {"message_id": call.message.message_id}
    kb = K()
    kb.add(B("❌ Отмена", callback_data=f"{P}_ac"))
    _wiz_msg(call.message.chat.id, uid,
             "<b>🎯 Наценка для отдельной игры</b>\n\n"
             "Отправьте ID товара KOSell и процент через пробел.\n"
             "Пример: <code>343 30</code>\n\n"
             "Чтобы вернуть общий процент шаблона: <code>343 default</code>.\n"
             "Для уже созданных лотов после этого запустите пересчёт цен.", kb)
    _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_game_markup_input")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def tg_ac_edit(call):
    if not _ac_has_template():
        bot.answer_callback_query(call.id, "Сначала настройте шаблон", show_alert=True)
        return
    t = _ac_tpl()
    kb = K()
    kb.row(B("📈 Наценка %", callback_data=f"{P}_ac_e:markup"))
    kb.row(B("📝 Название (RU+EN)", callback_data=f"{P}_ac_e:title"))
    kb.row(B("📄 Описание (RU+EN)", callback_data=f"{P}_ac_e:desc"))
    kb.row(_back_btn(f"{P}_ac"))
    txt = (
        f"<b>✏️ Изменить шаблон</b>\n\n"
        f"📈 Наценка: <b>{_pct(t.get('markup_percent', 0))}%</b>\n\n"
        f"📝 Название RU:\n<code>{_html.escape(t.get('summary_ru', '') or '—')}</code>\n"
        f"📝 Название EN:\n<code>{_html.escape(t.get('summary_en', '') or '—')}</code>\n\n"
        f"📄 Описание RU:\n<code>{_html.escape((t.get('desc_ru', '') or '—')[:120])}</code>\n"
        f"📄 Описание EN:\n<code>{_html.escape((t.get('desc_en', '') or '—')[:120])}</code>\n\n"
        f"<i>{PH_TIME} → время, {PH_GAME} → игра. Изменения вступят в силу для новых лотов; "
        f"для уже созданных — нажмите «Пересчитать цены» (для наценки).</i>"
    )
    _edit(call, txt, kb)


def tg_ac_edit_field(call):
    field = call.data.split(":", 1)[1]
    uid = call.from_user.id
    t = _ac_tpl()
    _ac_wizard[uid] = {"message_id": call.message.message_id}
    if field == "markup":
        kb = K()
        kb.add(B("❌ Отмена", callback_data=f"{P}_ac_edit"))
        _wiz_msg(call.message.chat.id, uid,
                 f"<b>📈 Наценка</b>\n\nТекущая: <b>{_pct(t.get('markup_percent', 0))}%</b>\n\n"
                 f"Отправьте новый процент (напр. <code>50</code>). Цены новых лотов будут "
                 f"= себестоимость KOSell × (1 + %).", kb)
        _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_e_markup")
    elif field == "title":
        kb = K()
        kb.add(B("❌ Отмена", callback_data=f"{P}_ac_edit"))
        _wiz_msg(call.message.chat.id, uid,
                 f"<b>📝 Название (краткое описание)</b>\n\n"
                 f"Отправьте в одном сообщении RU и EN через <code>|||</code>:\n"
                 f"<code>🟩 АРЕНДА {PH_GAME} STEAM 🌿 {PH_TIME} ||| RENTAL {PH_GAME} STEAM {PH_TIME}</code>\n\n"
                 f"Без <code>|||</code> — EN сделаю автоматически из RU (латиницей).", kb)
        _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_e_title")
    elif field == "desc":
        kb = K()
        kb.add(B("❌ Отмена", callback_data=f"{P}_ac_edit"))
        _wiz_msg(call.message.chat.id, uid,
                 f"<b>📄 Описание (подробное)</b>\n\n"
                 f"Отправьте в одном сообщении RU и EN через <code>|||</code>:\n"
                 f"<code>Текст RU {PH_GAME} ||| Text EN {PH_GAME}</code>\n\n"
                 f"Без <code>|||</code> — EN сделаю автоматически из RU.", kb)
        _set_tg_state(call.message.chat.id, call.message.message_id, uid, f"{P}_ac_e_desc")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def init(cardinal: "Cardinal"):
    global tg, bot, cardinal_instance
    tg = cardinal.telegram
    bot = cardinal.telegram.bot if cardinal.telegram else None
    cardinal_instance = cardinal

    load_all()
    try:
        _finalize_update_after_restart()
    except Exception as e:
        logger.error(f"{LP} finalize update error: {e}")
    _start_poller()

    if tg and bot:
        handlers = [
            (tg_main, lambda c: f"{CBT.PLUGIN_SETTINGS}:{UUID}" in c.data),
            (tg_main, lambda c: c.data == f"{P}_main"),
            (tg_info, lambda c: c.data == f"{P}_info"),
            (tg_restart, lambda c: c.data == f"{P}_restart"),
            (tg_toggle, lambda c: c.data.startswith(f"{P}_toggle:")),
            (tg_full, lambda c: c.data == f"{P}_full"),
            (tg_fullpage, lambda c: c.data.startswith(f"{P}_fullpage:")),
            (tg_apikey, lambda c: c.data == f"{P}_apikey"),
            (tg_balance, lambda c: c.data == f"{P}_balance"),
            (tg_stats, lambda c: c.data == f"{P}_stats"),
            (tg_maps, lambda c: c.data.startswith(f"{P}_maps:")),
            (tg_map_detail, lambda c: c.data.startswith(f"{P}_map:")),
            (tg_map_hours, lambda c: c.data.startswith(f"{P}_map_hours:")),
            (tg_map_cur, lambda c: c.data.startswith(f"{P}_map_cur:")),
            (tg_map_del, lambda c: c.data.startswith(f"{P}_map_del:")),
            (tg_map_del_yes, lambda c: c.data.startswith(f"{P}_map_dely:")),
            (tg_addmap, lambda c: c.data.startswith(f"{P}_addmap:")),
            (tg_addmap_manual, lambda c: c.data == f"{P}_addmap_manual"),
            (tg_pick_lot, lambda c: c.data.startswith(f"{P}_picklot:")),
            (tg_pick_product, lambda c: c.data.startswith(f"{P}_pickprod:")),
            (tg_prodlist, lambda c: c.data.startswith(f"{P}_prodlist:")),
            (tg_prodsearch, lambda c: c.data == f"{P}_prodsearch"),
            (tg_prodmanual, lambda c: c.data == f"{P}_prodmanual"),
            (tg_noop, lambda c: c.data == f"{P}_noop"),
            (tg_params, lambda c: c.data == f"{P}_params"),
            (tg_p_cur, lambda c: c.data == f"{P}_p_cur"),
            (tg_p_stars, lambda c: c.data == f"{P}_p_stars"),
            (tg_p_set, lambda c: c.data.startswith(f"{P}_p_set:")),
            (tg_texts, lambda c: c.data.startswith(f"{P}_texts")),
            (tg_text_edit, lambda c: c.data.startswith(f"{P}_text:")),
            (tg_text_reset, lambda c: c.data.startswith(f"{P}_textreset:")),
            (tg_ac_menu, lambda c: c.data == f"{P}_ac"),
            (tg_ac_w_start, lambda c: c.data == f"{P}_ac_w_start"),
            (tg_ac_game_markup, lambda c: c.data == f"{P}_ac_game_markup"),
            (tg_ac_edit, lambda c: c.data == f"{P}_ac_edit"),
            (tg_ac_edit_field, lambda c: c.data.startswith(f"{P}_ac_e:")),
            (tg_ac_w_dpre, lambda c: c.data.startswith(f"{P}_ac_w_dpre:")),
            (tg_ac_w_dcustom, lambda c: c.data == f"{P}_ac_w_dcustom"),
            (tg_ac_w_price_mode, lambda c: c.data.startswith(f"{P}_ac_w_pmode:")),
            (tg_ac_refresh, lambda c: c.data == f"{P}_ac_refresh"),
            (tg_ac_dbg, lambda c: c.data == f"{P}_ac_dbg"),
            (tg_ac_diag, lambda c: c.data == f"{P}_ac_diag"),
            (tg_ac_miss, lambda c: c.data == f"{P}_ac_miss"),
            (tg_ac_game_miss, lambda c: c.data == f"{P}_ac_game_miss"),
            (tg_ac_preview, lambda c: c.data.startswith(f"{P}_ac_prev:")),
            (tg_ac_confirm, lambda c: c.data == f"{P}_ac_confirm"),
            (tg_ac_go, lambda c: c.data == f"{P}_ac_go"),
            (tg_ac_cancel, lambda c: c.data == f"{P}_ac_cancel"),
            (tg_ac_reprice, lambda c: c.data == f"{P}_ac_reprice"),
            (tg_ac_delall, lambda c: c.data == f"{P}_ac_delall"),
            (tg_ac_del_lots, lambda c: c.data == f"{P}_ac_del_lots"),
            (tg_ac_del_lots_yes, lambda c: c.data == f"{P}_ac_del_lots_yes"),
            (tg_ac_del_links, lambda c: c.data == f"{P}_ac_del_links"),
            (tg_ac_del_links_yes, lambda c: c.data == f"{P}_ac_del_links_yes"),
        ]

        def _safe(handler):
            def wrapper(call):
                try:
                    handler(call)
                except Exception as e:
                    logger.error(f"{LP} [TG] {handler.__name__}: {e}", exc_info=True)
                    try:
                        bot.answer_callback_query(call.id, "Ошибка, см. логи", show_alert=True)
                    except Exception:
                        pass
            return wrapper

        for handler, cond in handlers:
            tg.cbq_handler(_safe(handler), cond)

        def _msg_filter(m):
            sd = _get_tg_state(m.chat.id, m.from_user.id)
            if not sd: 
                return False
            st = sd.get("state", "") if isinstance(sd, dict) else ""
            return st.startswith(P)

        tg.msg_handler(tg_msg_dispatch, func=_msg_filter)

    on_new_order.plugin_uuid = UUID
    on_new_message.plugin_uuid = UUID
    on_last_chat_message_changed.plugin_uuid = UUID

    def _drop_stale_handlers(items: list, keep):
        items[:] = [
            h for h in items
            if h is keep or getattr(h, "plugin_uuid", None) != UUID
        ]

    _drop_stale_handlers(cardinal.new_order_handlers, on_new_order)
    _drop_stale_handlers(cardinal.new_message_handlers, on_new_message)
    _drop_stale_handlers(cardinal.last_chat_message_changed_handlers, on_last_chat_message_changed)

    if on_new_order not in cardinal.new_order_handlers:
        cardinal.new_order_handlers.append(on_new_order)
    if on_new_message not in cardinal.new_message_handlers:
        cardinal.new_message_handlers.append(on_new_message)
    if on_last_chat_message_changed not in cardinal.last_chat_message_changed_handlers:
        cardinal.last_chat_message_changed_handlers.append(on_last_chat_message_changed)

    logger.info(f"{LP} Initialized v{VERSION}, mappings={len(MAPPINGS)}")


BIND_TO_PRE_INIT = [init]
BIND_TO_DELETE = _stop_poller
