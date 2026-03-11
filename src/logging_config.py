from __future__ import annotations

import logging
import os
from typing import Any

import orjson


class JsonOrJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                orjson.dumps(value)
                payload[key] = value
            except orjson.JSONEncodeError:
                payload[key] = repr(value)
        return orjson.dumps(payload).decode()


def configure_logging(level: str = "INFO") -> None:
    logging.captureWarnings(True)
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonOrJsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)

    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    tz = os.getenv("TZ")
    if tz:
        try:
            import time

            os.environ["TZ"] = tz
            time.tzset()
        except Exception:  # pragma: no cover
            logging.getLogger(__name__).warning("Failed to set timezone", extra={"tz": tz})
