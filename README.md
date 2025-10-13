# Goszakupki Telegram Bot

Телеграм-бот для мониторинга опубликованных закупок на [goszakupki.by](https://goszakupki.by/tenders/posted).

## Возможности

- Периодический опрос первых N страниц каталога (листинг).
- Детальный разбор страниц закупок и фильтрация по ключевым словам (уведомления только после детразбора).
- Семантический анализ текста закупок: поиск по смыслу с управляемым порогом сходства.
- Глобальные (единые) настройки: ключевые слова, интервал, число страниц.
- Уведомления с названием, ссылкой и номером закупки. Дедупликация уведомлений.

## Подготовка окружения

1. Создайте файл `.env` по образцу:

   ```
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
   SEMANTIC_MODEL=BAAI/bge-m3
   SEMANTIC_THRESHOLD=0.7
   SEMANTIC_USE_XNLI=false
   SEMANTIC_XNLI_MODEL=MoritzLaurer/mDeBERTa-v3-base-xnli
   SEMANTIC_XNLI_THRESHOLD=0.5
   SEMANTIC_MODELS_DIR=./models
   GZ_LIST_ITEM=.tenders-list .tender-card
   GZ_TITLE=.tender-card__title
   GZ_LINK=.tender-card__title a
   GZ_ID_TEXT=.tender-card__meta
   GZ_ID_FROM_HREF=1
   GZ_PREFER_TABLE=1
   # Ограничение доступа (опционально). Если задать, бот потребует авторизацию /login <логин> <пароль>
   AUTH_LOGIN=admin
   AUTH_PASSWORD=secret
   # Детальная страница: текст извлекается из всего документа (селекторы не используются)
   ```

2. Проверка TLS-сертификата при HTTP-запросах отключена в коде (форсированно). Значение `HTTP_VERIFY_SSL` игнорируется.

3. При необходимости добавьте другие переменные из `src/config.py`.
   
   Переменные `GZ_DETAIL_*` управляют выделением «основного контента» на странице закупки для полнотекстового поиска:
   - `GZ_DETAIL_MAIN` — CSS селектор контейнера основной области (если не задан, используется набор типичных кандидатов).
   - `GZ_DETAIL_TEXT_SELECTORS` — CSV-список селекторов внутри основного контейнера; если заданы, текст собирается только из них.
   - `GZ_DETAIL_EXCLUDE` — CSV-список селекторов, которые удаляются перед извлечением текста (меню, хлебные крошки, кнопки).
   
   Парсинг списка:
   - `GZ_PREFER_TABLE` — если 1, использовать табличный разбор раздела заявок (`//*[@id="w0"]/table/tbody/tr`) как основной метод.
   
   Параметры детального сканера:
   - `DETAIL_INTERVAL_SECONDS` — интервал тика детсканера (сканер всегда активен). Обрабатывается по одной записи за тик.
   - `DETAIL_MAX_RETRIES` — максимальное число повторов при неудачной загрузке.
   - `DETAIL_BACKOFF_BASE_SECONDS` — базовая задержка перед повтором.
   - `DETAIL_BACKOFF_FACTOR` — множитель экспоненты (2.0 означает удвоение задержки на каждый повтор).
   - `DETAIL_BACKOFF_MAX_SECONDS` — верхняя граница задержки.

   Параметры семантического анализа:
   - `SEMANTIC_MODEL` — имя sentence-transformers модели (по умолчанию `BAAI/bge-m3`).
   - `SEMANTIC_THRESHOLD` — порог косинусного сходства (0.0–1.0).
   - `SEMANTIC_USE_XNLI` — если `true`, дополнительно загружать zero-shot классификатор.
   - `SEMANTIC_XNLI_MODEL` и `SEMANTIC_XNLI_THRESHOLD` — модель и порог для zero-shot оценки.
   - `SEMANTIC_MODELS_DIR` — директория с локально скачанными моделями (по умолчанию `./models`).
   - `SEMANTIC_DEVICE` — устройство для инференса (`cpu`, `cuda`, `auto`).

## Авторизация

- Авторизация обязательна. Выполните `/login <логин> <пароль>`.
- Авторизованный чат сохраняется между перезапусками бота.
- Уведомления отправляются только в авторизованный чат.

## Семантический поиск

- Настройте фразы для поиска по смыслу командами `/squeries`, `/add_squery`, `/del_squery`, `/clear_squeries`.
- Порог сходства изменяется командой `/set_sthreshold <0.0-1.0>` и хранится в базе.
- Перед запуском скачайте модели в директорию `SEMANTIC_MODELS_DIR`, например:

  ```bash
  python scripts/download_models.py --models-dir ./models
  ```

## Локальный запуск (без Docker)

1. Создайте изолированное виртуальное окружение (пример для стандартного `venv`):

   ```bash
   python -m venv .venv
   ```

2. Активируйте окружение:

   ```bash
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

3. Установите зависимости проекта (Poetry ставит их в активированное окружение):

   ```bash
   poetry install --no-root --only main
   ```

4. Запустите приложение, находясь в активном окружении:

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

Данные (SQLite-база) сохраняются в папку `./data` на хосте.

Примечания
- На этапе листинга уведомления не отправляются — они генерируются только после детального разбора страницы, где выполняется поиск по ключевым словам (и при необходимости по заголовку, если в тексте нет совпадений).
- В статусе показаны только отправленные уведомления (засеянные при включении не считаются).
