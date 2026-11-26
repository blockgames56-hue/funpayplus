#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
resume=false
if [[ ${1:-} == --resume ]]; then resume=true; shift; fi
[[ -f .transfer/server.env && -f .transfer/snapshot.db ]] || {
  echo 'Нужен новый сервер без .env и полный пакет переноса в .transfer.' >&2; exit 1;
}
if ! $resume; then
  [[ ! -f .env ]] || { echo 'Установка уже существует. Для продолжения прерванного переноса используйте --resume.' >&2; exit 1; }
  cp .transfer/server.env .env
fi
[[ -f .env ]] || { echo 'Нет .env для продолжения переноса.' >&2; exit 1; }
chmod 600 .env
bash deploy/setup.sh --prepare-only "$@"
compose=(docker compose -f docker-compose.yml)
if grep -qE '^DOMAIN=.+$' .env; then compose+=(-f deploy/compose.https.yml); fi
"${compose[@]}" config --quiet
bash deploy/check-project.sh
"${compose[@]}" up -d --wait --wait-timeout 180 postgres
"${compose[@]}" build backend frontend
# Create/migrate the target schema without starting the account workers.
"${compose[@]}" run --rm --no-deps -T -e WORKERS_ENABLED=false backend alembic upgrade head
chmod 755 .transfer
# Only the non-root container user can read the encrypted snapshot.
chown 10001:10001 .transfer/snapshot.db
chmod 600 .transfer/snapshot.db
if [[ ! -f .transfer/imported ]]; then
  "${compose[@]}" run --rm --no-deps -T -e WORKERS_ENABLED=false \
    -v "$PWD/.transfer/snapshot.db:/app/snapshot.db:ro" backend python import_snapshot.py /app/snapshot.db
  touch .transfer/imported
fi
if [[ -d .transfer/storage ]]; then
  "${compose[@]}" run --rm --no-deps -T -e WORKERS_ENABLED=false \
    -v "$PWD/.transfer/storage:/import-storage:ro" backend sh -c 'cp -R /import-storage/. /app/storage/'
fi
"${compose[@]}" up -d --wait --wait-timeout 300
if grep -qE '^DOMAIN=.+$' .env; then "${compose[@]}" exec -T backend python check_public.py; fi
rm -f .transfer/server.env .transfer/snapshot.db .transfer/imported
rm -rf -- .transfer/storage
rmdir .transfer
echo 'Перенос завершён. Сервер работает самостоятельно, ПК можно выключить.'
echo 'Ключ входа: sudo bash deploy/server.sh token'
