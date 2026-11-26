"""Shared allowlist for FunPay's site and its separate image CDN."""

from html import unescape
from urllib.parse import urljoin, urlparse

AVATAR_DOMAINS = ("funpay.com", "sfunpay.com")


def normalize_avatar_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = unescape(value.strip())
    if any(char in value for char in ("\r", "\n", "\t", "\\")):
        return None
    result = urljoin("https://funpay.com/", value)
    try:
        parsed = urlparse(result)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or not any(
                parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain)
                for domain in AVATAR_DOMAINS
            )
        ):
            return None
    except ValueError:
        return None
    # This is the site's generic silhouette, not a user's profile picture.
    if parsed.path.rstrip("/") == "/img/layout/avatar.png":
        return None
    return parsed._replace(fragment="").geturl()
