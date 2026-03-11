from __future__ import annotations

import math

from ..config import AnalysisConfig, OllamaConfig
from ..db.repo import Repository
from .analysis_types import AnalysisResult, KeywordMatch
from .embedding_service import EmbeddingService
from .keyword_registry import KeywordEntryView


class SemanticMatcher:
    def __init__(
        self,
        *,
        repository: Repository,
        embedding_service: EmbeddingService,
        ollama_config: OllamaConfig,
        analysis_config: AnalysisConfig,
    ) -> None:
        self._repo = repository
        self._embedding_service = embedding_service
        self._ollama_config = ollama_config
        self._analysis_config = analysis_config

    async def match(
        self,
        *,
        text: str,
        entries: list[KeywordEntryView],
        analysis_version: int,
    ) -> AnalysisResult | None:
        if not self._analysis_config.semantic_enabled or not text.strip() or not entries:
            return None
        document_vector = await self._embedding_service.embed_text(text)
        if not document_vector:
            return None

        matches: list[KeywordMatch] = []
        for entry in entries:
            keyword_vector = await self._load_keyword_vector(entry)
            if not keyword_vector:
                continue
            score = _cosine_similarity(document_vector, keyword_vector)
            if score < self._analysis_config.semantic_review_threshold:
                continue
            matches.append(
                KeywordMatch(
                    keyword_id=entry.id,
                    keyword=entry.source_phrase,
                    matched_text=entry.source_phrase,
                    match_type="semantic",
                    score=score,
                    reason=f"Семантическая близость к фразе: {entry.source_phrase}",
                )
            )

        if not matches:
            return None

        matches.sort(key=lambda item: (item.score or 0.0), reverse=True)
        top_matches = matches[: max(self._analysis_config.semantic_top_n, 1)]
        confidence = top_matches[0].score or 0.0
        needs_review = False
        is_relevant = confidence >= self._analysis_config.semantic_threshold
        if not is_relevant:
            needs_review = True
        if len(top_matches) > 1:
            top_gap = (top_matches[0].score or 0.0) - (top_matches[1].score or 0.0)
            if top_gap < 0.03:
                needs_review = True

        return AnalysisResult(
            is_relevant=is_relevant,
            confidence=confidence,
            matches=top_matches,
            explanation="Лучшие совпадения найдены по embeddings.",
            needs_review=needs_review,
            decision_source="semantic",
            summary=text[:280],
            status="needs_review" if needs_review else "completed",
            analysis_version=analysis_version,
        )

    async def _load_keyword_vector(self, entry: KeywordEntryView) -> list[float]:
        cached = await self._repo.get_keyword_embedding(
            keyword_id=entry.id,
            model=self._ollama_config.embedding_model,
        )
        if cached is not None and cached.vector:
            return cached.vector
        vector = await self._embedding_service.embed_text(entry.source_phrase)
        if vector:
            await self._repo.upsert_keyword_embedding(
                keyword_id=entry.id,
                model=self._ollama_config.embedding_model,
                vector=vector,
            )
        return vector


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)
