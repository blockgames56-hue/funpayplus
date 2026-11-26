"""Offline upgrade check on an empty DB and optionally a backup copy."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[1]
backup = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
with tempfile.TemporaryDirectory(prefix="funpay-upgrade-check-") as directory:
    for mode in (["blank", "backup-copy"] if backup else ["blank"]):
        target = Path(directory) / (mode + ".db")
        before = {}
        if mode == "backup-copy":
            with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(target)) as dest:
                    source.backup(dest)
                    tables = [r[0] for r in dest.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")
                        if r[0] != "alembic_version"]
                    for name in tables:
                        sql = 'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                        before[name] = dest.execute(sql).fetchone()[0]
        env = {**os.environ, "STORAGE_DIR": directory,
               "DATABASE_URL": "sqlite+aiosqlite:///" + target.as_posix(),
               "SECRET_KEY": "offline-migration-test-key-not-real",
               "API_TOKEN": "offline-migration-test-token-not-real", "WORKERS_ENABLED": "false"}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=root / "backend", env=env, check=True, capture_output=True)
        with closing(sqlite3.connect(target)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0021"
            assert db.execute("SELECT COUNT(*) FROM offline_guard_accounts").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM delivery_reservations").fetchone()[0] == 0
            columns = {row[1] for row in db.execute("PRAGMA table_info(chats)")}
            assert {"last_message_cursor", "runner_baselined", "runner_checked_at"} <= columns
            blacklist_columns = {row[1] for row in db.execute("PRAGMA table_info(blacklist)")}
            assert {"account_id", "chat_id", "user_id"} <= blacklist_columns
            for name, count in before.items():
                sql = 'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                assert db.execute(sql).fetchone()[0] == count
        print("PASS:", mode, "revision 0021, integrity ok, existing row counts preserved")
