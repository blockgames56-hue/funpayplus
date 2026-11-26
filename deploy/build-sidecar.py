"""Run on the target OS after installing Rust and Python dependencies."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
result = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True)
target = next(
    line.split(": ", 1)[1] for line in result.stdout.splitlines() if line.startswith("host:")
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "pulse-backend",
        "--collect-submodules",
        "alembic",
        "--collect-submodules",
        "sqlalchemy",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "apscheduler",
        "--collect-all",
        "curl_cffi",
        "--collect-all",
        "lxml",
        "--collect-all",
        "FunPayAPI",
        "--add-data",
        f"{root / 'apps/web/dist'}{os.pathsep}apps/web/dist",
        "--add-data",
        f"app/source_messages.json{os.pathsep}app",
        "--add-data",
        f"app/vendor/MANIFEST.json{os.pathsep}app/vendor",
        "--add-data",
        f"app/vendor/kosellrent{os.pathsep}app/vendor/kosellrent",
        "--add-data",
        f"app/vendor/pysteamauth/LICENSE{os.pathsep}app/vendor/pysteamauth",
        "--add-data",
        f"app/vendor/steampassword/LICENSE{os.pathsep}app/vendor/steampassword",
        "--add-data",
        f"migrations{os.pathsep}migrations",
        "--hidden-import",
        "aiosqlite",
        "--hidden-import",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "--hidden-import",
        "asyncpg",
        "--collect-all",
        "tzdata",
        "--hidden-import",
        "socksio",
        "launcher.py",
    ],
    cwd=root / "backend",
    check=True,
)
suffix = ".exe" if os.name == "nt" else ""
destination = root / "apps/desktop/src-tauri/binaries" / f"pulse-backend-{target}{suffix}"
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / f"backend/dist/pulse-backend{suffix}", destination)
print(f"Sidecar ready: {destination}")
