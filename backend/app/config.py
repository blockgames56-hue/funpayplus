from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )
    storage_dir: Path = Path("storage")
    database_url: str = ""
    secret_key: str = ""
    api_token: str = ""
    allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,tauri://localhost,http://tauri.localhost"
    )
    public_origin: str = ""
    secure_cookie: bool = False
    telegram_only: bool = False
    workers_enabled: bool = True
    mailbox_key_store: str = "system"
    funpay_transport: str = "httpx"

    def prepare(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and not self.secret_key:
            from .os_secrets import load_master_key

            self.secret_key = load_master_key(self.storage_dir)
        for attr, filename in (
            ("secret_key", "encryption.key"),
            ("api_token", "access.key"),
        ):
            path = self.storage_dir / filename
            if not getattr(self, attr):
                if not path.exists():
                    with path.open("x", encoding="utf-8") as file:
                        file.write(secrets.token_urlsafe(48))
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        # Windows ACLs inherit from the user-owned storage directory.
                        if os.name != "nt":
                            raise
                setattr(self, attr, path.read_text(encoding="utf-8").strip())
        if len(self.secret_key) < 32 or len(self.api_token) < 24:
            raise ValueError("SECRET_KEY >= 32 и API_TOKEN >= 24 символов обязательны")
        if not self.database_url:
            self.database_url = (
                f"sqlite+aiosqlite:///{(self.storage_dir / 'pulse.db').resolve().as_posix()}"
            )


config = Config()
config.prepare()
