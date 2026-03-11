from __future__ import annotations

import logging

from ..config import AnalysisConfig
from ..db.repo import Repository
from .analysis_types import AnalysisResult
from .keyword_registry import KeywordRegistry
from .llm_resolver import LlmResolver
from .rules_matcher import RulesMatcher
from .semantic_matcher import SemanticMatcher
from .text_normalizer import NormalizedTenderText, TenderTextPayload, TextNormalizer

LOGGER = logging.getLogger(__name__)


class AnalysisPipeline:
    def __init__(
        self,
        *,
        repository: Repository,
        text_normalizer: TextNormalizer,
        keyword_registry: KeywordRegistry,
        rules_matcher: RulesMatcher,
        semantic_matcher: SemanticMatcher,
        llm_resolver: LlmResolver,
        config: AnalysisConfig,
    ) -> None:
        self._repo = repository
        self._text_normalizer = text_normalizer
        self._keyword_registry = keyword_registry
        self._rules_matcher = rules_matcher
        self._semantic_matcher = semantic_matcher
        self._llm_resolver = llm_resolver
        self._config = config

    @property
    def analysis_version(self) -> int:
        return self._config.analysis_version

    async def warmup(self) -> None:
        await self._keyword_registry.refresh()

    async def analyze_detection(
        self,
        *,
        detection_id: int,
        payload: TenderTextPayload,
    ) -> AnalysisResult:
        normalized = self._text_normalizer.normalize(payload)
        await self._repo.save_detail_content(
            detection_id,
            raw_text=payload.raw_detail_text or "",
            normalized_text=normalized.full_text,
        )
        entries = await self._keyword_registry.get_entries()
        result = await self._run_pipeline(normalized, entries)
        result = await self._llm_resolver.resolve(result)
        await self._repo.save_analysis_result(
            detection_id,
            status=result.status,
            analysis_version=result.analysis_version,
            is_relevant=result.is_relevant,
            confidence=result.confidence,
            summary=result.summary,
            explanation=result.explanation,
            decision_source=result.decision_source,
            needs_review=result.needs_review,
            matches=[
                {
                    "keyword_id": match.keyword_id,
                    "matched_text": match.matched_text,
                    "match_type": match.match_type,
                    "score": match.score,
                    "reason": match.reason,
                }
                for match in result.matches
            ],
        )
        return result

    async def _run_pipeline(self, normalized: NormalizedTenderText, entries) -> AnalysisResult:
        if not normalized.full_text:
            return AnalysisResult(
                is_relevant=False,
                confidence=0.0,
                explanation="Текст закупки пустой после нормализации.",
                needs_review=False,
                decision_source="rules",
                summary=None,
                status="failed",
                analysis_version=self._config.analysis_version,
            )

        rules_result = self._rules_matcher.match(
            normalized.full_text,
            entries,
            analysis_version=self._config.analysis_version,
        )
        if rules_result is not None:
            rules_result.summary = normalized.summary_text or rules_result.summary
            return rules_result

        semantic_result = await self._semantic_matcher.match(
            text=normalized.full_text,
            entries=entries,
            analysis_version=self._config.analysis_version,
        )
        if semantic_result is not None:
            semantic_result.summary = normalized.summary_text or semantic_result.summary
            return semantic_result

        return AnalysisResult(
            is_relevant=False,
            confidence=0.0,
            explanation="Совпадений по правилам и embeddings не найдено.",
            needs_review=False,
            decision_source="rules",
            summary=normalized.summary_text,
            status="completed",
            analysis_version=self._config.analysis_version,
        )
