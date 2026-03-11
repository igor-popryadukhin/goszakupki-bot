from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.config import HttpSelectorsConfig, ProviderConfig
from src.monitor.classification import ClassificationResult
from src.monitor.detail_service import DetailScanService


class DummyProvider:
    async def fetch_detail_text(self, url: str) -> str:
        return "Поставка бетона для ремонта школы"


class DummyBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, disable_web_page_preview: bool = False) -> None:
        self.messages.append((chat_id, text))


class DummyAuthState:
    def all_targets(self) -> list[int]:
        return [1001]


@dataclass(slots=True)
class DummyPrefs:
    keywords: list[str]
    enabled: bool = True


class DummyRepo:
    def __init__(self) -> None:
        self.saved: dict | None = None
        self.completed = False
        self.notification_created = False

    async def get_next_pending_detail(self):  # pragma: no cover - not used directly
        return None

    async def get_preferences(self) -> DummyPrefs:
        return DummyPrefs(keywords=["бетон"], enabled=True)

    async def mark_detail_loaded(self, detection_id: int, success: bool) -> None:
        return None

    async def save_detection_classification(self, **kwargs) -> None:
        self.saved = kwargs

    async def has_notification_global_sent(self, source_id: str, external_id: str) -> bool:
        return False

    async def create_notification_global(self, source_id: str, external_id: str, *, sent: bool) -> None:
        self.notification_created = sent

    async def complete_detail_scan(self, detection_id: int) -> None:
        self.completed = True


class DummyClassifier:
    async def classify(self, **kwargs):
        return "поставка бетона для ремонта школы", ClassificationResult(
            topic_id=1,
            subtopic_id=None,
            topic_code="construction",
            subtopic_code=None,
            confidence=0.91,
            decision_source="rules+embeddings",
            summary="Закупка строительных материалов для ремонта школы.",
            reasoning="Прямое указание на бетон и ремонтные работы.",
            matched_features=["бетон", "ремонт"],
            candidate_topics=[{"code": "construction", "score": 0.91}],
            keyword_matches=["бетон"],
            is_keyword_relevant=True,
        )


def test_detail_service_sends_notification_and_persists_classification() -> None:
    repo = DummyRepo()
    bot = DummyBot()
    service = DetailScanService(
        provider=DummyProvider(),
        repository=repo,
        bot=bot,
        provider_config=ProviderConfig(
            source_id="goszakupki.by",
            base_url="https://example.test",
            pages_default=1,
            check_interval_default=60,
            detail_check_interval_seconds=60,
            http_timeout_seconds=10,
            http_concurrency=1,
            rate_limit_rps=1.0,
            selectors=HttpSelectorsConfig(list_item=".item", title=".title", link="a"),
        ),
        auth_state=DummyAuthState(),
        classifier=DummyClassifier(),
    )
    item = type(
        "Pending",
        (),
        {
            "id": 5,
            "source_id": "goszakupki.by",
            "external_id": "auc123",
            "url": "https://example.test/tender",
            "title": "Поставка бетона",
            "procedure_type": None,
            "status": None,
            "deadline": None,
            "price": None,
            "retry_count": 0,
        },
    )()

    asyncio.run(service._process_item(item, DummyPrefs(keywords=["бетон"]), []))  # type: ignore[arg-type]

    assert repo.saved is not None
    assert repo.saved["status"] == "classified"
    assert repo.completed is True
    assert repo.notification_created is True
    assert len(bot.messages) == 1
