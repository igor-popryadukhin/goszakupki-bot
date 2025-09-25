# Goszakupki Telegram Bot

Телеграм-бот для мониторинга опубликованных закупок на [goszakupki.by](https://goszakupki.by/tenders/posted).

## Возможности

- Периодический опрос первых *N* страниц каталога.
- Фильтрация новых закупок по ключевым словам.
- Управление настройками из чата: ключевые слова, интервал, число страниц.
- Отправка уведомлений с названием, ссылкой и номером закупки.

## Запуск

1. Создайте файл `.env` по образцу:

```
TELEGRAM_BOT_TOKEN=123456:ABC...
GZ_LIST_ITEM=.tenders-list .tender-card
GZ_TITLE=.tender-card__title
GZ_LINK=.tender-card__title a
GZ_ID_TEXT=.tender-card__meta
GZ_ID_FROM_HREF=1
```

2. Установите зависимости через Poetry или `pip install -r` (см. `pyproject.toml`).
3. Запустите приложение:

```
python -m src.app
```

Дополнительные переменные окружения описаны в `src/config.py`.
