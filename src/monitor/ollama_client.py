from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from ..config import OllamaConfig


class OllamaClient:
    def __init__(self, config: OllamaConfig) -> None:
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def embed(self, text: str) -> list[float]:
        session = await self._ensure_session()
        payload: dict[str, Any] = {
            "model": self._config.embedding_model,
            "input": text,
        }
        async with session.post(self._config.embed_api_url, json=payload) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Ollama embed failed with {response.status}: {body[:500]}")
            data = json.loads(body)
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            raise RuntimeError("Ollama embed response does not contain embeddings")
        vector = embeddings[0]
        if not isinstance(vector, list):
            raise RuntimeError("Ollama embedding vector is malformed")
        return [float(item) for item in vector]

    async def structured_chat(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str,
    ) -> tuple[dict[str, Any], str]:
        session = await self._ensure_session()
        payload: dict[str, Any] = {
            "model": self._config.chat_model,
            "stream": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        }
        if self._config.request_options:
            payload["options"] = self._config.request_options
        async with session.post(self._config.chat_api_url, json=payload) as response:
            body = await response.text()
            if response.status != 200:
                raise RuntimeError(f"Ollama chat failed with {response.status}: {body[:500]}")
            data = json.loads(body)
        message = data.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama chat response has no message object")
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Ollama chat content is missing")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ollama chat returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Ollama chat JSON must be an object")
        return parsed, content

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None:
                timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session
