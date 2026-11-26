#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
domain=""
prepare=false
while (($#)); do
  case "$1" in
    --prepare-only) prepare=true; shift ;;
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
  grep -vE '^(DOMAIN|PUBLIC_ORIGIN|SECURE_COOKIE)=' .env > "$temporary" || true
  printf 'DOMAIN=%s\nPUBLIC_ORIGIN=https://%s\nSECURE_COOKIE=true\n' "$domain" "$domain" >> "$temporary"
  mv "$temporary" .env
fi
if $prepare; then exit 0; fi
compose=(docker compose -f docker-compose.yml)
if grep -qE '^DOMAIN=.+$' .env; then compose+=(-f deploy/compose.https.yml); fi
"${compose[@]}" config --quiet
bash deploy/check-project.sh
"${compose[@]}" up -d --build --wait --wait-timeout 300
if grep -qE '^DOMAIN=.+$' .env; then "${compose[@]}" exec -T backend python check_public.py; fi
printf '\nFunPay Plus запущен и продолжит работу после выхода из SSH и перезагрузки сервера.\n'
if grep -qE '^DOMAIN=.+$' .env; then
  printf 'Панель: https://%s\n' "$(sed -n 's/^DOMAIN=//p' .env | tail -1)"
else
  echo 'Доступ с ПК: ssh -L 8080:127.0.0.1:8080 USER@SERVER → http://localhost:8080'
fi
echo 'Ключ входа: sudo bash deploy/server.sh token'
