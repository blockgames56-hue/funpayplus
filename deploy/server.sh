#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
[[ -f .env ]] || { echo 'Сначала выполните sudo bash install.sh.' >&2; exit 1; }
compose=(docker compose -f docker-compose.yml)
if grep -qE '^DOMAIN=.+$' .env; then compose+=(-f deploy/compose.https.yml)
elif grep -qE '^PUBLIC_IP=.+$' .env; then compose+=(-f deploy/compose.ip.yml); fi
backup() {
  mkdir -p backups
  local directory running
  directory=$(mktemp -d "backups/$(date -u +%Y%m%d-%H%M%S)-XXXXXX")
  running=$("${compose[@]}" ps --status running --services backend)
  if [[ "$running" == backend ]]; then
    "${compose[@]}" stop backend
    trap '"${compose[@]}" start backend >/dev/null' EXIT
  fi
  cp .env "$directory/.env"
  "${compose[@]}" exec -T postgres pg_dump -U pulse_user -d pulse -Fc > "$directory/database.dump"
  "${compose[@]}" run --rm --no-deps -T backend tar -C /app/storage -czf - . > "$directory/storage.tar.gz"
  if [[ "$running" == backend ]]; then "${compose[@]}" start backend; trap - EXIT; fi
  echo "Резервная копия: $PWD/$directory (база, файлы, ключи)"
}
case "${1:-status}" in
  status) "${compose[@]}" ps ;;
  telegram) bash deploy/connect-telegram.sh ;;
  easy) bash deploy/setup.sh --easy ;;
  logs) "${compose[@]}" logs --tail 100 -f ;;
  token) "${compose[@]}" exec -T backend python -c 'from app.config import config; print(config.api_token)' ;;
  start) "${compose[@]}" up -d --wait --wait-timeout 300 ;;
  stop) "${compose[@]}" stop ;;
  restart) "${compose[@]}" restart ;;
  backup) backup ;;
  update)
    [[ -d .git ]] || { echo 'Это установка из архива. Для обновления из GitHub используйте перенос с параметром -Repository или установку install.sh --repo.' >&2; exit 1; }
    # Transfers may be cloned by the SSH user and managed with sudo later.
    git_command=(git -c "safe.directory=$PWD")
    [[ -z "$("${git_command[@]}" status --porcelain --untracked-files=no)" ]] || { echo 'Есть изменения исходников. Сохраните их перед обновлением.' >&2; exit 1; }
    backup
    "${git_command[@]}" pull --ff-only
    "${compose[@]}" build backend frontend
    "${compose[@]}" stop backend
    "${compose[@]}" up -d --wait --wait-timeout 300
    ;;
  *) echo 'Использование: sudo bash deploy/server.sh {status|logs|telegram|easy|token|start|stop|restart|backup|update}' >&2; exit 1 ;;
esac
