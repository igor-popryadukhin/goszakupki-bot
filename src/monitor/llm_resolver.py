from __future__ import annotations

from .analysis_types import AnalysisResult


class LlmResolver:
    async def resolve(self, result: AnalysisResult) -> AnalysisResult:
        return result
