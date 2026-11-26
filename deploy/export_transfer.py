"""Build a private VPS transfer snapshot without modifying the desktop installation."""

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.crypto import Vault  # noqa: E402
from dotenv import dotenv_values  # noqa: E402


def local_keys(storage: Path, env: dict) -> tuple[str, str]:
    master = env.get("SECRET_KEY")
    if not master:
        protected = storage / "encryption.key.dpapi"
        if protected.is_file():
            from app.os_secrets import dpapi

            master = dpapi(protected.read_bytes(), decrypt=True).decode("utf-8")
        else:
            master = (storage / "encryption.key").read_text(encoding="utf-8").strip()
    token = env.get("API_TOKEN")
    if not token:
        token_file = storage / "access.key"
        token = token_file.read_text(encoding="utf-8").strip() if token_file.is_file() else secrets.token_hex(32)
    if len(master) < 32 or len(token) < 24 or any(c in master + token for c in "\r\n\x00"):
        raise ValueError("Некорректные ключи локальной установки")
    return master, token


def export_snapshot(storage: Path, output: Path, env: dict, *, mailbox_key=None) -> None:
    source = storage / "pulse.db"
    if not source.is_file():
        raise ValueError("В выбранной папке нет pulse.db; укажите папку данных приложения")
    if output.exists():
        raise ValueError("Каталог экспорта уже существует; выберите новый")
    url = env.get("DATABASE_URL", "")
    if url and not url.startswith("sqlite"):
        raise ValueError("Перенос с ПК поддерживает SQLite; PostgreSQL переносится резервной копией")
    if url:
        from sqlalchemy.engine import make_url

        database = Path(make_url(url).database).resolve()
        if database != source.resolve():
            raise ValueError("DATABASE_URL указывает на другую базу; укажите её STORAGE_DIR")
    master, token = local_keys(storage, env)
    output.mkdir(parents=True, mode=0o700)
    snapshot = output / "snapshot.db"
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(snapshot)) as target:
            original.backup(target)
    snapshot.chmod(0o600)
    with closing(sqlite3.connect(snapshot)) as target, target:
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Проверка копии базы не пройдена")
        accounts = target.execute("SELECT golden_key FROM accounts").fetchall()
        for (encrypted,) in accounts:
            Vault(master).decrypt(encrypted)
        # Desktop mail credentials use a separate OS key; rewrap only the copy.
        mailboxes = target.execute("SELECT key, value FROM settings WHERE key LIKE 'imap_mailbox_%'").fetchall()
        for name, raw in mailboxes:
            data = json.loads(raw)
            if not isinstance(data, dict) or not data.get("host"):
                continue
            encrypted_names = [k for k in ("password", "proxy_password") if data.get(k)]
            if encrypted_names and env.get("MAILBOX_KEY_STORE", "system") != "master":
                if mailbox_key is None:
                    import keyring

                    mailbox_key = keyring.get_password("FunPay Plus Email Code Fetcher", "mailbox-encryption-key")
                if not mailbox_key:
                    raise ValueError("Ключ почтовых паролей не найден в системном хранилище")
                for field in encrypted_names:
                    data[field] = Vault(master).encrypt(Vault(mailbox_key).decrypt(data[field]))
            target.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(data, ensure_ascii=False), name))
    # Quote dotenv values so $ and # in existing keys stay literal in Compose.
    def quoted(value):
        return "'" + value.replace("'", "\\'") + "'"

    values = {
        "COMPOSE_PROJECT_NAME": "funpay-plus",
        "DB_PASSWORD": secrets.token_hex(24),
        "SECRET_KEY": master,
        "API_TOKEN": token,
        "PORT": "8080",
        "BIND_ADDRESS": "127.0.0.1",
        "PUBLIC_ORIGIN": "http://localhost:8080",
        "SECURE_COOKIE": "false",
        "FUNPAY_TRANSPORT": env.get("FUNPAY_TRANSPORT") or "httpx",
    }
    (output / "server.env").write_text("".join(f"{key}={quoted(value)}\n" for key, value in values.items()), encoding="utf-8")
    (output / "server.env").chmod(0o600)
    files = output / "storage"
    files.mkdir(mode=0o755)
    if (storage / "avatars").is_dir():
        shutil.copytree(storage / "avatars", files / "avatars")
    print(f"Подготовлена копия: {len(accounts)} аккаунтов; пароли и ключи не изменялись на ПК.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    export_snapshot(args.storage.resolve(), args.output.resolve(), env)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Libraries may include credentials in their exception text.
        print(f"Экспорт не выполнен ({type(error).__name__}). Проверьте папку данных и доступ к ключам Windows.", file=sys.stderr)
        raise SystemExit(1) from None
