from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any

import aiohttp

from ..config import DeepSeekConfig

LOGGER = logging.getLogger(__name__)


class DeepSeekJokeGenerator:
    def __init__(self, config: DeepSeekConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def generate_joke(self) -> str | None:
        if not self._config.enabled or not self._config.api_key:
            return None
        payload = self._build_payload()
        session = await self._ensure_session()

        try:
            async with session.post(self._config.api_url, json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    LOGGER.warning(
                        "DeepSeek joke API returned non-200 status",
                        extra={"status": response.status, "body": body[:500]},
                    )
                    return None
                data = await response.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to call DeepSeek joke API")
            return None

        return self._parse_response(data)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None:
                timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
                headers = {
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                }
                self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
            return self._session

    def _build_payload(self) -> dict[str, Any]:
        theme = random.choice(
            [
                "работа",
                "дедлайны",
                "почта",
                "таблички",
                "созвоны",
                "офисные привычки",
                "чай/кофе",
                "заметки",
                "планирование",
                "ежедневники",
                "перерывы",
                "переписки в чате",
                "утренние ритуалы",
                "вечерние итоги",
                "микрозадачи",
                "встречи без повестки",
                "переименованные файлы",
                "настройки и пароли",
                "снеки на столе",
                "домашний офис",
                "офисные мемы",
                "многозадачность",
                "срочные просьбы",
                "стикеры",
            ]
        )
        nonce = uuid.uuid4().hex
        user_prompt = (
            "Сгенерируй короткую шутку на русском языке в 1-2 предложения. "
            "Тон дружелюбный, как от живого человека. "
            "Без упоминаний ИИ, моделей или генерации. "
            "Без токсичности, политики, религии и грубостей. "
            "Шутка должна быть новой и отличаться от прошлых. "
            f"Тематика: {theme}. "
            f"Скрытый код для разнообразия: {nonce}. "
            "Ответ верни в JSON вида {\"joke\": \"...\"}."
        )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты помощник, который пишет короткие шутки. "
                        "Отвечай строго в формате JSON и без лишних комментариев."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.9,
            "response_format": {"type": "json_object"},
        }
        return payload

    def _parse_response(self, data: dict[str, Any]) -> str | None:
        choices = data.get("choices")
        if not choices:
            LOGGER.warning("DeepSeek joke response has no choices", extra={"data": data})
            return None
        message = choices[0].get("message")
        if not isinstance(message, dict):
            LOGGER.warning("DeepSeek joke message malformed", extra={"message": message})
            return None
        content = message.get("content")
        if not isinstance(content, str):
            LOGGER.warning("DeepSeek joke content missing", extra={"content": content})
            return None

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            LOGGER.warning("Failed to decode DeepSeek joke JSON", extra={"content": content})
            return None

        joke = parsed.get("joke")
        if not isinstance(joke, str):
            return None
        joke = " ".join(joke.split())
        return joke or None
