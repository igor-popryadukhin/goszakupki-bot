from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class Keyword:
    pattern: re.Pattern[str]
    is_regex: bool
    raw: str

    def matches(self, text: str) -> bool:
        return bool(self.pattern.search(text))


def compile_keywords(items: Iterable[str]) -> list[Keyword]:
    compiled: list[Keyword] = []
    for raw in items:
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) >= 2 and raw.startswith("/") and raw.rfind("/") > 0:
            last_slash = raw.rfind("/")
            pattern = raw[1:last_slash]
            flags_segment = raw[last_slash + 1 :]
            flags = 0
            if "i" in flags_segment:
                flags |= re.IGNORECASE
            compiled.append(Keyword(pattern=re.compile(pattern, flags), is_regex=True, raw=raw))
        else:
            compiled.append(
                Keyword(pattern=re.compile(re.escape(raw), re.IGNORECASE), is_regex=False, raw=raw)
            )
    return compiled


def match_title(title: str | None, keywords: list[Keyword]) -> bool:
    if not title or not keywords:
        return False
    for keyword in keywords:
        if keyword.matches(title):
            return True
    return False


def match_text(text: str | None, keywords: list[Keyword]) -> bool:
    if not text or not keywords:
        return False
    for keyword in keywords:
        if keyword.matches(text):
            return True
    return False


def find_matching_keywords(text: str | None, keywords: list[Keyword]) -> list[Keyword]:
    if not text or not keywords:
        return []
    matched: list[Keyword] = []
    for kw in keywords:
        if kw.matches(text):
            matched.append(kw)
    return matched
