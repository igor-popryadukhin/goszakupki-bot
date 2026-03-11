# Goszakupki Telegram Bot

Телеграм-бот для мониторинга опубликованных закупок на [goszakupki.by](https://goszakupki.by/tenders/posted).

## Возможности

- Периодический опрос первых N страниц каталога (листинг).
- Детальный разбор страниц закупок и локальный многоступенчатый анализ: нормализация текста, rules-based matching и семантическое сравнение через Ollama embeddings.
- Глобальные (единые) настройки: ключевые слова, интервал, число страниц.
- Выбор активной embedding-модели Ollama прямо из Telegram-бота по списку уже скачанных моделей.
- Уведомления с названием, ссылкой и номером закупки. Дедупликация уведомлений.

## Подготовка окружения

1. Создайте файл `.env` по образцу:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   SOURCE_IDS=goszakupki.by
   SOURCE_PREFIX=GZ
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
   OLLAMA_HOST=http://127.0.0.1
   OLLAMA_PORT=11434
   OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b
   OLLAMA_TIMEOUT_SECONDS=30
   ANALYSIS_SEMANTIC_THRESHOLD=0.84
   ANALYSIS_SEMANTIC_REVIEW_THRESHOLD=0.72
   ANALYSIS_SEMANTIC_TOP_N=5
   ANALYSIS_VERSION=1
   GZ_SOURCE_BASE_URL=https://goszakupki.by/tenders/posted
   GZ_LIST_ITEM=.tenders-list .tender-card
   GZ_TITLE=.tender-card__title
   GZ_LINK=.tender-card__title a
   GZ_ID_TEXT=.tender-card__meta
   GZ_ID_FROM_HREF=1
   GZ_PREFER_TABLE=1
   ICE_SOURCE_BASE_URL=https://icetrade.by/tenders/all
   ICE_LIST_ITEM=table tbody tr
   ICE_TITLE=a[href]
   ICE_LINK=a[href]
   ICE_TABLE_ROW=table tbody tr
   ICE_TABLE_LINK=a[href]
   ICE_TABLE_TITLE=a[href]
   ICE_TABLE_ID_CELL=td:nth-child(1)
   # Ограничение доступа (опционально). Если задать, бот потребует авторизацию /login <логин> <пароль>
   AUTH_LOGIN=admin
   AUTH_PASSWORD=secret
   # Детальная страница: текст извлекается из всего документа (селекторы не используются)
   ```

2. Проверка TLS-сертификата при HTTP-запросах отключена в коде (форсированно). Значение `HTTP_VERIFY_SSL` игнорируется.

3. При необходимости добавьте другие переменные из `src/config.py`.
   
   Источники задаются через `SOURCE_IDS` и префиксы окружения:
   - `SOURCE_IDS=goszakupki.by,icetrade.by`
   - `SOURCE_PREFIXES=GZ,ICE` (порядок совпадает с `SOURCE_IDS`)
   - Для каждого источника используйте переменные с префиксом (`GZ_`, `ICE_`), например `GZ_SOURCE_BASE_URL`, `ICE_SOURCE_BASE_URL` и селекторы `*_LIST_ITEM`, `*_TITLE`, `*_LINK`.
   
   Переменные `<PREFIX>_DETAIL_*` управляют выделением «основного контента» на странице закупки для полнотекстового поиска:
   - `<PREFIX>_DETAIL_MAIN` — CSS селектор контейнера основной области (если не задан, используется набор типичных кандидатов).
   - `<PREFIX>_DETAIL_TEXT_SELECTORS` — CSV-список селекторов внутри основного контейнера; если заданы, текст собирается только из них.
   - `<PREFIX>_DETAIL_EXCLUDE` — CSV-список селекторов, которые удаляются перед извлечением текста (меню, хлебные крошки, кнопки).
   
   Парсинг списка:
   - `<PREFIX>_PREFER_TABLE` — если 1, использовать табличный разбор раздела заявок (`//*[@id="w0"]/table/tbody/tr`) как основной метод.
   - `<PREFIX>_TABLE_ROW` — CSS селектор строк таблицы (отдельно от карточного листинга).
   - `<PREFIX>_TABLE_LINK` — CSS селектор ссылки внутри строки таблицы.
   - `<PREFIX>_TABLE_TITLE` — CSS селектор заголовка внутри строки таблицы (если отличается от ссылки).
   - `<PREFIX>_TABLE_ID_CELL` — CSS селектор ячейки с номером закупки/ID.
   - `<PREFIX>_TABLE_ID_FROM_HREF` — если 1, извлекать ID из ссылки (иначе сначала из таблицы).
   
   Параметры детального сканера:
   - `DETAIL_INTERVAL_SECONDS` — интервал тика детсканера (сканер всегда активен). Обрабатывается по одной записи за тик.
   - `DETAIL_MAX_RETRIES` — максимальное число повторов при неудачной загрузке.
   - `DETAIL_BACKOFF_BASE_SECONDS` — базовая задержка перед повтором.
   - `DETAIL_BACKOFF_FACTOR` — множитель экспоненты (2.0 означает удвоение задержки на каждый повтор).
   - `DETAIL_BACKOFF_MAX_SECONDS` — верхняя граница задержки.

## Авторизация

- Авторизация обязательна. Выполните `/login <логин> <пароль>`.
- Авторизованный чат сохраняется между перезапусками бота.
- Уведомления отправляются только в авторизованный чат.

## Локальный запуск (без Docker)

1. Установите зависимости через Poetry:

   ```bash
   poetry install --only main
   ```

2. Запустите приложение:

   ```bash
   python -m src.app
   ```

## Запуск в Docker

1. Соберите образ:

   ```bash
   docker compose build
   ```

2. Запустите контейнер в фоне:

   ```bash
   docker compose up -d
   ```

3. Логи работы:

   ```bash
   docker logs -f purchases-bot
   ```

4. Остановить и удалить контейнер:

   ```bash
   docker compose down
   ```

## Автообновление и полный перезапуск

Для полного цикла обновления через Git и перезапуска Docker Compose используйте:

```bash
./scripts/update_and_run.sh
```

Скрипт делает:
- проверку зависимостей (`git`, `docker`, `docker compose`);
- `git fetch` и `git pull --ff-only`;
- остановку старых контейнеров;
- пересборку образа;
- запуск проекта в фоне;
- запись подробного лога на русском в `logs/update_and_run_YYYY-MM-DD_HH-MM-SS.log`.

Если в репозитории есть незакоммиченные изменения или локальная ветка расходится с upstream, скрипт остановится с понятным сообщением.

Данные (SQLite-база) сохраняются в папку `./data` на хосте.

Примечания
- На этапе листинга уведомления не отправляются — они генерируются только после детального разбора страницы, где выполняется поиск по ключевым словам (и при необходимости по заголовку, если в тексте нет совпадений).
- В статусе показаны только отправленные уведомления (засеянные при включении не считаются).
- Команды `/status` и `/test` принимают необязательный `source_id` (например, `/status icetrade.by`). Без параметров выводится агрегированный результат по всем источникам.

## Семантический подбор ключевых слов

Для более точного соответствия ключевых слов содержанию закупки бот использует локальный pipeline анализа. После загрузки detail page текст нормализуется, затем проверяется быстрыми rules и при необходимости сравнивается по embeddings через локальный Ollama API.

Результат анализа сохраняется в БД отдельно от сырой закупки: бот хранит нормализованный текст, confidence, источник решения и список сработавших ключей.

Чтобы активировать режим:

1. Поднимите локальный Ollama.
2. Укажите `OLLAMA_HOST`, `OLLAMA_PORT` и `OLLAMA_EMBEDDING_MODEL`.
3. При необходимости настройте пороги `ANALYSIS_SEMANTIC_THRESHOLD`, `ANALYSIS_SEMANTIC_REVIEW_THRESHOLD` и версию пайплайна `ANALYSIS_VERSION`.

Если Ollama недоступен, semantic stage не сможет дать результат, но rules-based слой продолжит работать для прямых совпадений.
