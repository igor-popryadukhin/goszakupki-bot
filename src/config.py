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
class LoggingConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    timezone: str = field(default_factory=lambda: os.getenv("TZ", "UTC"))


@dataclass(slots=True)
class AppConfig:
    telegram: TelegramConfig
    database: DatabaseConfig
    provider: ProviderConfig
    logging: LoggingConfig
    semantic: "SemanticConfig"
    
    @dataclass(slots=True)
    class AuthConfig:
        login: Optional[str] = None
        password: Optional[str] = None

        @property
        def enabled(self) -> bool:
            return bool((self.login or "") and (self.password or ""))

    auth: "AppConfig.AuthConfig" = field(default_factory=lambda: AppConfig.AuthConfig())


@dataclass(slots=True)
class SemanticConfig:
    model: str
    threshold: float
    models_dir: Path
    use_xnli: bool
    zero_shot_model: Optional[str]
    zero_shot_threshold: float
    device: Optional[str] = None


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

    # Детальные селекторы
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

    # Детскан: интервал берём из нового ENV, либо из старого (для обратной совместимости)
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

    semantic_models_dir = Path(os.getenv("SEMANTIC_MODELS_DIR", "./models")).resolve()
    semantic_model = os.getenv("SEMANTIC_MODEL", "BAAI/bge-m3")
    semantic_threshold = _get_float("SEMANTIC_THRESHOLD", 0.7)
    semantic_use_xnli = _get_bool("SEMANTIC_USE_XNLI", False)
    semantic_xnli_model = os.getenv("SEMANTIC_XNLI_MODEL")
    if semantic_use_xnli and not semantic_xnli_model:
        semantic_xnli_model = "MoritzLaurer/mDeBERTa-v3-base-xnli"
    semantic_xnli_threshold = _get_float("SEMANTIC_XNLI_THRESHOLD", 0.5)
    semantic_device = os.getenv("SEMANTIC_DEVICE") or None

    return AppConfig(
        telegram=TelegramConfig(token=token),
        database=DatabaseConfig(path=db_path),
        provider=provider_config,
        logging=LoggingConfig(),
        semantic=SemanticConfig(
            model=semantic_model,
            threshold=semantic_threshold,
            models_dir=semantic_models_dir,
            use_xnli=semantic_use_xnli,
            zero_shot_model=semantic_xnli_model,
            zero_shot_threshold=semantic_xnli_threshold,
            device=semantic_device,
        ),
        auth=AppConfig.AuthConfig(
            login=os.getenv("AUTH_LOGIN") or None,
            password=os.getenv("AUTH_PASSWORD") or None,
        ),
    )
