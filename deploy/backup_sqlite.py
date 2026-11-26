"""Consistent snapshot while the local SQLite application may still be running."""

import sqlite3
import sys

if len(sys.argv) != 3:
    raise SystemExit("Usage: python deploy/backup_sqlite.py SOURCE DESTINATION")
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as source:
    with sqlite3.connect(sys.argv[2]) as destination:
        source.backup(destination)
