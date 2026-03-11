from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

_SPACE_RE = re.compile(r"\s+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(slots=True)
class TenderTextPayload:
    title: str | None
    procedure_type: str | None = None
    status: str | None = None
    deadline: str | None = None
    price: str | None = None
    raw_detail_text: str | None = None


@dataclass(slots=True)
class NormalizedTenderText:
    full_text: str
    summary_text: str


class TextNormalizer:
    def normalize(self, payload: TenderTextPayload) -> NormalizedTenderText:
        parts = [
            payload.title or "",
            payload.procedure_type or "",
            payload.status or "",
            payload.deadline or "",
            payload.price or "",
            payload.raw_detail_text or "",
        ]
        cleaned_parts = [self._normalize_chunk(part) for part in parts if part and part.strip()]
        full_text = " ".join(part for part in cleaned_parts if part).strip()
        summary_text = full_text[:500]
        return NormalizedTenderText(full_text=full_text, summary_text=summary_text)

    def _normalize_chunk(self, text: str) -> str:
        without_comments = _HTML_COMMENT_RE.sub(" ", text)
        soup = BeautifulSoup(without_comments, "lxml")
        for tag in soup.find_all(["script", "style", "noscript", "template"]):
            tag.decompose()
        normalized = soup.get_text(" ", strip=True)
        normalized = normalized.lower()
        normalized = _SPACE_RE.sub(" ", normalized)
        return normalized.strip()
