#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm install --no-audit --no-fund
npm run build
export STORAGE_DIR="$PWD/storage"
printf 'FunPay Plus: http://127.0.0.1:8765\nВход без ручного ключа.\n'
cd backend
../.venv/bin/python -m alembic upgrade head
exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
