#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
domain=""
prepare=false
easy=false
bot_only=false
public_ip=""
while (($#)); do
  case "$1" in
    --prepare-only) prepare=true; shift ;;
    --easy) easy=true; shift ;;
    --bot-only) easy=true; bot_only=true; shift ;;
    --ip) public_ip="${2:?Укажите публичный IPv4}"; easy=true; shift 2 ;;
    --domain) domain="${2:?Укажите домен}"; shift 2 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
  esac
done
if [[ -n "$domain" && ! "$domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]]; then
  echo 'Домен должен иметь вид panel.example.com, без https:// и пути.' >&2; exit 1
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  if [[ $(id -u) != 0 ]]; then
    echo 'Для установки Docker/Compose запустите скрипт с sudo.' >&2; exit 1
  fi
  . /etc/os-release
  case "$ID" in ubuntu|debian) ;; *) echo 'Поддерживаются Ubuntu и Debian.' >&2; exit 1 ;; esac
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  architecture="$(dpkg --print-architecture)"
  codename="${UBUNTU_CODENAME:-$VERSION_CODENAME}"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' "$architecture" "$ID" "$codename" > /etc/apt/sources.list.d/docker.list
  apt-get update
  if command -v docker >/dev/null 2>&1; then
    apt-get install -y docker-buildx-plugin docker-compose-plugin
  else
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  fi
fi
if [[ $(id -u) == 0 ]] && command -v systemctl >/dev/null 2>&1; then systemctl enable --now docker; fi
docker compose version >/dev/null
docker info >/dev/null
if ! command -v openssl >/dev/null 2>&1; then
  if [[ $(id -u) != 0 ]]; then echo 'Установите openssl или запустите с sudo.' >&2; exit 1; fi
  apt-get update && apt-get install -y openssl
fi
if [[ ! -f .env ]]; then
  {
    printf 'COMPOSE_PROJECT_NAME=funpay-plus\n'
    printf 'DB_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
    printf 'API_TOKEN=%s\n' "$(openssl rand -hex 32)"
    printf 'PORT=8080\nBIND_ADDRESS=127.0.0.1\nPUBLIC_ORIGIN=http://localhost:8080\nSECURE_COOKIE=false\n'
  } > .env
fi
chmod 600 .env
if [[ -n "$domain" ]]; then
  temporary=$(mktemp .env.XXXXXX)
  grep -vE '^(DOMAIN|PUBLIC_IP|PUBLIC_ORIGIN|SECURE_COOKIE)=' .env > "$temporary" || true
  printf 'DOMAIN=%s\nPUBLIC_ORIGIN=https://%s\nSECURE_COOKIE=true\n' "$domain" "$domain" >> "$temporary"
  mv "$temporary" .env
fi
# The easy mode uses a public IP certificate; no DNS account or paid domain.
valid_public_ipv4() {
  local a b c d octet
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  IFS=. read -r a b c d <<< "$1"
  for octet in "$a" "$b" "$c" "$d"; do
    [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] && ((10#$octet <= 255)) || return 1
  done
  (( a > 0 && a < 224 && a != 10 && a != 127 )) || return 1
  [[ "$a.$b" != 169.254 && "$a.$b" != 192.168 ]] || return 1
  (( !(a == 172 && b >= 16 && b <= 31) && !(a == 100 && b >= 64 && b <= 127) )) || return 1
  (( !(a == 198 && (b == 18 || b == 19)) && !(a == 192 && b == 0) )) || return 1
  [[ "$a.$b.$c" != 198.51.100 && "$a.$b.$c" != 203.0.113 ]] || return 1
}
if [[ -n "$public_ip" ]] && ! valid_public_ipv4 "$public_ip"; then
  echo 'Нужен публичный IPv4 VPS, без https:// и порта.' >&2; exit 1
fi
if $easy; then
  temporary=$(mktemp .env.XXXXXX)
  grep -vE '^TELEGRAM_ONLY=' .env > "$temporary" || true
  printf 'TELEGRAM_ONLY=true\n' >> "$temporary"
  mv "$temporary" .env
  if ! $bot_only && ! grep -qE '^DOMAIN=.+$' .env; then
    if [[ -z "$public_ip" ]]; then public_ip=$(sed -n 's/^PUBLIC_IP=//p' .env | tail -1); fi
    if [[ -z "$public_ip" ]] && ! $prepare; then
      public_ip=$(curl -4 -fsS --connect-timeout 5 --max-time 10 https://api.ipify.org 2>/dev/null || true)
    fi
    if [[ -n "$public_ip" ]] && valid_public_ipv4 "$public_ip"; then
      temporary=$(mktemp .env.XXXXXX)
      grep -vE '^(PUBLIC_IP|PUBLIC_ORIGIN|SECURE_COOKIE)=' .env > "$temporary" || true
      printf 'PUBLIC_IP=%s\nPUBLIC_ORIGIN=https://%s\nSECURE_COOKIE=true\n' "$public_ip" "$public_ip" >> "$temporary"
      mv "$temporary" .env
    elif ! $prepare; then
      echo 'Публичный IPv4 не определён. Установка продолжится с меню Telegram без HTTPS.'
    fi
  fi
fi
if $prepare; then exit 0; fi
compose=(docker compose -f docker-compose.yml)
if grep -qE '^DOMAIN=.+$' .env; then compose+=(-f deploy/compose.https.yml)
elif grep -qE '^PUBLIC_IP=.+$' .env; then compose+=(-f deploy/compose.ip.yml); fi
"${compose[@]}" config --quiet
bash deploy/check-project.sh
caddy_ready=true
if $easy; then
  # A busy web port must not prevent the shop and outbound Telegram polling.
  "${compose[@]}" up -d --build --wait --wait-timeout 300 backend frontend
  if ! $bot_only && grep -qE '^(DOMAIN|PUBLIC_IP)=.+$' .env; then
    if ! "${compose[@]}" up -d --wait --wait-timeout 60 caddy; then
      caddy_ready=false
      echo 'HTTPS не запущен: проверьте свободные TCP 80/443. Приложение и бот оставлены работать.' >&2
    fi
  fi
else
  "${compose[@]}" up -d --build --wait --wait-timeout 300
fi
https_ready=false
origin=$(sed -n 's/^PUBLIC_ORIGIN=//p' .env | tail -1)
if [[ "$origin" == https://* ]] && ! $bot_only && $caddy_ready; then
  if "${compose[@]}" exec -T backend python check_public.py; then
    https_ready=true
  elif $easy; then
    echo 'HTTPS пока недоступен. Проверьте входящие TCP 80/443 в панели VPS; бот продолжит работу без Mini App.' >&2
  else
    exit 1
  fi
fi
printf '\nFunPay Plus запущен и продолжит работу после выхода из SSH и перезагрузки сервера.\n'
if $https_ready; then
  printf 'Панель: %s\n' "$origin"
else
  echo 'Доступ с ПК: ssh -L 8080:127.0.0.1:8080 USER@SERVER → http://localhost:8080'
fi
if $easy; then
  mini_url=''
  if $https_ready; then mini_url="$origin"; fi
  bash deploy/connect-telegram.sh --mini-app "$mini_url" || true
  echo 'Готово. После привязки откройте своего Telegram-бота. Закрытие EXE или SSH не останавливает VPS.'
  echo 'Повторная настройка Telegram: bash deploy/server.sh telegram'
else
  echo 'Подключить Telegram без домена: bash deploy/server.sh telegram'
  echo 'Откройте панель и выберите режим запуска. Ключ входа не требуется.'
fi
