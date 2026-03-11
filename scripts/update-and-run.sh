#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCK_FILE="${PROJECT_ROOT}/.update-and-run.lock"

REMOTE="origin"
BRANCH=""
ALLOW_DIRTY=0
STASH_DIRTY=0
SKIP_TESTS=0

usage() {
  cat <<'EOF'
Usage: scripts/update-and-run.sh [options]

Options:
  --branch <name>   Branch to deploy. Default: current branch.
  --remote <name>   Git remote. Default: origin.
  --allow-dirty     Continue even if working tree has local changes.
  --stash-dirty     Stash local changes before update and re-apply after.
  --skip-tests      Skip pytest before restart.
  -h, --help        Show this help.

Behavior:
  1. Acquires a lock to prevent parallel runs.
  2. Verifies required tools and .env presence.
  3. Fetches from git and fast-forwards the target branch.
  4. Runs tests unless --skip-tests is provided.
  5. Rebuilds and restarts the docker compose stack.
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      --branch)
        shift
        [[ $# -gt 0 ]] || fail "--branch requires a value"
        BRANCH="$1"
        ;;
      --remote)
        shift
        [[ $# -gt 0 ]] || fail "--remote requires a value"
        REMOTE="$1"
        ;;
      --allow-dirty)
        ALLOW_DIRTY=1
        ;;
      --stash-dirty)
        STASH_DIRTY=1
        ;;
      --skip-tests)
        SKIP_TESTS=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "Unknown argument: $1"
        ;;
    esac
    shift
  done
}

cleanup() {
  if [[ -n "${STASH_REF:-}" ]]; then
    log "Re-applying stashed changes"
    git stash pop --index >/dev/null || log "Could not auto-apply stash; restore manually with: git stash list"
  fi
}

main() {
  parse_args "$@"

  require_cmd git
  require_cmd docker
  require_cmd poetry
  require_cmd flock

  cd "$PROJECT_ROOT"
  [[ -f ".env" ]] || fail ".env file not found in ${PROJECT_ROOT}"
  [[ -f "docker-compose.yml" ]] || fail "docker-compose.yml not found in ${PROJECT_ROOT}"

  exec 9>"$LOCK_FILE"
  flock -n 9 || fail "Another update is already running"
  trap cleanup EXIT

  if [[ -z "$BRANCH" ]]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  fi
  [[ "$BRANCH" != "HEAD" ]] || fail "Detached HEAD is not supported; use --branch"

  if [[ $ALLOW_DIRTY -eq 0 ]] && [[ $STASH_DIRTY -eq 0 ]] && [[ -n "$(git status --porcelain)" ]]; then
    fail "Working tree is dirty. Commit/stash changes or rerun with --stash-dirty / --allow-dirty"
  fi

  if [[ $STASH_DIRTY -eq 1 ]] && [[ -n "$(git status --porcelain)" ]]; then
    log "Stashing local changes"
    git stash push --include-untracked -m "auto-update $(date '+%Y-%m-%d %H:%M:%S')" >/dev/null
    STASH_REF=1
  fi

  log "Fetching ${REMOTE}"
  git fetch --prune "$REMOTE"

  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    log "Checking out ${BRANCH}"
    git checkout "$BRANCH" >/dev/null
  else
    log "Creating local branch ${BRANCH} from ${REMOTE}/${BRANCH}"
    git checkout -b "$BRANCH" "${REMOTE}/${BRANCH}" >/dev/null
  fi

  git rev-parse --verify "${REMOTE}/${BRANCH}" >/dev/null 2>&1 || fail "Remote branch not found: ${REMOTE}/${BRANCH}"

  log "Fast-forwarding ${BRANCH}"
  git merge --ff-only "${REMOTE}/${BRANCH}" >/dev/null

  if [[ $SKIP_TESTS -eq 0 ]]; then
    log "Running tests"
    poetry run pytest -q
  else
    log "Skipping tests by request"
  fi

  log "Rebuilding and restarting docker compose stack"
  docker compose up --build -d --remove-orphans

  log "Current containers"
  docker compose ps

  log "Update completed"
}

main "$@"
