"""Verify the public HTTPS proxy, certificate and authenticated API after deployment."""

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from app.config import config


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(origin: str, token: str, *, timeout: float = 120):
    url = urlparse(origin)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("PUBLIC_ORIGIN должен быть HTTPS-адресом панели")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while True:
        try:
            request = urllib.request.Request(origin.rstrip("/") + "/api/health",
                headers={"Authorization": f"Bearer {token}"})
            with opener.open(request, timeout=5) as response:
                data = json.load(response)
            if isinstance(data, dict) and data.get("ok") is True and data.get("database") in {"postgresql", "sqlite-wal"}:
                return
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("HTTPS-панель пока недоступна. Проверьте DNS, сертификат и TCP 80/443; контейнеры оставлены запущенными.")
        time.sleep(2)


if __name__ == "__main__":
    try:
        verify(config.public_origin, config.api_token)
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from None
    print("PASS: public HTTPS, valid certificate, authenticated API and database")
