#!/usr/bin/env bash
# Run from a checkout, or pipe the raw GitHub file into sudo bash -s -- --repo URL.
set -euo pipefail
repo=""
ref=main
destination=/opt/funpay-plus
domain=""
while (($#)); do
  case "$1" in
    --repo) repo="${2:?Укажите HTTPS или SSH URL репозитория}"; shift 2 ;;
    --ref) ref="${2:?Укажите ветку или тег}"; shift 2 ;;
    --dir) destination="${2:?Укажите каталог установки}"; shift 2 ;;
    --domain) domain="${2:?Укажите домен}"; shift 2 ;;
    --help|-h) echo 'sudo bash install.sh [--domain panel.example.com]'; echo 'curl -fsSL RAW_URL/install.sh | sudo bash -s -- --repo GIT_URL [--ref main] [--domain panel.example.com] [--dir /opt/funpay-plus]'; exit 0 ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
  esac
done
if [[ $(id -u) != 0 ]]; then echo 'Запустите установку с sudo.' >&2; exit 1; fi
if [[ -n "$repo" ]]; then
  [[ "$repo" != -* && "$ref" != -* && "$destination" == /* ]] || { echo 'Некорректный URL, ref или путь.' >&2; exit 1; }
  if [[ -e "$destination" ]]; then
    echo "Каталог $destination уже существует. Для обновления: cd '$destination' && sudo bash deploy/server.sh update" >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    . /etc/os-release
    case "$ID" in ubuntu|debian) ;; *) echo 'Поддерживаются Ubuntu и Debian.' >&2; exit 1 ;; esac
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates
  fi
  git clone --depth 1 --branch "$ref" -- "$repo" "$destination"
  cd "$destination"
else
  script="${BASH_SOURCE[0]:-}"
  if [[ ! -f "$script" ]]; then echo 'При запуске через curl укажите --repo URL.' >&2; exit 1; fi
  cd "$(dirname "$script")"
fi
[[ -f deploy/setup.sh && -f docker-compose.yml ]] || { echo 'Это не корень проекта FunPay Plus.' >&2; exit 1; }
args=()
if [[ -n "$domain" ]]; then args+=(--domain "$domain"); fi
bash deploy/setup.sh "${args[@]}"
