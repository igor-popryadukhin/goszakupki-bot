from __future__ import annotations

import asyncio

from src.config import AnalysisConfig, OllamaConfig
from src.monitor.analysis_pipeline import AnalysisPipeline
from src.monitor.keyword_registry import KeywordEntryView
from src.monitor.llm_resolver import LlmResolver
from src.monitor.rules_matcher import RulesMatcher
from src.monitor.semantic_matcher import SemanticMatcher
from src.monitor.text_normalizer import TenderTextPayload, TextNormalizer


class DummyRepository:
    def __init__(self) -> None:
        self.detail_payloads: list[tuple[int, str, str]] = []
        self.analysis_payloads: list[dict] = []
        self.keyword_embeddings: dict[tuple[int, str], list[float]] = {}

    async def save_detail_content(self, detection_id: int, *, raw_text: str, normalized_text: str) -> None:
        self.detail_payloads.append((detection_id, raw_text, normalized_text))

    async def save_analysis_result(self, detection_id: int, **kwargs) -> None:
        payload = dict(kwargs)
        payload["detection_id"] = detection_id
        self.analysis_payloads.append(payload)

    async def get_keyword_embedding(self, *, keyword_id: int, model: str):
        vector = self.keyword_embeddings.get((keyword_id, model))
        if vector is None:
            return None
        return type("KeywordEmbeddingRecord", (), {"keyword_id": keyword_id, "model": model, "vector": vector})()

    async def upsert_keyword_embedding(self, *, keyword_id: int, model: str, vector: list[float]) -> None:
        self.keyword_embeddings[(keyword_id, model)] = vector


class DummyKeywordRegistry:
    def __init__(self, entries: list[KeywordEntryView]) -> None:
        self._entries = entries

    async def get_entries(self, *, force_refresh: bool = False) -> list[KeywordEntryView]:
        return list(self._entries)


class DummyEmbeddingService:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed_text(self, text: str) -> list[float]:
        return list(self._vectors.get(text, []))


def test_text_normalizer_removes_html_and_normalizes_spaces() -> None:
    normalizer = TextNormalizer()

    result = normalizer.normalize(
        TenderTextPayload(
            title="  Ремонт тормозов ",
            raw_detail_text="<div>Поставка   деталей<script>alert(1)</script> и ремонт</div>",
        )
    )

    assert "alert" not in result.full_text
    assert result.full_text == "ремонт тормозов поставка деталей и ремонт"


def test_rules_matcher_returns_lexical_match() -> None:
    matcher = RulesMatcher()
    entries = [KeywordEntryView(id=1, source_phrase="ремонт тормозов", normalized_phrase="ремонт тормозов")]

    result = matcher.match("планируется ремонт тормозов автобуса", entries, analysis_version=3)

    assert result is not None
    assert result.is_relevant is True
    assert result.decision_source == "rules"
    assert result.matches[0].match_type == "lexical"


def test_semantic_matcher_uses_embedding_similarity() -> None:
    repo = DummyRepository()
    matcher = SemanticMatcher(
        repository=repo,
        embedding_service=DummyEmbeddingService(
            {
                "обслуживание тормозной системы автобуса": [1.0, 0.0],
                "ремонт тормозов": [0.99, 0.01],
            }
        ),
        ollama_config=OllamaConfig(embedding_model="test-embed"),
        analysis_config=AnalysisConfig(semantic_threshold=0.7, semantic_review_threshold=0.5, semantic_top_n=3),
    )
    entries = [KeywordEntryView(id=1, source_phrase="ремонт тормозов", normalized_phrase="ремонт тормозов")]

    result = asyncio.run(
        matcher.match(
            text="обслуживание тормозной системы автобуса",
            entries=entries,
            analysis_version=4,
        )
    )

    assert result is not None
    assert result.is_relevant is True
    assert result.matches[0].match_type == "semantic"


def test_analysis_pipeline_persists_result() -> None:
    repo = DummyRepository()
    entries = [KeywordEntryView(id=7, source_phrase="тормозная система", normalized_phrase="тормозная система")]
    pipeline = AnalysisPipeline(
        repository=repo,
        text_normalizer=TextNormalizer(),
        keyword_registry=DummyKeywordRegistry(entries),
        rules_matcher=RulesMatcher(),
        semantic_matcher=SemanticMatcher(
            repository=repo,
            embedding_service=DummyEmbeddingService(
                {
                    "закупка тормозная система автобуса": [1.0, 0.0],
                    "тормозная система": [1.0, 0.0],
                }
            ),
            ollama_config=OllamaConfig(embedding_model="test-embed"),
            analysis_config=AnalysisConfig(semantic_threshold=0.7, semantic_review_threshold=0.5, semantic_top_n=3),
        ),
        llm_resolver=LlmResolver(),
        config=AnalysisConfig(analysis_version=9, semantic_threshold=0.7, semantic_review_threshold=0.5),
    )

    result = asyncio.run(
        pipeline.analyze_detection(
            detection_id=101,
            payload=TenderTextPayload(title="Закупка", raw_detail_text="тормозная система автобуса"),
        )
    )

    assert result.is_relevant is True
    assert repo.detail_payloads
    assert repo.analysis_payloads[0]["analysis_version"] == 9
    assert repo.analysis_payloads[0]["decision_source"] in {"rules", "semantic"}


def test_semantic_matcher_does_not_auto_accept_weak_anchor_match() -> None:
    repo = DummyRepository()
    matcher = SemanticMatcher(
        repository=repo,
        embedding_service=DummyEmbeddingService(
            {
                "услуги для городской инфраструктуры": [1.0, 0.0],
                "серверное оборудование": [1.0, 0.0],
            }
        ),
        ollama_config=OllamaConfig(embedding_model="test-embed"),
        analysis_config=AnalysisConfig(semantic_threshold=0.84, semantic_review_threshold=0.72, semantic_top_n=3),
    )
    entries = [KeywordEntryView(id=5, source_phrase="серверное оборудование", normalized_phrase="серверное оборудование")]

    result = asyncio.run(
        matcher.match(
            text="услуги для городской инфраструктуры",
            entries=entries,
            analysis_version=5,
        )
    )

    assert result is not None
    assert result.is_relevant is False
    assert result.needs_review is True
