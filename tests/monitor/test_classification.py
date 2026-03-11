from __future__ import annotations

import asyncio
from dataclasses import dataclass

from src.config import OllamaConfig
from src.monitor.classification import ProcurementClassifier
from src.monitor.match import compile_keywords


@dataclass(slots=True)
class TopicRecord:
    id: int
    code: str
    name: str
    parent_id: int | None
    description: str
    synonyms: list[str]
    keywords: list[str]
    negative_keywords: list[str]
    embedding_text: str
    is_active: bool = True


class FakeRepository:
    def __init__(self, topics: list[TopicRecord]) -> None:
        self.topics = topics
        self.cache: dict[str, list[float]] = {}

    async def list_active_topic_profiles(self) -> list[TopicRecord]:
        return list(self.topics)

    async def get_embedding_cache(self, cache_key: str) -> list[float] | None:
        return self.cache.get(cache_key)

    async def set_embedding_cache(
        self,
        *,
        cache_key: str,
        source_type: str,
        source_ref: str,
        model: str,
        vector: list[float],
    ) -> None:
        self.cache[cache_key] = vector


class FakeOllamaClient:
    def __init__(self, *, embeddings: dict[str, list[float]], selected_code: str = "construction") -> None:
        self.embeddings = embeddings
        self.selected_code = selected_code
        self.prompts: list[str] = []

    async def close(self) -> None:
        return None

    async def embed(self, text: str) -> list[float]:
        return self.embeddings[text]

    async def structured_chat(self, *, prompt: str, schema: dict, system_prompt: str) -> tuple[dict, str]:
        self.prompts.append(prompt)
        if "полем summary" in prompt:
            return {"summary": "Закупка строительных материалов."}, '{"summary":"Закупка строительных материалов."}'
        return {
            "selected_code": self.selected_code,
            "reasoning": "Выбран наиболее близкий кандидат."
        }, '{"selected_code":"construction","reasoning":"Выбран наиболее близкий кандидат."}'


def test_classifier_uses_rules_and_semantic_keyword_match() -> None:
    topics = [
        TopicRecord(
            id=1,
            code="construction",
            name="Строительство",
            parent_id=None,
            description="Строительные работы и материалы",
            synonyms=["ремонт"],
            keywords=["бетон", "строительство"],
            negative_keywords=[],
            embedding_text="строительство бетон ремонт",
        ),
        TopicRecord(
            id=2,
            code="office",
            name="Офис",
            parent_id=None,
            description="Офисные поставки",
            synonyms=["канцелярия"],
            keywords=["бумага"],
            negative_keywords=[],
            embedding_text="канцелярия бумага офис",
        ),
    ]
    text = "поставка бетона и материалов для ремонта здания"
    repo = FakeRepository(topics)
    client = FakeOllamaClient(
        embeddings={
            text: [1.0, 0.0],
            "строительство бетон ремонт": [1.0, 0.0],
            "канцелярия бумага офис": [0.0, 1.0],
            "бетон": [1.0, 0.0],
        }
    )
    classifier = ProcurementClassifier(
        repository=repo,
        ollama_client=client,
        config=OllamaConfig(confidence_threshold=0.6, llm_trigger_margin=0.1, keyword_semantic_threshold=0.3),
    )

    normalized_text, result = asyncio.run(
        classifier.classify(
            detection_id=1,
            title=None,
            detail_text=text,
            keywords=compile_keywords(["бетон"]),
        )
    )

    assert "бетона" in normalized_text
    assert result.topic_code == "construction"
    assert result.decision_source == "rules+embeddings"
    assert result.is_keyword_relevant is True
    assert "бетон" in result.keyword_matches


def test_classifier_calls_llm_for_ambiguous_candidates() -> None:
    topics = [
        TopicRecord(
            id=1,
            code="construction",
            name="Строительство",
            parent_id=None,
            description="Строительство",
            synonyms=[],
            keywords=["ремонт"],
            negative_keywords=[],
            embedding_text="строительство ремонт",
        ),
        TopicRecord(
            id=2,
            code="services",
            name="Услуги",
            parent_id=None,
            description="Услуги",
            synonyms=[],
            keywords=["обслуживание"],
            negative_keywords=[],
            embedding_text="услуги обслуживание",
        ),
    ]
    text = "услуги по ремонту и обслуживанию здания"
    repo = FakeRepository(topics)
    client = FakeOllamaClient(
        embeddings={
            text: [0.7, 0.7],
            "строительство ремонт": [0.8, 0.6],
            "услуги обслуживание": [0.6, 0.8],
            "ремонт": [0.8, 0.6],
        },
        selected_code="construction",
    )
    classifier = ProcurementClassifier(
        repository=repo,
        ollama_client=client,
        config=OllamaConfig(confidence_threshold=0.9, llm_trigger_margin=0.2, keyword_semantic_threshold=0.3),
    )

    _, result = asyncio.run(
        classifier.classify(
            detection_id=2,
            title=None,
            detail_text=text,
            keywords=compile_keywords(["ремонт"]),
        )
    )

    assert result.decision_source == "llm_resolver"
    assert result.topic_code == "construction"
    assert client.prompts[-1].count("Кандидаты:") == 1
