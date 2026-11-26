"""Dependency-free syntax and configuration checks; does not replace runtime tests."""

import ast
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
failures = []
files = list((root / "backend").rglob("*.py")) + list((root / "deploy").glob("*.py"))
for file in files:
    if any(part in {".venv", "build", "dist", "__pycache__"} for part in file.parts):
        continue
    try:
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    except SyntaxError as error:
        failures.append(str(error))
for file in [
    root / "package.json",
    *root.glob("apps/*/package.json"),
    root / "apps/web/tsconfig.json",
    root / "apps/desktop/src-tauri/tauri.conf.json",
    root / "apps/desktop/src-tauri/capabilities/default.json",
]:
    try:
        json.loads(file.read_text(encoding="utf-8"))
    except ValueError as error:
        failures.append(f"{file}: {error}")
if failures:
    print("\n".join(failures))
    sys.exit(1)
print(f"PASS: Python syntax ({len(files)} files) and JSON configuration")
