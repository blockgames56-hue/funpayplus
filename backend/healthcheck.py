"""Container readiness includes the authenticated API and database connection."""

import json
import urllib.request

from app.config import config

request = urllib.request.Request(
    "http://127.0.0.1:8765/api/health",
    headers={"Authorization": f"Bearer {config.api_token}"},
)
with urllib.request.urlopen(request, timeout=5) as response:
    if json.load(response).get("ok") is not True:
        raise SystemExit(1)
