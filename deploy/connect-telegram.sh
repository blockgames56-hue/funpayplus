#!/usr/bin/env bash
set -euo pipefail
set +x
cd "$(dirname "${BASH_SOURCE[0]}")/.."
compose=(docker compose -f docker-compose.yml)
if grep -qE '^DOMAIN=.+$' .env; then compose+=(-f deploy/compose.https.yml)
elif grep -qE '^PUBLIC_IP=.+$' .env; then compose+=(-f deploy/compose.ip.yml); fi
if ! { true < /dev/tty > /dev/tty; } 2>/dev/null; then
  echo 'Для привязки нужен интерактивный SSH-терминал: bash deploy/server.sh telegram' >&2
  exit 1
fi
args=()
if [[ ${1:-} == --mini-app ]]; then args+=(--mini-app "${2:-}")
else
  origin=$(sed -n 's/^PUBLIC_ORIGIN=//p' .env | tail -1)
  if [[ "$origin" == https://* ]] && "${compose[@]}" exec -T backend python check_public.py; then
    args+=(--mini-app "$origin")
  fi
fi
printf '\nТокен бота от @BotFather (Enter — использовать сохранённый или пропустить): ' > /dev/tty
IFS= read -r -s bot_token < /dev/tty || bot_token=''
printf '\n' > /dev/tty
printf 'golden_key FunPay (Enter — оставить текущие аккаунты, подключить позже в панели): ' > /dev/tty
IFS= read -r -s golden_key < /dev/tty || golden_key=''
printf '\n' > /dev/tty
status=0
printf '%s\n%s\n' "$bot_token" "$golden_key" | "${compose[@]}" exec -T backend python easy_setup.py "${args[@]}" || status=$?
unset bot_token golden_key
if ((status)); then echo 'Сервер оставлен запущенным. Повторить подключение: bash deploy/server.sh telegram' >&2; fi
exit "$status"
