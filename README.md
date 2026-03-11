# Goszakupki Telegram Bot

Телеграм-бот для мониторинга опубликованных закупок на [goszakupki.by](https://goszakupki.by/tenders/posted).

## Что умеет

- Периодически сканирует первые N страниц каталога закупок.
- Загружает детальные страницы и нормализует текст закупки.
- Классифицирует закупку локально через гибридный пайплайн:
  - rules и словарные признаки;
  - embeddings через Ollama;
  - LLM-арбитр через Ollama только для спорных случаев.
- Хранит результат классификации: тему, подтему, confidence, summary, признаки, кандидатов и ошибки анализа.
- Отправляет уведомления только после детального анализа и только если закупка релевантна текущим пользовательским `keywords`.

## Архитектура анализа

1. Из title, detail text и доступных метаполей собирается нормализованный текст.
2. Справочник тем и подтем из SQLite используется для rules-скоринга.
3. Для текста закупки и профилей тем считаются embeddings через Ollama.
4. Если лидер по score неуверенный или есть конфликт кандидатов, подключается chat-модель Ollama.
5. Результат классификации сохраняется в БД и используется для уведомления и диагностики.

## Подготовка окружения

Создайте `.env`:

```env
TELEGRAM_BOT_TOKEN=123456:ABC...
SOURCE_PAGES_DEFAULT=2
CHECK_INTERVAL_DEFAULT=300
HTTP_TIMEOUT_SECONDS=10
HTTP_CONCURRENCY=3
RATE_LIMIT_RPS=2
HTTP_VERIFY_SSL=0
DETAIL_INTERVAL_SECONDS=3
DETAIL_MAX_RETRIES=5
DETAIL_BACKOFF_BASE_SECONDS=60
DETAIL_BACKOFF_FACTOR=2.0
DETAIL_BACKOFF_MAX_SECONDS=3600

OLLAMA_ENABLED=1
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_CHAT_MODEL=bjoernb/gemma3n-e2b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_TIMEOUT_SECONDS=60
OLLAMA_MAX_CHARS=8000
OLLAMA_TOP_K_CANDIDATES=5
OLLAMA_CONFIDENCE_THRESHOLD=0.72
OLLAMA_LLM_TRIGGER_MARGIN=0.08
OLLAMA_KEYWORD_SEMANTIC_THRESHOLD=0.40

AUTH_LOGIN=admin
AUTH_PASSWORD=secret
```

Дополнительно можно передать `OLLAMA_REQUEST_OPTIONS_JSON`, если нужно задать опции модели через Ollama API.

## Локальный запуск

```bash
poetry install --no-root
python -m src.app
```

## Docker

```bash
docker compose up --build -d
docker compose logs -f --tail=200
```

По умолчанию контейнер ожидает Ollama на `http://host.docker.internal:11434`.

## Автообновление

Для сценария "запустил и забыл" добавлен скрипт:

```bash
./scripts/update-and-run.sh
```

Что он делает:

1. Проверяет, что есть `.env`, `git`, `poetry`, `docker` и `flock`.
2. Берёт lock, чтобы не запускаться параллельно.
3. Подтягивает изменения из `origin`.
4. Делает `fast-forward` текущей ветки.
5. Запускает `poetry run pytest -q`.
6. Выполняет `docker compose up --build -d --remove-orphans`.

Полезные опции:

- `--branch <name>` — обновить и запустить конкретную ветку.
- `--skip-tests` — пропустить `pytest`.
- `--stash-dirty` — временно убрать локальные изменения в stash, затем вернуть.
- `--allow-dirty` — не останавливать выполнение при грязном дереве.

Примеры:

```bash
./scripts/update-and-run.sh --branch feature/ollama-pipeline
./scripts/update-and-run.sh --skip-tests
./scripts/update-and-run.sh --stash-dirty
```

## Справочник тем

При первом запуске бот автоматически заполняет SQLite базовым справочником тем и подтем. В первом релизе темы не редактируются из Telegram, но используются для внутренней классификации и диагностики.

## Команды

- `/start` — старт и главное меню
- `/settings` — настройки
- `/set_keywords` — заменить список ключевых слов
- `/keywords` — добавление и удаление по одному
- `/set_interval 5m` — интервал опроса
- `/set_pages 2` — число страниц
- `/enable` / `/disable` — включить или выключить мониторинг
- `/status` — текущее состояние, очереди и счётчики классификации
- `/analysis_last <id>` — показать последнюю сохранённую классификацию по id детекции

## Диагностика

- В `detections` сохраняются нормализованный текст, статус и ошибка классификации.
- В `tender_classifications` сохраняются summary, reasoning, top candidates, matched features и raw LLM response.
- При недоступности Ollama уведомление не отправляется, а запись уходит в retry детального сканера.
