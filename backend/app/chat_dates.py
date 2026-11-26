"""FunPay places timestamps in HTML, including a shared date for grouped messages."""

import re
from datetime import datetime, timedelta, timezone

FUNPAY_TIMEZONE = timezone(timedelta(hours=3))
UNKNOWN_DATE = datetime(1970, 1, 1, tzinfo=timezone.utc)
MONTHS = {
    name: n
    for n, name in enumerate(
        (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ),
        1,
    )
}


def message_date(item, soup):
    raw = item.get("date") or item.get("timestamp")
    if raw is not None:
        try:
            number = float(raw)
            if number > 10**11:
                number /= 1000
            if number > 0:
                return datetime.fromtimestamp(number, timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    node = soup.select_one(".chat-msg-date, time[datetime]")
    if not node:
        return None
    iso = node.get("datetime")
    if iso:
        try:
            value = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return (
                value.replace(tzinfo=FUNPAY_TIMEZONE).astimezone(timezone.utc)
                if value.tzinfo is None
                else value.astimezone(timezone.utc)
            )
        except ValueError:
            pass
    visible, title = node.get_text(" ", strip=True), node.get("title", "")
    clock = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", title or visible)
    numeric = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})\b", visible + " " + title)
    try:
        if numeric:
            day, month, year = map(int, numeric.groups())
            if year < 100:
                year += 2000
        else:
            named = re.search(
                r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", title.lower()
            )
            if not named:
                return None
            day, month = int(named[1]), MONTHS[named[2]]
            now = datetime.now(FUNPAY_TIMEZONE)
            year = int(named[3]) if named[3] else now.year
            if not named[3] and (month, day) > (now.month, now.day):
                year -= 1
        hour, minute, second = (
            (int(clock[1]), int(clock[2]), int(clock[3] or 0)) if clock else (0, 0, 0)
        )
        return datetime(year, month, day, hour, minute, second, tzinfo=FUNPAY_TIMEZONE).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def order_date(native, soup, *, now=None):
    """Correct SDK relative dates using FunPay's day/year, not the host timezone."""
    if not isinstance(native, datetime):
        return UNKNOWN_DATE
    if native.tzinfo is not None:
        return native.astimezone(timezone.utc)
    now = (now or datetime.now(FUNPAY_TIMEZONE)).astimezone(FUNPAY_TIMEZONE)
    node = soup.select_one(".tc-date-time")
    raw = node.get_text(" ", strip=True).casefold() if node else ""
    if "сегодня" in raw or "вчера" in raw:
        day = now.date() - timedelta(days=int("вчера" in raw))
        native = datetime.combine(day, native.time())
    elif any(month in raw for month in MONTHS) and not re.search(r"\b\d{4}\b", raw):
        year = now.year - int((native.month, native.day) > (now.month, now.day))
        try:
            native = native.replace(year=year)
        except ValueError:
            return UNKNOWN_DATE
    return native.replace(tzinfo=FUNPAY_TIMEZONE).astimezone(timezone.utc)
