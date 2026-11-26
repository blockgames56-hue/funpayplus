"""Build a standalone backend + existing web UI without requiring Rust.

This is not a Tauri installer. Run the executable with a private STORAGE_DIR,
then open its localhost URL. The Tauri shell still needs Rust/MSVC separately.
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if not (root / "apps/web/dist/index.html").is_file():
    raise SystemExit("Run npm run build first")
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "FunPayPlus-Core",
        "--distpath",
        str(root / "artifacts"),
        "--workpath",
        str(root / ".verification/pyinstaller"),
        "--specpath",
        str(root / ".verification"),
        "--collect-submodules",
        "alembic",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "apscheduler",
        "--collect-all",
        "FunPayAPI",
        "--collect-all",
        "curl_cffi",
        "--collect-all",
        "tzdata",
        "--hidden-import",
        "sqlalchemy.dialects.sqlite.aiosqlite",
        "--hidden-import",
        "aiosqlite",
        "--hidden-import",
        "socksio",
        "--add-data",
        f"{root / 'backend/migrations'}{os.pathsep}migrations",
        "--add-data",
        f"{root / 'backend/app/source_messages.json'}{os.pathsep}app",
        "--add-data",
        f"{root / 'apps/web/dist'}{os.pathsep}apps/web/dist",
        "launcher.py",
    ],
    cwd=root / "backend",
    check=True,
)
