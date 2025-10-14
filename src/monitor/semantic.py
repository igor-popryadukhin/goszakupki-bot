from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

import aiohttp

from ..config import DeepSeekConfig

LOGGER = logging.getLogger(__name__)


def _normalize_keyword(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


@dataclass(slots=True)
class SemanticMatch:
    keyword: str
    score: float
    reason: str


@dataclass(slots=True)
class SemanticAnalysis:
    summary: str
    matches: list[SemanticMatch]
    submission_deadline: str | None = None


class SemanticMatcher:
    async def match_keywords(self, text: str, keywords: Sequence[str]) -> SemanticAnalysis | None:  # pragma: no cover - interface
        raise NotImplementedError


class DeepSeekSemanticAnalyzer(SemanticMatcher):
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def match_keywords(self, text: str, keywords: Sequence[str]) -> SemanticAnalysis | None:
        if not self._config.enabled:
            return None
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return None

        unique_keywords: list[str] = []
        seen: set[str] = set()
        for raw in keywords:
            candidate = (raw or "").strip()
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_keywords.append(candidate)
            if len(unique_keywords) >= max(self._config.max_keywords, 1):
                break

        if not unique_keywords:
            return None

        if len(cleaned_text) > self._config.max_chars > 0:
            cleaned_text = cleaned_text[: self._config.max_chars]

        payload = self._build_payload(cleaned_text, unique_keywords)
        session = await self._ensure_session()

        try:
            async with session.post(self._config.api_url, json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "DeepSeek API returned non-200 status",
                        extra={"status": response.status, "body": body[:500]},
                    )
                    return None
                data = await response.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to call DeepSeek API")
            return None

        return self._parse_response(data, unique_keywords)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None:
                if not self._config.api_key:
                    raise RuntimeError("DeepSeek API key is not configured")
                timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
                headers = {
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                }
                self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
            return self._session

    def _build_payload(self, text: str, keywords: Sequence[str]) -> dict[str, Any]:
        formatted_keywords = "\n".join(f"- {kw}" for kw in keywords)
        user_prompt = (
            "Текст закупки приведён ниже между тройными кавычками. "
            "Затем перечислены ключевые слова. Сначала в одном коротком предложении (до 25 слов) сформулируй суть закупки. "
            "Затем определи, какие ключевые слова действительно отражают содержание текста, даже если формулировки отличаются. "
            "Оцени семантическую близость, а не только дословные совпадения."
            "\n\n" "\"\"\"\n"
            f"{text}\n"
            "\"\"\"\n\n"
            "Ключевые слова:\n"
            f"{formatted_keywords}\n\n"
            "Верни JSON вида {\"summary\": \"...\", \"matches\": [{\"keyword\": \"...\", \"score\": 0.0-1.0, \"reason\": \"...\"}], \"submission_deadline\": \"...\"|null}. "
            "summary — это краткое описание сути закупки на русском языке. reason — короткое (до 20 слов) объяснение, почему слово подходит."
            "submission_deadline — дата и время прекращения приёма сведений в формате ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ. Если в тексте нет информации о прекращении приёма сведений, используй null."
            "Используй только ключевые слова из списка. Если совпадений нет, верни {\"matches\": []}."
        )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты ассистент, который помогает определять соответствие ключевых слов тексту закупки. "
                        "Отвечай строго в формате JSON и не добавляй пояснений вне структуры. "
                        "Пиши кратко и по-русски."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        return payload

    def _parse_response(self, data: dict[str, Any], keywords: Sequence[str]) -> SemanticAnalysis | None:
        choices = data.get("choices")
        if not choices:
            LOGGER.warning("DeepSeek response has no choices", extra={"data": data})
            return None
        message = choices[0].get("message")
        if not isinstance(message, dict):
            LOGGER.warning("DeepSeek response message malformed", extra={"message": message})
            return None
        content = message.get("content")
        if not isinstance(content, str):
            LOGGER.warning("DeepSeek content is missing or not a string", extra={"content": content})
            return None

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            LOGGER.warning("Failed to decode DeepSeek JSON response", extra={"content": content})
            return None

        matches = parsed.get("matches")
        if not isinstance(matches, list):
            matches = []

        summary = parsed.get("summary")
        if not isinstance(summary, str):
            summary = ""
        summary = summary.strip()

        raw_deadline = parsed.get("submission_deadline")
        if isinstance(raw_deadline, str):
            submission_deadline = raw_deadline.strip() or None
        else:
            submission_deadline = None

        normalized_pairs = [(kw, _normalize_keyword(kw)) for kw in keywords]
        normalized = {norm: kw for kw, norm in normalized_pairs if norm}
        min_score = max(min(self._config.min_score, 1.0), 0.0)
        result: list[SemanticMatch] = []
        for entry in matches:
            if isinstance(entry, str):
                candidate = entry
                score = 1.0
                reason = ""
            elif isinstance(entry, dict):
                candidate = (entry.get("keyword") or entry.get("key") or "").strip()
                try:
                    score = float(entry.get("score", 0.0))
                except (TypeError, ValueError):
                    score = 0.0
                reason = str(entry.get("reason") or entry.get("explanation") or "").strip()
            else:
                continue
            norm_candidate = _normalize_keyword(candidate)
            original = normalized.get(norm_candidate)
            if not original and norm_candidate:
                for kw, norm in normalized_pairs:
                    if not norm:
                        continue
                    if norm in norm_candidate or norm_candidate in norm:
                        original = kw
                        break
            if not original and norm_candidate:
                best_ratio = 0.0
                best_keyword: str | None = None
                for kw, norm in normalized_pairs:
                    if not norm:
                        continue
                    ratio = SequenceMatcher(None, norm_candidate, norm).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_keyword = kw
                if best_ratio >= 0.75 and best_keyword:
                    original = best_keyword
            if not original:
                LOGGER.debug(
                    "DeepSeek keyword not mapped", extra={"candidate": candidate, "keywords": keywords}
                )
                continue
            if score < min_score:
                continue
            if any(match.keyword.casefold() == original.casefold() for match in result):
                continue
            if not reason:
                reason = "Совпадение по смыслу"
            result.append(SemanticMatch(keyword=original, score=score, reason=reason))

        if result:
            LOGGER.debug(
                "DeepSeek matched keywords",
                extra={"keywords": [match.keyword for match in result], "summary": summary},
            )

        if not result and not summary and not submission_deadline:
            return None

        return SemanticAnalysis(
            summary=summary,
            matches=result,
            submission_deadline=submission_deadline,
        )

