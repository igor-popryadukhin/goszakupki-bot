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


def _split_csv(name: str) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return []
    return [seg.strip() for seg in raw.split(",") if seg.strip()]


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
class HttpDetailSelectorsConfig:
    # Основной контейнер подробной страницы (CSS). Если пусто — используем fallback‑список в провайдере
    main: Optional[str] = None
    # Селекторы для текстовых блоков внутри main; если заданы, собираем текст только из них и объединяем
    text_selectors: list[str] = field(default_factory=list)
    # Селекторы элементов, которые нужно удалить перед извлечением текста (например, меню, кнопки)
    exclude: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderConfig:
    source_id: str
    base_url: str
    pages_default: int
    check_interval_default: int
    detail_check_interval_seconds: int
    http_timeout_seconds: int
    http_concurrency: int
    rate_limit_rps: float
    selectors: HttpSelectorsConfig
    detail_selectors: HttpDetailSelectorsConfig = field(default_factory=HttpDetailSelectorsConfig)
    prefer_table: bool = False
    # Секция детального сканирования
    @dataclass(slots=True)
    class DetailScanConfig:
        interval_seconds: int = 60
        max_retries: int = 5
        backoff_base_seconds: int = 60
        backoff_factor: float = 2.0
        backoff_max_seconds: int = 3600

    detail: "ProviderConfig.DetailScanConfig" = field(default_factory=lambda: ProviderConfig.DetailScanConfig())
    use_playwright: bool = False
    http_verify_ssl: bool = True


@dataclass(slots=True)
class DeepSeekConfig:
    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    enabled: bool = False
    timeout_seconds: float = 30.0
    min_score: float = 0.6
    max_chars: int = 6000
    max_keywords: int = 25

    @property
    def api_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


@dataclass(slots=True)
class LoggingConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "UTC"))


@dataclass(slots=True)
class AppConfig:
    telegram: TelegramConfig
    database: DatabaseConfig
    providers: list[ProviderConfig]
    deepseek: DeepSeekConfig
    logging: LoggingConfig

    @dataclass(slots=True)
    class AuthConfig:
        login: Optional[str] = None
        password: Optional[str] = None

        @property
        def enabled(self) -> bool:
            return bool((self.login or "") and (self.password or ""))

    auth: "AppConfig.AuthConfig" = field(default_factory=lambda: AppConfig.AuthConfig())

    @property
    def provider(self) -> ProviderConfig:
        if not self.providers:
            raise RuntimeError("No providers configured")
        return self.providers[0]


def _resolve_source_prefixes(source_ids: list[str]) -> list[str]:
    explicit_prefixes = _split_csv("SOURCE_PREFIXES")
    default_prefix_map = {
        "goszakupki.by": "GZ",
        "icetrade.by": "ICE",
    }
    if explicit_prefixes:
        if len(explicit_prefixes) != len(source_ids):
            raise ValueError("SOURCE_PREFIXES length must match SOURCE_IDS")
        return explicit_prefixes
    if len(source_ids) == 1:
        source_id = source_ids[0]
        prefix = os.getenv("SOURCE_PREFIX") or default_prefix_map.get(source_id)
        if not prefix:
            raise ValueError("SOURCE_PREFIX is required for custom SOURCE_IDS")
        return [prefix]
    prefixes: list[str] = []
    missing: list[str] = []
    for source_id in source_ids:
        prefix = default_prefix_map.get(source_id)
        if not prefix:
            missing.append(source_id)
            prefix = ""
        prefixes.append(prefix)
    if missing:
        raise ValueError(f"SOURCE_PREFIXES required for: {', '.join(missing)}")
    return prefixes


def _get_prefixed(name: str, prefix: str, default: Optional[str] = None) -> Optional[str]:
    key = f"{prefix}_{name}"
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def _get_prefixed_required(name: str, prefix: str, default: Optional[str] = None) -> str:
    value = _get_prefixed(name, prefix, default)
    if value is None or value == "":
        key = f"{prefix}_{name}"
        raise RuntimeError(f"{key} is not set")
    return value


