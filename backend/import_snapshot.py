"""Import a stopped local SQLite installation into a fresh VPS PostgreSQL database."""

import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.models import Base
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine


async def import_snapshot(path: str, engine):
    source = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with source.connect() as connection:
            names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        async with engine.begin() as target:
            if engine.url.get_backend_name() == "postgresql":
                # Two upload processes must never import the same installation twice.
                await target.execute(text("SELECT pg_advisory_xact_lock(681427001)"))
            for table in Base.metadata.sorted_tables:
                if (await target.execute(select(table).limit(1))).first() is not None:
                    raise RuntimeError("Целевая база уже содержит данные; перенос отменён")
            for table in Base.metadata.sorted_tables:
                if table.name not in names:
                    continue
                async with source.connect() as connection:
                    rows = (await connection.execute(select(table))).mappings().all()
                if rows:
                    values = [dict(row) for row in rows]
                    for row in values:
                        for name, value in row.items():
                            # SQLite drops timezone offsets; PostgreSQL needs UTC-aware values.
                            if isinstance(value, datetime) and value.tzinfo is None:
                                row[name] = value.replace(tzinfo=timezone.utc)
                    await target.execute(table.insert(), values)
                if "id" in table.c and engine.url.get_backend_name() == "postgresql":
                    # Names are static SQLAlchemy metadata, never user supplied.
                    await target.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                            f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table.name}), 1), true)"
                        )
                    )
    finally:
        await source.dispose()


def prepare_snapshot(source: Path, destination: Path) -> None:
    """Migrate a private copy so older desktop databases keep new column defaults."""
    if not source.is_file():
        raise ValueError("Файл базы для импорта не найден")
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as target:
            original.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Проверка базы для импорта не пройдена")
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///" + destination.resolve().as_posix(),
           "WORKERS_ENABLED": "false"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=Path(__file__).resolve().parent, env=env, check=True)


async def main(path: str):
    from app.db import engine

    try:
        with tempfile.TemporaryDirectory(prefix="funpay-import-") as directory:
            snapshot = Path(directory) / "snapshot.db"
            prepare_snapshot(Path(path), snapshot)
            await import_snapshot(str(snapshot), engine)
        print("Данные перенесены: аккаунты, настройки, плагины, чаты и история.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python import_snapshot.py /app/snapshot.db")
    asyncio.run(main(sys.argv[1]))
