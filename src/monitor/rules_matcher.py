from __future__ import annotations

import re

from .analysis_types import AnalysisResult, KeywordMatch
from .keyword_registry import KeywordEntryView

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-Я0-9]+")
_GENERIC_TOKENS = {
    "приобретение",
    "товаров",
    "товара",
    "товары",
    "закупка",
    "закупки",
    "услуги",
    "услуг",
    "работы",
    "работ",
    "поставка",
    "поставки",
    "покупка",
    "заявка",
    "заявки",
    "источника",
    "одноисточника",
}


class RulesMatcher:
    def match(self, text: str, entries: list[KeywordEntryView], *, analysis_version: int) -> AnalysisResult | None:
        normalized = (text or "").strip()
        if not normalized or not entries:
            return None

        negative_hits = [entry for entry in entries if any(marker and marker in normalized for marker in entry.negative_contexts)]
        if negative_hits:
            explanation = f"Найден отрицательный контекст: {negative_hits[0].source_phrase}"
            return AnalysisResult(
                is_relevant=False,
                confidence=0.05,
                explanation=explanation,
                needs_review=False,
                decision_source="rules",
                summary=None,
                status="completed",
                analysis_version=analysis_version,
            )

        matches: list[KeywordMatch] = []
        for entry in entries:
            alias_hit = next((alias for alias in entry.aliases if alias and alias in normalized), None)
            if alias_hit is None:
                continue
            if _is_generic_phrase(alias_hit):
                continue
            matches.append(
                KeywordMatch(
                    keyword_id=entry.id,
                    keyword=entry.source_phrase,
                    matched_text=alias_hit,
                    match_type="lexical",
                    score=min(0.99, 0.8 + max(entry.weight - 1.0, 0.0) * 0.05),
                    reason=f"Прямое совпадение по фразе: {entry.source_phrase}",
                )
            )

        if not matches:
            return None

        matches.sort(key=lambda item: (item.score or 0.0), reverse=True)
        return AnalysisResult(
            is_relevant=True,
            confidence=max(match.score or 0.0 for match in matches),
            matches=matches,
            explanation="Найдены прямые совпадения по ключевым фразам.",
            needs_review=False,
            decision_source="rules",
            summary=None,
            status="completed",
            analysis_version=analysis_version,
        )


def _is_generic_phrase(text: str) -> bool:
    tokens = [token.lower() for token in _TOKEN_RE.findall(text) if len(token) >= 4]
    if not tokens:
        return True
    informative_tokens = [token for token in tokens if token not in _GENERIC_TOKENS]
    return not informative_tokens