def _load_provider_config(prefix: str, source_id: str) -> ProviderConfig:
    prefix = prefix.upper()
    default_selectors = {
        "GZ": {
            "list_item": ".tenders-list .tender-card",
            "title": ".tender-card__title",
            "link": ".tender-card__title a",
            "base_url": "https://goszakupki.by/tenders/posted",
            "prefer_table": True,
        }
    }
    defaults = default_selectors.get(prefix, {})
    selectors = HttpSelectorsConfig(
        list_item=_get_prefixed_required("LIST_ITEM", prefix, defaults.get("list_item")),
        title=_get_prefixed_required("TITLE", prefix, defaults.get("title")),
        link=_get_prefixed_required("LINK", prefix, defaults.get("link")),
        id_text=_get_prefixed("ID_TEXT", prefix),
        id_from_href=_get_bool(f"{prefix}_ID_FROM_HREF", False),
    )
    detail_selectors = HttpDetailSelectorsConfig(
        main=_get_prefixed("DETAIL_MAIN", prefix) or None,
        text_selectors=_split_csv(f"{prefix}_DETAIL_TEXT_SELECTORS"),
        exclude=_split_csv(f"{prefix}_DETAIL_EXCLUDE"),
    )

    detail_interval = _get_int("DETAIL_INTERVAL_SECONDS", _get_int("DETAIL_CHECK_INTERVAL_SECONDS", 60))
    detail_max_retries = _get_int("DETAIL_MAX_RETRIES", 5)
    detail_backoff_base = _get_int("DETAIL_BACKOFF_BASE_SECONDS", 60)
    detail_backoff_factor = _get_float("DETAIL_BACKOFF_FACTOR", 2.0)
    detail_backoff_max = _get_int("DETAIL_BACKOFF_MAX_SECONDS", 3600)

    return ProviderConfig(
        source_id=source_id,
        base_url=_get_prefixed_required("SOURCE_BASE_URL", prefix, defaults.get("base_url")),
        pages_default=_get_int(f"{prefix}_PAGES_DEFAULT", _get_int("SOURCE_PAGES_DEFAULT", 2)),
        check_interval_default=_get_int(f"{prefix}_CHECK_INTERVAL_DEFAULT", _get_int("CHECK_INTERVAL_DEFAULT", 300)),
        detail_check_interval_seconds=_get_int("DETAIL_CHECK_INTERVAL_SECONDS", 60),
        http_timeout_seconds=_get_int("HTTP_TIMEOUT_SECONDS", 10),
        http_concurrency=_get_int("HTTP_CONCURRENCY", 3),
        rate_limit_rps=_get_float("RATE_LIMIT_RPS", 2.0),
        selectors=selectors,
        detail_selectors=detail_selectors,
        detail=ProviderConfig.DetailScanConfig(
            interval_seconds=detail_interval,
            max_retries=detail_max_retries,
            backoff_base_seconds=detail_backoff_base,
            backoff_factor=detail_backoff_factor,
            backoff_max_seconds=detail_backoff_max,
        ),
        use_playwright=_get_bool("USE_PLAYWRIGHT", False),
        http_verify_ssl=_get_bool("HTTP_VERIFY_SSL", True),
        prefer_table=_get_bool(f"{prefix}_PREFER_TABLE", bool(defaults.get("prefer_table", False))),
    )


def load_config() -> AppConfig:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    db_path = Path(os.getenv("DB_PATH", "/data/app.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    source_ids = _split_csv("SOURCE_IDS")
    if not source_ids:
        source_ids = [os.getenv("SOURCE_ID", "goszakupki.by")]
    prefixes = _resolve_source_prefixes(source_ids)
    providers = [
        _load_provider_config(prefix=prefix, source_id=source_id)
        for source_id, prefix in zip(source_ids, prefixes)
    ]

    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY") or None
    deepseek_enabled = _get_bool("DEEPSEEK_ENABLED", bool(deepseek_api_key))
    if not deepseek_api_key:
        deepseek_enabled = False
    deepseek_config = DeepSeekConfig(
        api_key=deepseek_api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        enabled=deepseek_enabled,
        timeout_seconds=_get_float("DEEPSEEK_TIMEOUT_SECONDS", 30.0),
        min_score=_get_float("DEEPSEEK_MIN_SCORE", 0.6),
        max_chars=_get_int("DEEPSEEK_MAX_CHARS", 6000),
        max_keywords=_get_int("DEEPSEEK_MAX_KEYWORDS", 25),
    )

    return AppConfig(
        telegram=TelegramConfig(token=token),
        database=DatabaseConfig(path=db_path),
        providers=providers,
        deepseek=deepseek_config,
        logging=LoggingConfig(),
        auth=AppConfig.AuthConfig(
            login=os.getenv("AUTH_LOGIN") or None,
            password=os.getenv("AUTH_PASSWORD") or None,
        ),
    )
