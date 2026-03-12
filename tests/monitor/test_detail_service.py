from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.config import ProviderConfig
from src.monitor.detail_service import DetailScanService
from src.monitor.semantic import SemanticAnalysis, SemanticMatch


@dataclass(slots=True)
class DummyPendingDetail:
    id: int = 1
    source_id: str = "goszakupki.by"
    external_id: str = "auc123"
    url: str = "https://example.test/tender/auc123"
    title: str | None = "Tender title"
    retry_count: int = 0
    next_retry_at: object | None = None


class DummyProvider:
    async def fetch_detail_text(self, url: str) -> str:
        return "Закупка серверного оборудования и лицензий."


class DummyRepository:
    def __init__(self) -> None:
        self.detail_completed: list[int] = []
        self.detail_loaded: list[tuple[int, bool]] = []
        self.notifications_created: list[tuple[str, str, bool]] = []

    async def mark_detail_loaded(self, detection_id: int, success: bool) -> None:
        self.detail_loaded.append((detection_id, success))

    async def has_notification_global_sent(self, source_id: str, external_id: str) -> bool:
        return False

    async def create_notification_global(self, source_id: str, external_id: str, *, sent: bool) -> None:
        self.notifications_created.append((source_id, external_id, sent))

    async def complete_detail_scan(self, detection_id: int) -> None:
        self.detail_completed.append(detection_id)


class DummyBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_message(self, chat_id: int, text: str, disable_web_page_preview: bool = False) -> None:
        self.messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": disable_web_page_preview,
            }
        )


class DummyAuthState:
    def all_targets(self) -> list[int]:
        return [101]


class DummySemanticMatcher:
    def __init__(self, result: SemanticAnalysis | None = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc

    async def match_keywords(self, text: str, keywords: list[str]) -> SemanticAnalysis | None:
        if self._exc is not None:
            raise self._exc
        return self._result


def make_service(semantic_matcher: DummySemanticMatcher | None) -> tuple[DetailScanService, DummyRepository, DummyBot]:
    repository = DummyRepository()
    bot = DummyBot()
    provider_config = ProviderConfig(
        source_id="goszakupki.by",
        base_url="https://example.test/tenders",
        pages_default=3,
        check_interval_default=300,
        detail_check_interval_seconds=10,
        http_timeout_seconds=10,
        http_concurrency=3,
        rate_limit_rps=2.0,
        selectors=None,  # type: ignore[arg-type]
    )
    service = DetailScanService(
        provider=DummyProvider(),
        repository=repository,
        bot=bot,
        provider_config=provider_config,
        auth_state=DummyAuthState(),
        semantic_matcher=semantic_matcher,
    )
    return service, repository, bot


def test_process_item_sends_message_only_on_deepseek_match() -> None:
    analysis = SemanticAnalysis(
        summary="Закупка серверного оборудования.",
        matches=[SemanticMatch(keyword="сервер", score=0.92, reason="Упомянута поставка серверного оборудования")],
    )
    service, repository, bot = make_service(DummySemanticMatcher(result=analysis))

    asyncio.run(
        service._process_item(  # type: ignore[attr-defined]
            DummyPendingDetail(),
            prefs=type("Prefs", (), {"enabled": True})(),
            keywords=[type("Keyword", (), {"raw": "сервер"})()],
        )
    )

    assert repository.detail_loaded == [(1, True)]
    assert repository.detail_completed == [1]
    assert repository.notifications_created == [("goszakupki.by", "auc123", True)]
    assert len(bot.messages) == 1
    assert "Суть: Закупка серверного оборудования." in str(bot.messages[0]["text"])
    assert "Семантические совпадения:" in str(bot.messages[0]["text"])
    assert "сервер" in str(bot.messages[0]["text"])


def test_process_item_skips_when_deepseek_returns_none() -> None:
    service, repository, bot = make_service(DummySemanticMatcher(result=None))

    asyncio.run(
        service._process_item(  # type: ignore[attr-defined]
            DummyPendingDetail(),
            prefs=type("Prefs", (), {"enabled": True})(),
            keywords=[type("Keyword", (), {"raw": "сервер"})()],
        )
    )

    assert repository.detail_loaded == [(1, True)]
    assert repository.detail_completed == [1]
    assert repository.notifications_created == []
    assert bot.messages == []


def test_process_item_skips_when_deepseek_returns_empty_matches() -> None:
    analysis = SemanticAnalysis(summary="Закупка канцелярии.", matches=[])
    service, repository, bot = make_service(DummySemanticMatcher(result=analysis))

    asyncio.run(
        service._process_item(  # type: ignore[attr-defined]
            DummyPendingDetail(),
            prefs=type("Prefs", (), {"enabled": True})(),
            keywords=[type("Keyword", (), {"raw": "сервер"})()],
        )
    )

    assert repository.detail_loaded == [(1, True)]
    assert repository.detail_completed == [1]
    assert repository.notifications_created == []
    assert bot.messages == []


def test_process_item_skips_when_deepseek_times_out() -> None:
    service, repository, bot = make_service(DummySemanticMatcher(exc=TimeoutError()))

    asyncio.run(
        service._process_item(  # type: ignore[attr-defined]
            DummyPendingDetail(),
            prefs=type("Prefs", (), {"enabled": True})(),
            keywords=[type("Keyword", (), {"raw": "сервер"})()],
        )
    )

    assert repository.detail_loaded == [(1, True)]
    assert repository.detail_completed == [1]
    assert repository.notifications_created == []
    assert bot.messages == []
