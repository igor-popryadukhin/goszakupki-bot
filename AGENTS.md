# Repository Guidelines

A Python bot that monitors the goszakupki.by tender catalog, checks only the first pages for new tenders, filters them by user-defined keywords, and sends Telegram notifications with title and link. Prevents duplicates. Keywords and check interval can be updated on the fly without restarting.
## Project Structure & Module Organization
- `src/` — application code
  - `src/app.py` — entry point
  - `src/config.py` — env/config parsing
  - `src/provider/` — data sources (e.g., `goszakupki_http.py`)
  - `src/monitor/` — scheduling and matching logic
  - `src/db/` — SQLAlchemy models and repository
  - `src/tg/` — bot setup and handlers
- `Dockerfile`, `docker-compose.yml` — containerization/runtime
- `.env` — local environment (not committed)
- Tests should live under `tests/` mirroring `src/` modules.

## Build, Test, and Development Commands
- Install deps locally (no venv from Poetry):
  - `poetry install --no-root`
- Run locally:
  - `TELEGRAM_BOT_TOKEN=... python -m src.app`
- Build and run in Docker:
  - `docker compose up --build -d`
  - Logs: `docker compose logs -f --tail=200`
- Tests (if present):
  - `pytest -q`

## Coding Style & Naming Conventions
- Python style: PEP 8, 4‑space indentation, type hints required.
- Prefer dataclasses and explicit names (no one‑letter variables).
- Keep functions small; log context via `logging` (JSON formatter is configured in `src/logging_config.py`).
- File naming: `snake_case.py`; module structure should mirror domains (provider/monitor/db/tg).

## Testing Guidelines
- Use `pytest`; place tests under `tests/<module>/test_*.py`.
- Cover parsing and repo logic with focused unit tests.
- Avoid network in tests; use fixtures and sample HTML snippets.

## Commit & Pull Request Guidelines
- Commits: short, imperative subject; include rationale in body when needed.
- PRs must include:
  - Problem statement and high‑level approach
  - Affected files and risk considerations
  - How to validate (commands, expected logs/DB effects)

## Security & Configuration Tips
- Never commit secrets. Set variables in `.env` or compose `environment` (e.g., `TELEGRAM_BOT_TOKEN`, `SOURCE_PAGES_DEFAULT`, `DB_PATH`).
- Persistent DB volume: `./data:/data`. Ensure write perms for UID 1000.
- Respect rate limits (`HTTP_CONCURRENCY`, `RATE_LIMIT_RPS`) and timeouts.

## Agent‑Specific Instructions
- Make minimal, targeted changes; keep style consistent.
- If you add files, place them within the appropriate `src/` subpackage.
- When changing behavior, update docs and, if present, tests.
