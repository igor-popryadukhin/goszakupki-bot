from __future__ import annotations

import math
import re

from ..config import AnalysisConfig, OllamaConfig
from ..db.repo import Repository
from .analysis_types import AnalysisResult, KeywordMatch
from .embedding_service import EmbeddingService
from .keyword_registry import KeywordEntryView

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9]+")


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
        text_tokens = _extract_tokens(text)

        matches: list[KeywordMatch] = []
        for entry in entries:
            keyword_vector = await self._load_keyword_vector(entry)
            if not keyword_vector:
                continue
            semantic_score = _cosine_similarity(document_vector, keyword_vector)
            if semantic_score < self._analysis_config.semantic_review_threshold:
                continue
            lexical_support = _best_alias_support(text_tokens, entry.aliases)
            if lexical_support == 0.0 and semantic_score < (self._analysis_config.semantic_threshold + 0.12):
                continue
            score = (semantic_score * 0.8) + (lexical_support * 0.2)
            matches.append(
                KeywordMatch(
                    keyword_id=entry.id,
                    keyword=entry.source_phrase,
                    matched_text=entry.source_phrase,
                    match_type="semantic",
                    score=score,
                    reason=(
                        f"Семантическая близость {round(semantic_score * 100)}% "
                        f"и текстовая опора {round(lexical_support * 100)}% к фразе: {entry.source_phrase}"
                    ),
                )
            )

        if not matches:
            return None

        matches.sort(key=lambda item: (item.score or 0.0), reverse=True)
        top_matches = matches[: max(self._analysis_config.semantic_top_n, 1)]
        confidence = top_matches[0].score or 0.0
        top_support = _extract_support(top_matches[0].reason)
        needs_review = False
        is_relevant = confidence >= self._analysis_config.semantic_threshold
        if not is_relevant:
            needs_review = True
        if len(top_matches) > 1:
            top_gap = (top_matches[0].score or 0.0) - (top_matches[1].score or 0.0)
            if top_gap < 0.05:
                needs_review = True
        if top_support < 0.2:
            is_relevant = False
            needs_review = True

        return AnalysisResult(
            is_relevant=is_relevant,
            confidence=confidence,
            matches=top_matches,
            explanation="Лучшие совпадения найдены по embeddings с дополнительной проверкой текстовой опоры.",
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


def _extract_tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text) if len(token) >= 4]


def _best_alias_support(text_tokens: list[str], aliases: list[str]) -> float:
    if not text_tokens:
        return 0.0
    best = 0.0
    for alias in aliases:
        alias_tokens = _extract_tokens(alias)
        if not alias_tokens:
            continue
        hits = 0
        for alias_token in alias_tokens:
            if any(_tokens_related(alias_token, text_token) for text_token in text_tokens):
                hits += 1
        best = max(best, hits / len(alias_tokens))
    return best


def _tokens_related(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith(right) or right.startswith(left):
        return True
    common = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        common += 1
    return common >= 5


def _extract_support(reason: str) -> float:
    match = re.search(r"текстовая опора (\d+)%", reason)
    if match is None:
        return 0.0
    return int(match.group(1)) / 100.0
