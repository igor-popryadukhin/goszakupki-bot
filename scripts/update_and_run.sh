#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/logs"
TIMESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
LOG_FILE="${LOG_DIR}/update_and_run_${TIMESTAMP}.log"

mkdir -p "${LOG_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

log() {
  local level="$1"
  shift
  printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${level}" "$*"
}

fail() {
  log "ОШИБКА" "$*"
  exit 1
}

on_error() {
  local exit_code="$1"
  local line_no="$2"
  fail "Скрипт завершился с ошибкой. Код=${exit_code}, строка=${line_no}. Подробности смотрите в ${LOG_FILE}"
}

trap 'on_error $? ${LINENO}' ERR

require_command() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || fail "Не найдена обязательная команда: ${cmd}"
}

detect_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
    return 0
  fi
  fail "Не найден Docker Compose. Установите 'docker compose' или 'docker-compose'."
}

print_header() {
  log "ИНФО" "Запуск автоматического обновления и полного старта проекта."
  log "ИНФО" "Каталог проекта: ${PROJECT_DIR}"
  log "ИНФО" "Файл лога: ${LOG_FILE}"
}

check_environment() {
  require_command git
  require_command docker
  detect_compose_command

  [[ -d "${PROJECT_DIR}/.git" ]] || fail "Каталог ${PROJECT_DIR} не является git-репозиторием."
  [[ -f "${PROJECT_DIR}/docker-compose.yml" ]] || fail "В корне проекта не найден docker-compose.yml."

  if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    log "ПРЕДУПРЕЖДЕНИЕ" "Файл .env не найден. Docker Compose может не получить обязательные переменные."
  fi
}

check_git_state() {
  cd "${PROJECT_DIR}"
  local branch
  branch="$(git rev-parse --abbrev-ref HEAD)"
  log "ИНФО" "Текущая ветка Git: ${branch}"

  if [[ -n "$(git status --porcelain)" ]]; then
    fail "В репозитории есть незакоммиченные изменения. Остановлено, чтобы не сломать git pull."
  fi

  log "ИНФО" "Получаю изменения с удалённого репозитория."
  git fetch --all --prune

  local upstream
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [[ -z "${upstream}" ]]; then
    fail "Для текущей ветки не настроен upstream. Настройте tracking branch и повторите."
  fi

  local ahead behind
  ahead="$(git rev-list --count HEAD.."${upstream}" 2>/dev/null || echo 0)"
  behind="$(git rev-list --count "${upstream}"..HEAD 2>/dev/null || echo 0)"
  log "ИНФО" "Состояние ветки относительно ${upstream}: входящих коммитов=${ahead}, локальных коммитов=${behind}"

  if [[ "${behind}" -gt 0 ]]; then
    fail "В локальной ветке есть коммиты, которых нет в ${upstream}. Автоматический fast-forward pull остановлен."
  fi

  if [[ "${ahead}" -eq 0 ]]; then
    log "ИНФО" "Новых коммитов нет. Репозиторий уже актуален."
  else
    log "ИНФО" "Обновляю код через git pull --ff-only."
    git pull --ff-only
    log "ИНФО" "Код успешно обновлён."
  fi
}

restart_services() {
  cd "${PROJECT_DIR}"
  log "ИНФО" "Останавливаю текущие контейнеры проекта."
  "${COMPOSE_CMD[@]}" down --remove-orphans

  log "ИНФО" "Собираю свежий образ приложения."
  "${COMPOSE_CMD[@]}" build --pull

  log "ИНФО" "Запускаю контейнеры в фоновом режиме."
  "${COMPOSE_CMD[@]}" up -d --build --remove-orphans

  log "ИНФО" "Проверяю состояние сервисов."
  "${COMPOSE_CMD[@]}" ps
}

print_tail_hint() {
  log "ИНФО" "Обновление и запуск завершены успешно."
  log "ИНФО" "Для просмотра логов приложения используйте:"
  log "ИНФО" "  cd ${PROJECT_DIR} && ${COMPOSE_CMD[*]} logs -f --tail=200"
}

main() {
  print_header
  check_environment
  check_git_state
  restart_services
  print_tail_hint
}

main "$@"
