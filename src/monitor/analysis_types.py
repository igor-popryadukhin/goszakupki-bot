from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AnalysisDecisionSource = Literal["rules", "semantic", "llm"]
AnalysisMatchType = Literal["lexical", "semantic"]
AnalysisStatus = Literal["pending", "completed", "failed", "needs_review"]


@dataclass(slots=True)
class KeywordMatch:
    keyword_id: int | None
    keyword: str
    matched_text: str
    match_type: AnalysisMatchType
    score: float | None
    reason: str


@dataclass(slots=True)
class AnalysisResult:
    is_relevant: bool
    confidence: float
    matches: list[KeywordMatch] = field(default_factory=list)
    explanation: str = ""
    needs_review: bool = False
    decision_source: AnalysisDecisionSource = "rules"
    summary: str | None = None
    status: AnalysisStatus = "completed"
    analysis_version: int = 1
