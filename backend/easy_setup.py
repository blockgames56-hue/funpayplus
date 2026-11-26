"""Private stdin-only setup used by the VPS wizard; never puts credentials in argv."""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx
from app.config import config


class SetupError(Exception):
    pass


async def api(client, path, method="GET", body=None):
    try:
        response = await client.request(method, "/api" + path, json=body)
    except httpx.HTTPError:
        raise SetupError(
            "Сервер не ответил. Повторите настройку после проверки статуса контейнеров."
        ) from None
    if response.is_error:
        if path == "/accounts" and response.status_code == 409:
            return {"existing": True}
        raise SetupError(
            "Проверьте токен Telegram, golden_key и доступ сервера к внешним сервисам."
        )
    return response.json()


async def configure(token: str, golden_key: str, mini_app: str | None, *, transport=None):
    result = {"pair_url": "", "account_added": False, "telegram_skipped": False, "errors": []}
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8765",
        transport=transport,
        timeout=60,
        trust_env=False,
        headers={"Authorization": "Bearer " + config.api_token},
    ) as client:
        try:
            previous = await api(client, "/settings/telegram")
            if token or previous.get("has_token"):
                changes = {"enabled": True, "control_enabled": True}
                if token:
                    changes["token"] = token
                if mini_app is not None:
                    changes["mini_app_url"] = mini_app
                await api(client, "/settings/telegram", "PATCH", changes)
                result["pair_url"] = (await api(client, "/telegram/pair", "POST"))["url"]
            else:
                result["telegram_skipped"] = True
        except SetupError as error:
            result["errors"].append(str(error))
        if golden_key:
            try:
                await api(client, "/accounts", "POST", {"golden_key": golden_key})
                result["account_added"] = True
            except SetupError as error:
                result["errors"].append(str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description="VPS setup: credentials arrive on stdin only")
    parser.add_argument("--mini-app", default=None)
    args = parser.parse_args()
    token = sys.stdin.readline().strip()
    golden = sys.stdin.readline().strip()
    result = asyncio.run(configure(token, golden, args.mini_app))
    if result["pair_url"]:
        print("\nОткройте ссылку и нажмите «Запустить» в Telegram (действует 10 минут):")
        print(result["pair_url"])
        print("Chat ID и уведомления настроятся автоматически после привязки.")
        print(
            "Кнопка «Открыть панель» появится в боте."
            if args.mini_app
            else "Меню бота работает без домена и входящих портов."
        )
    if result["telegram_skipped"]:
        print("Telegram пропущен. Подключить позже: bash deploy/server.sh telegram")
    if result["account_added"]:
        print("Аккаунт FunPay подключён на сервере.")
    for error in result["errors"]:
        print(error, file=sys.stderr)
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
