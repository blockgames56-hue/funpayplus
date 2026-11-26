#!/usr/bin/env bash
# Avoid reusing another installation's Compose name, containers and database volumes.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
project="${COMPOSE_PROJECT_NAME:-}"
if [[ -z "$project" ]]; then
  project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env | tail -1 | tr -d "'\"\r")
fi
[[ -n "$project" ]] || exit 0
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo 'Некорректный COMPOSE_PROJECT_NAME.' >&2; exit 1; }
for container in $(docker ps -aq --filter "label=com.docker.compose.project=$project"); do
  owner=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container")
  if [[ -z "$owner" || ! -d "$owner" || "$(cd "$owner" && pwd -P)" != "$(pwd -P)" ]]; then
    echo "Docker-проект $project уже используется другой установкой. Перенос/запуск отменён; её данные не изменены." >&2
    exit 1
  fi
done
