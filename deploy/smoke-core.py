"""Offline startup/HTTP smoke check of the packaged core with empty temporary storage."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing
from pathlib import Path

from alembic.script import ScriptDirectory

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
expected_revision = ScriptDirectory(str(root / "backend/migrations")).get_current_head()
executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "artifacts/FunPayPlus-Core/FunPayPlus-Core.exe"
with tempfile.TemporaryDirectory(prefix="funpay-core-smoke-") as temporary:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "STORAGE_DIR": temporary, "DATABASE_URL": "",
           "WORKERS_ENABLED": "false", "API_TOKEN": "offline-smoke-token-not-a-real-secret",
           "SECRET_KEY": "offline-smoke-encryption-not-a-real-secret"}
    log_path = root / ".verification/packaged-smoke-server.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([str(executable), "--port", str(port)], cwd=root, env=env,
                                 stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            base = f"http://127.0.0.1:{port}"
            def get(path):
                request = urllib.request.Request(base + path, headers={
                    "Authorization": "Bearer " + env["API_TOKEN"]
                })
                with opener.open(request, timeout=2) as response:
                    return response.read()
            for _ in range(80):
                if child.poll() is not None:
                    raise RuntimeError(f"Packaged server exited; see {log_path}")
                try:
                    health = json.loads(get("/api/health"))
                    break
                except (OSError, ValueError):
                    time.sleep(0.25)
            else:
                raise RuntimeError("Packaged server did not become ready")
            assert health["ok"] is True
            assert b"<html" in get("/").lower()
            status = json.loads(get("/api/automation/status"))
            assert status["accounts"] == [] and status["unknown_sends"] == 0
            assert json.loads(get("/api/automation/deliveries")) == []
            with closing(sqlite3.connect(Path(temporary) / "pulse.db")) as db:
                assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == expected_revision
                assert db.execute("SELECT COUNT(*) FROM delivery_reservations").fetchone()[0] == 0
            catalog = json.loads(get("/api/plugins/catalog"))
            assert isinstance(catalog, list) and catalog
            assert all(isinstance(item, dict) and item.get("id") for item in catalog)
            assert any(item["id"] == "kosell-rent" for item in catalog)
            rent = json.loads(get("/api/plugins/kosell-rent"))
            assert rent["version"] == "1.1.52" and rent["name"] == "KOSellRent"
            assert rent["source_sha256"] == "4603ba8f4d6ff79663a16571ec27d616f0ce9b7aa69e71ed7d05a9b72dccfeff"
            assert not rent["has_api_key"] and "api_key" not in rent["settings"]
            assert len(rent["groups"]) == 4 and rent["texts"]
            print("PASS: packaged executable, migrations, authenticated health, web UI, diagnostics, plugin catalog")
        finally:
            if os.name == "nt":
                # PyInstaller onefile has a bootloader parent and server child.
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                child.terminate()
            child.wait(timeout=15)
