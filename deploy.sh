#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

readonly ENV_FILE="$ROOT_DIR/.env"
readonly DATA_DIR="$ROOT_DIR/data"

COMPOSE_CMD=()

log_section() {
  printf '\n[deploy] === %s ===\n' "$*"
}

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || fail "Command not found: $command_name"
  log "Found command: $command_name"
}

mask_value() {
  local value="$1"
  local visible_prefix="${2:-4}"

  if [[ -z "$value" ]]; then
    printf '<empty>'
    return
  fi

  if (( ${#value} <= visible_prefix )); then
    printf '***'
    return
  fi

  printf '%s***' "${value:0:visible_prefix}"
}

print_runtime_summary() {
  local branch head origin_url
  branch="$(git rev-parse --abbrev-ref HEAD)"
  head="$(git rev-parse --short HEAD)"
  origin_url="$(git remote get-url origin)"

  log "Repository: $ROOT_DIR"
  log "Git branch: $branch"
  log "Git HEAD: $head"
  log "Git origin: $origin_url"
  log "Compose command: ${COMPOSE_CMD[*]}"
  log "Env file: $ENV_FILE"
  log "Data dir: $DATA_DIR"
}

print_env_summary() {
  local telegram_token auth_login auth_password deepseek_key
  telegram_token="$(get_env_value "TELEGRAM_BOT_TOKEN")"
  auth_login="$(get_env_value "AUTH_LOGIN")"
  auth_password="$(get_env_value "AUTH_PASSWORD")"
  deepseek_key="$(get_env_value "DEEPSEEK_API_KEY")"

  log "Environment summary:"
  log "  TELEGRAM_BOT_TOKEN=$(mask_value "$telegram_token")"
  log "  AUTH_LOGIN=${auth_login:-<empty>}"
  log "  AUTH_PASSWORD=$(mask_value "$auth_password" 0)"
  log "  DEEPSEEK_API_KEY=$(mask_value "$deepseek_key")"
  log "  data dir writable=yes"
}

detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
    log "Using compose implementation: docker compose"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
    log "Using compose implementation: docker-compose"
    return
  fi
  fail "Neither 'docker compose' nor 'docker-compose' is available"
}

ensure_repo_root() {
  [[ -f "$ROOT_DIR/docker-compose.yml" ]] || fail "docker-compose.yml not found in $ROOT_DIR"
  [[ -d "$ROOT_DIR/.git" ]] || fail ".git directory not found in $ROOT_DIR"
  log "Repository root check passed"
}

ensure_git_main_clean() {
  local current_branch
  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  [[ "$current_branch" == "main" ]] || fail "Current branch is '$current_branch'. Switch to 'main' before deploy."
  log "Current branch is main"

  if [[ -n "$(git status --porcelain)" ]]; then
    fail "Working tree has local changes. Commit, stash, or discard them before deploy."
  fi
  log "Working tree is clean"

  git remote get-url origin >/dev/null 2>&1 || fail "Git remote 'origin' is not configured"
  log "Git remote 'origin' is configured"
}

update_code() {
  local before_head after_head
  before_head="$(git rev-parse --short HEAD)"
  log "Fetching latest changes from origin/main"
  git fetch origin main
  log "Updating local main with fast-forward only"
  git pull --ff-only origin main
  after_head="$(git rev-parse --short HEAD)"
  if [[ "$before_head" == "$after_head" ]]; then
    log "Repository already up to date at $after_head"
  else
    log "Updated repository: $before_head -> $after_head"
  fi
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    log "Creating empty .env"
    : >"$ENV_FILE"
  else
    log ".env already exists"
  fi
}

get_env_value() {
  local key="$1"

  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi

  awk -v search_key="$key" '
    BEGIN { FS="=" }
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      pos = index(line, "=")
      if (pos == 0) {
        next
      }
      current_key = substr(line, 1, pos - 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", current_key)
      if (current_key != search_key) {
        next
      }
      value = substr(line, pos + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if ((value ~ /^".*"$/) || (value ~ /^'\''.*'\''$/)) {
        value = substr(value, 2, length(value) - 2)
      }
      print value
      found = 1
    }
    END {
      if (!found) {
        exit 0
      }
    }
  ' "$ENV_FILE" | tail -n 1
}

escape_env_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "$value"
}

prompt_required_value() {
  local key="$1"
  local prompt_text="$2"
  local secret="${3:-0}"
  local value=""

  while [[ -z "$value" ]]; do
    if [[ "$secret" == "1" ]]; then
      read -r -s -p "$prompt_text: " value
      printf '\n'
    else
      read -r -p "$prompt_text: " value
    fi
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    [[ -n "$value" ]] || log "Value for $key cannot be empty"
  done

  printf '%s' "$value"
}

update_env_file() {
  local temp_file
  temp_file="$(mktemp)"

  if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "$temp_file"
  fi

  local managed_keys=("$@")
  if [[ ${#managed_keys[@]} -gt 0 ]]; then
    awk '
      BEGIN {
        split(keys, raw_keys, "\n")
        for (key_idx in raw_keys) {
          if (raw_keys[key_idx] != "") {
            managed[raw_keys[key_idx]] = 1
          }
        }
      }
      {
        line = $0
        sub(/\r$/, "", line)
        if (line ~ /^[[:space:]]*#/ || line ~ /^[[:space:]]*$/) {
          print line
          next
        }
        pos = index(line, "=")
        if (pos == 0) {
          print line
          next
        }
        key = substr(line, 1, pos - 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
        if (!(key in managed)) {
          print line
        }
      }
    ' keys="$(printf '%s\n' "${managed_keys[@]}")" "$temp_file" >"${temp_file}.filtered"
    mv "${temp_file}.filtered" "$temp_file"
  fi

  for key in "${managed_keys[@]}"; do
    local raw_value="${ENV_VALUES[$key]}"
    printf '%s=%s\n' "$key" "$(escape_env_value "$raw_value")" >>"$temp_file"
  done

  mv "$temp_file" "$ENV_FILE"
}

run_env_wizard() {
  ensure_env_file

  declare -gA ENV_VALUES=()
  local managed_keys=()

  local telegram_token deepseek_key auth_login auth_password
  telegram_token="$(get_env_value "TELEGRAM_BOT_TOKEN")"
  deepseek_key="$(get_env_value "DEEPSEEK_API_KEY")"
  auth_login="$(get_env_value "AUTH_LOGIN")"
  auth_password="$(get_env_value "AUTH_PASSWORD")"

  if [[ -z "$telegram_token" ]]; then
    log "Missing required value: TELEGRAM_BOT_TOKEN"
    telegram_token="$(prompt_required_value "TELEGRAM_BOT_TOKEN" "Enter TELEGRAM_BOT_TOKEN" 1)"
    ENV_VALUES["TELEGRAM_BOT_TOKEN"]="$telegram_token"
    managed_keys+=("TELEGRAM_BOT_TOKEN")
  fi

  if [[ -z "$deepseek_key" ]]; then
    log "Missing required value: DEEPSEEK_API_KEY"
    deepseek_key="$(prompt_required_value "DEEPSEEK_API_KEY" "Enter DEEPSEEK_API_KEY" 1)"
    ENV_VALUES["DEEPSEEK_API_KEY"]="$deepseek_key"
    managed_keys+=("DEEPSEEK_API_KEY")
  fi

  if [[ -n "$auth_login" && -z "$auth_password" ]]; then
    log "AUTH_LOGIN is set but AUTH_PASSWORD is missing"
    auth_password="$(prompt_required_value "AUTH_PASSWORD" "Enter AUTH_PASSWORD for existing AUTH_LOGIN" 1)"
    ENV_VALUES["AUTH_PASSWORD"]="$auth_password"
    managed_keys+=("AUTH_PASSWORD")
  elif [[ -z "$auth_login" && -n "$auth_password" ]]; then
    log "AUTH_PASSWORD is set but AUTH_LOGIN is missing"
    auth_login="$(prompt_required_value "AUTH_LOGIN" "Enter AUTH_LOGIN for existing AUTH_PASSWORD" 0)"
    ENV_VALUES["AUTH_LOGIN"]="$auth_login"
    managed_keys+=("AUTH_LOGIN")
  fi

  if [[ ${#managed_keys[@]} -gt 0 ]]; then
    log "Updating .env with required values"
    update_env_file "${managed_keys[@]}"
  else
    log ".env already has all required values"
  fi
}

ensure_data_dir() {
  mkdir -p "$DATA_DIR"
  log "Ensured data directory exists: $DATA_DIR"

  local probe_file
  probe_file="$(mktemp "$DATA_DIR/.deploy-write-check.XXXXXX")" || fail "Directory $DATA_DIR is not writable"
  rm -f "$probe_file"
  log "Data directory is writable"
}

validate_compose() {
  log "Validating docker compose configuration"
  "${COMPOSE_CMD[@]}" config >/dev/null
  log "Compose configuration is valid"
}

deploy_stack() {
  log "Building and starting containers"
  "${COMPOSE_CMD[@]}" up -d --build
  log "Compose deploy finished"
}

show_status() {
  log "Current container status"
  "${COMPOSE_CMD[@]}" ps
  log "Logs: ${COMPOSE_CMD[*]} logs -f --tail=200"
}

main() {
  log_section "Bootstrap"
  require_command git
  require_command docker
  detect_compose
  ensure_repo_root
  print_runtime_summary

  log_section "Git Checks"
  ensure_git_main_clean
  update_code

  log_section "Environment"
  run_env_wizard
  ensure_data_dir
  print_env_summary

  log_section "Compose"
  validate_compose
  deploy_stack

  log_section "Status"
  show_status
}

main "$@"
