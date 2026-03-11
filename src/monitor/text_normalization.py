from __future__ import annotations

import re
from html import unescape


WHITESPACE_RE = re.compile(r"\s+")
HTML_TAG_RE = re.compile(r"<[^>]+>")
NOISE_RE = re.compile(r"[^0-9a-zа-яёіїў\-_/.,:;()%\s]+", re.IGNORECASE)


def normalize_procurement_text(*parts: str | None) -> str:
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        text = unescape(part)
        text = HTML_TAG_RE.sub(" ", text)
        text = text.replace("\xa0", " ").replace("№", " номер ")
        text = text.casefold()
        text = text.replace("ё", "е")
        text = text.replace("і", "и")
        text = text.replace("ў", "у")
        text = NOISE_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text).strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)
