from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

import aiohttp

from ..config import DeepSeekConfig

LOGGER = logging.getLogger(__name__)


class SemanticMatcher:
    async def match_keywords(self, text: str, keywords: Sequence[str]) -> list[str]:  # pragma: no cover - interface
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

    async def match_keywords(self, text: str, keywords: Sequence[str]) -> list[str]:
        if not self._config.enabled:
            return []
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return []

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
            return []

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
                    return []
                data = await response.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to call DeepSeek API")
            return []

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
            "Затем перечислены ключевые слова. Определи, какие ключевые слова действительно отражают содержание текста, "
            "даже если формулировки отличаются. Оцени семантическую близость, а не только дословные совпадения."
            "\n\n" "\"\"\"\n"
            f"{text}\n"
            "\"\"\"\n\n"
            "Ключевые слова:\n"
            f"{formatted_keywords}\n\n"
            "Верни только JSON вида {\"matches\": [{\"keyword\": \"...\", \"score\": 0.0-1.0, \"reason\": \"...\"}]}."
            "Используй только ключевые слова из списка. Если совпадений нет, верни {\"matches\": []}."
        )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты ассистент, который помогает определять соответствие ключевых слов тексту закупки. "
                        "Отвечай строго в формате JSON и не добавляй пояснений вне структуры."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        return payload

    def _parse_response(self, data: dict[str, Any], keywords: Sequence[str]) -> list[str]:
        choices = data.get("choices")
        if not choices:
            LOGGER.warning("DeepSeek response has no choices", extra={"data": data})
            return []
        message = choices[0].get("message")
        if not isinstance(message, dict):
            LOGGER.warning("DeepSeek response message malformed", extra={"message": message})
            return []
        content = message.get("content")
        if not isinstance(content, str):
            LOGGER.warning("DeepSeek content is missing or not a string", extra={"content": content})
            return []

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            LOGGER.warning("Failed to decode DeepSeek JSON response", extra={"content": content})
            return []

        matches = parsed.get("matches")
        if not isinstance(matches, list):
            return []

        normalized = {kw.casefold(): kw for kw in keywords}
        min_score = max(min(self._config.min_score, 1.0), 0.0)
        result: list[str] = []
        for entry in matches:
            if isinstance(entry, str):
                candidate = entry
                score = 1.0
            elif isinstance(entry, dict):
                candidate = (entry.get("keyword") or entry.get("key") or "").strip()
                try:
                    score = float(entry.get("score", 0.0))
                except (TypeError, ValueError):
                    score = 0.0
            else:
                continue
            key = candidate.casefold()
            original = normalized.get(key)
            if not original:
                continue
            if score < min_score:
                continue
            if original not in result:
                result.append(original)

        if result:
            LOGGER.debug("DeepSeek matched keywords", extra={"keywords": result})
        return result

