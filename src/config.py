from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid integer for {name}: {value}")


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"Invalid float for {name}: {value}")


@dataclass(slots=True)
class TelegramConfig:
    token: str


@dataclass(slots=True)
class DatabaseConfig:
    path: Path

    @property
    def url(self) -> str:
        return f"sqlite+aiosqlite:///{self.path}"


@dataclass(slots=True)
class HttpSelectorsConfig:
    list_item: str
    title: str
    link: str
    id_text: Optional[str] = None
    id_from_href: bool = False


@dataclass(slots=True)
class ProviderConfig:
    source_id: str
    base_url: str
    pages_default: int
    check_interval_default: int
    http_timeout_seconds: int
    http_concurrency: int
    rate_limit_rps: float
    selectors: HttpSelectorsConfig
    use_playwright: bool = False
    http_ca_bundle: Optional[Path] = None
    http_verify_ssl: bool = True


@dataclass(slots=True)
class LoggingConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "UTC"))


@dataclass(slots=True)
class AppConfig:
    telegram: TelegramConfig
    database: DatabaseConfig
    provider: ProviderConfig
    logging: LoggingConfig


def load_config() -> AppConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    db_path = Path(os.getenv("DB_PATH", "/data/app.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    selectors = HttpSelectorsConfig(
        list_item=os.getenv("GZ_LIST_ITEM", ".tenders-list .tender-card"),
        title=os.getenv("GZ_TITLE", ".tender-card__title"),
        link=os.getenv("GZ_LINK", ".tender-card__title a"),
        id_text=os.getenv("GZ_ID_TEXT"),
        id_from_href=_get_bool("GZ_ID_FROM_HREF", False),
    )

    ca_bundle_raw = os.getenv("HTTP_CA_BUNDLE")
    http_ca_bundle = Path(ca_bundle_raw).expanduser() if ca_bundle_raw else None

    provider_config = ProviderConfig(
        source_id=os.getenv("SOURCE_ID", "goszakupki.by"),
        base_url=os.getenv("SOURCE_BASE_URL", "https://goszakupki.by/tenders/posted"),
        pages_default=_get_int("SOURCE_PAGES_DEFAULT", 2),
        check_interval_default=_get_int("CHECK_INTERVAL_DEFAULT", 300),
        http_timeout_seconds=_get_int("HTTP_TIMEOUT_SECONDS", 10),
        http_concurrency=_get_int("HTTP_CONCURRENCY", 3),
        rate_limit_rps=_get_float("RATE_LIMIT_RPS", 2.0),
        selectors=selectors,
        use_playwright=_get_bool("USE_PLAYWRIGHT", False),
        http_ca_bundle=http_ca_bundle,
        http_verify_ssl=_get_bool("HTTP_VERIFY_SSL", True),
    )

    return AppConfig(
        telegram=TelegramConfig(token=token),
        database=DatabaseConfig(path=db_path),
        provider=provider_config,
        logging=LoggingConfig(),
    )
