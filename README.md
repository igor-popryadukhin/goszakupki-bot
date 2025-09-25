# Goszakupki Telegram Bot

Телеграм-бот для мониторинга опубликованных закупок на [goszakupki.by](https://goszakupki.by/tenders/posted).

## Возможности

- Периодический опрос первых *N* страниц каталога.
- Фильтрация новых закупок по ключевым словам.
- Управление настройками из чата: ключевые слова, интервал, число страниц.
- Отправка уведомлений с названием, ссылкой и номером закупки.

## Подготовка окружения

1. Создайте файл `.env` по образцу:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   SOURCE_PAGES_DEFAULT=2
   CHECK_INTERVAL_DEFAULT=300
   HTTP_TIMEOUT_SECONDS=10
   HTTP_CONCURRENCY=3
   RATE_LIMIT_RPS=2
   GZ_LIST_ITEM=.tenders-list .tender-card
   GZ_TITLE=.tender-card__title
   GZ_LINK=.tender-card__title a
   GZ_ID_TEXT=.tender-card__meta
   GZ_ID_FROM_HREF=1
   ```

2. При необходимости добавьте другие переменные из `src/config.py`.

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

Данные (SQLite-база) сохраняются в папку `./data` на хосте.
