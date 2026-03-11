from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Optional

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
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {value}") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid float for {name}: {value}") from exc


def _get_json_object(name: str) -> dict[str, Any]:
    value = os.getenv(name)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


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
    main: Optional[str] = None
    text_selectors: list[str] = field(default_factory=list)
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
class OllamaConfig:
    enabled: bool = True
    base_url: str = "http://127.0.0.1:11434"
    chat_model: str = "bjoernb/gemma3n-e2b"
    embedding_model: str = "nomic-embed-text"
    timeout_seconds: float = 60.0
    max_chars: int = 8000
    top_k_candidates: int = 5
    confidence_threshold: float = 0.72
    llm_trigger_margin: float = 0.08
    keyword_semantic_threshold: float = 0.40
    request_options: dict[str, Any] = field(default_factory=dict)

    @property
    def chat_api_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/chat"

    @property
    def embed_api_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/embed"


@dataclass(slots=True)
class LoggingConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "UTC"))


@dataclass(slots=True)
class AppConfig:
    telegram: TelegramConfig
    database: DatabaseConfig
    provider: ProviderConfig
    ollama: OllamaConfig
    logging: LoggingConfig

    @dataclass(slots=True)
    class AuthConfig:
        login: Optional[str] = None
        password: Optional[str] = None

        @property
        def enabled(self) -> bool:
            return bool((self.login or "") and (self.password or ""))

    auth: "AppConfig.AuthConfig" = field(default_factory=lambda: AppConfig.AuthConfig())


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

    def _split_csv(name: str) -> list[str]:
        raw = os.getenv(name)
        if not raw:
            return []
        return [seg.strip() for seg in raw.split(",") if seg.strip()]

    detail_selectors = HttpDetailSelectorsConfig(
        main=os.getenv("GZ_DETAIL_MAIN") or None,
        text_selectors=_split_csv("GZ_DETAIL_TEXT_SELECTORS"),
        exclude=_split_csv("GZ_DETAIL_EXCLUDE"),
    )

    detail_interval = _get_int("DETAIL_INTERVAL_SECONDS", _get_int("DETAIL_CHECK_INTERVAL_SECONDS", 60))
    detail_max_retries = _get_int("DETAIL_MAX_RETRIES", 5)
    detail_backoff_base = _get_int("DETAIL_BACKOFF_BASE_SECONDS", 60)
    detail_backoff_factor = _get_float("DETAIL_BACKOFF_FACTOR", 2.0)
    detail_backoff_max = _get_int("DETAIL_BACKOFF_MAX_SECONDS", 3600)

    provider_config = ProviderConfig(
        source_id=os.getenv("SOURCE_ID", "goszakupki.by"),
        base_url=os.getenv("SOURCE_BASE_URL", "https://goszakupki.by/tenders/posted"),
        pages_default=_get_int("SOURCE_PAGES_DEFAULT", 2),
        check_interval_default=_get_int("CHECK_INTERVAL_DEFAULT", 300),
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
        prefer_table=_get_bool("GZ_PREFER_TABLE", True),
    )

    ollama_config = OllamaConfig(
        enabled=_get_bool("OLLAMA_ENABLED", True),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        chat_model=os.getenv("OLLAMA_CHAT_MODEL", "bjoernb/gemma3n-e2b"),
        embedding_model=os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
        timeout_seconds=_get_float("OLLAMA_TIMEOUT_SECONDS", 60.0),
        max_chars=_get_int("OLLAMA_MAX_CHARS", 8000),
        top_k_candidates=_get_int("OLLAMA_TOP_K_CANDIDATES", 5),
        confidence_threshold=_get_float("OLLAMA_CONFIDENCE_THRESHOLD", 0.72),
        llm_trigger_margin=_get_float("OLLAMA_LLM_TRIGGER_MARGIN", 0.08),
        keyword_semantic_threshold=_get_float("OLLAMA_KEYWORD_SEMANTIC_THRESHOLD", 0.40),
        request_options=_get_json_object("OLLAMA_REQUEST_OPTIONS_JSON"),
    )

    return AppConfig(
        telegram=TelegramConfig(token=token),
        database=DatabaseConfig(path=db_path),
        provider=provider_config,
        ollama=ollama_config,
        logging=LoggingConfig(),
        auth=AppConfig.AuthConfig(
            login=os.getenv("AUTH_LOGIN") or None,
            password=os.getenv("AUTH_PASSWORD") or None,
        ),
    )
