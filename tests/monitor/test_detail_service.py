from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.db.repo import AppPreferences, Repository
from src.monitor.detail_service import DetailScanService


class DummyProvider:
    def __init__(self, text: str) -> None:
        self._text = text

    async def fetch_detail_text(self, url: str) -> str:
        return self._text


class FakeSemanticAnalyzer:
    def __init__(self, query: str, score: float = 0.95) -> None:
        self.query = query
        self.score = score
        self.last: tuple[str | None, float] | None = None

    def is_relevant(self, text: str, queries: list[str], threshold: float) -> tuple[bool, float]:
        if self.query in queries and "серверного оборудования" in text:
            self.last = (self.query, self.score)
            return True, self.score
        self.last = None
        return False, 0.0

    def explain_last_match(self, text: str, queries: list[str]) -> tuple[str | None, float] | None:
        return self.last


async def _run_semantic_test() -> None:
    provider = DummyProvider("поставка серверного оборудования с монтажом")
    repo = SimpleNamespace(
        mark_detail_loaded=AsyncMock(),
        complete_detail_scan=AsyncMock(),
        has_notification_global_sent=AsyncMock(return_value=False),
        create_notification_global=AsyncMock(),
    )
    bot = AsyncMock()
    auth_state = SimpleNamespace(authorized_targets=lambda: [12345])
    analyzer = FakeSemanticAnalyzer("закупка оборудования", score=0.92)
    config = SimpleNamespace(source_id="test-source", detail=SimpleNamespace(interval_seconds=60))
    semantic_config = SimpleNamespace(threshold=0.7)

    service = DetailScanService(
        provider=provider,
        repository=repo,  # type: ignore[arg-type]
        bot=bot,
        provider_config=config,  # type: ignore[arg-type]
        auth_state=auth_state,  # type: ignore[arg-type]
        semantic_analyzer=analyzer,  # type: ignore[arg-type]
        semantic_config=semantic_config,  # type: ignore[arg-type]
    )

    prefs = AppPreferences(
        keywords=[],
        semantic_queries=["закупка оборудования"],
        interval_seconds=60,
        pages=1,
        enabled=True,
        semantic_threshold=0.5,
    )
    item = Repository.PendingDetail(
        id=1,
        source_id="test-source",
        external_id="abc",
        url="https://example.org",
        title="Тестовая закупка",
        retry_count=0,
        next_retry_at=None,
    )

    await service._process_item(item, prefs, [], prefs.semantic_queries)

    bot.send_message.assert_awaited_once()
    message_text = bot.send_message.await_args.kwargs["text"]
    assert "Семантическое совпадение" in message_text
    repo.create_notification_global.assert_awaited_once()
    repo.complete_detail_scan.assert_awaited_with(item.id)
    repo.mark_detail_loaded.assert_awaited_with(item.id, True)


def test_semantic_match_triggers_notification() -> None:
    asyncio.run(_run_semantic_test())
