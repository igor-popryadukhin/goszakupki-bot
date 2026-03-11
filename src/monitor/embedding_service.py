from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import aiohttp

from ..config import OllamaConfig
from ..db.repo import Repository

LOGGER = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, *, config: OllamaConfig, repository: Repository) -> None:
        self._config = config
        self._repo = repository
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(config.max_concurrency, 1))
        self._active_model = config.embedding_model

    async def close(self) -> None:
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None

    async def embed_text(self, text: str) -> list[float]:
        normalized = (text or "").strip()
        if not normalized:
            return []
        model = await self.get_active_model()
        text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        cache_key = f"{model}:{text_hash}"
        cached = await self._repo.get_embedding_cache(cache_key=cache_key)
        if cached is not None and cached.vector:
            return cached.vector

        session = await self._ensure_session()
        payload = {"model": model, "input": normalized}
        async with self._semaphore:
            async with session.post(self._config.embeddings_api_url, json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"Ollama embeddings API returned {response.status}: {body[:300]}")
                data = await response.json()
        vector = self._extract_vector(data)
        await self._repo.upsert_embedding_cache(
            cache_key=cache_key,
            model=model,
            text_hash=text_hash,
            text_preview=normalized[:250],
            vector=vector,
        )
        return vector

    async def list_models(self) -> list[str]:
        session = await self._ensure_session()
        async with self._semaphore:
            async with session.get(f"{self._config.base_url}/api/tags") as response:
                if response.status != 200:
                    body = await response.text()
                    raise RuntimeError(f"Ollama tags API returned {response.status}: {body[:300]}")
                data = await response.json()
        models = data.get("models")
        if not isinstance(models, list):
            return []
        result: list[str] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                result.append(name)
        result.sort()
        return result

    async def get_active_model(self) -> str:
        prefs = await self._repo.get_preferences()
        configured = (prefs.embedding_model if prefs else None) or self._active_model or self._config.embedding_model
        self._active_model = configured
        return configured

    async def set_active_model(self, model: str) -> None:
        model_name = model.strip()
        if not model_name:
            raise ValueError("Embedding model name is empty")
        self._active_model = model_name

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None:
                timeout = aiohttp.ClientTimeout(total=self._config.timeout_seconds)
                self._session = aiohttp.ClientSession(timeout=timeout)
            return self._session

    @staticmethod
    def _extract_vector(data: dict[str, Any]) -> list[float]:
        raw_vector = data.get("embedding")
        if raw_vector is None:
            embeddings = data.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                raw_vector = embeddings[0]
        if not isinstance(raw_vector, list):
            LOGGER.warning("Ollama embedding payload missing vector", extra={"payload": data})
            return []
        result: list[float] = []
        for item in raw_vector:
            try:
                result.append(float(item))
            except (TypeError, ValueError):
                continue
        return result
