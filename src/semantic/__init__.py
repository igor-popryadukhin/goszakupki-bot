from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable, Sequence

import numpy as np

try:  # pragma: no cover - import guard for optional dependency
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency for runtime, tests may stub it
    class SentenceTransformer:  # type: ignore[no-redef]
        def __init__(self, *_, **__):
            raise ImportError(
                "sentence-transformers is required for SemanticAnalyzer. Install with 'pip install sentence-transformers'."
            )

        def encode(self, *_args, **_kwargs):
            raise RuntimeError("sentence-transformers is not installed")

_MODEL_CACHE: dict[str, SentenceTransformer] = {}
_MODEL_LOCK = threading.Lock()
_ZERO_SHOT_CACHE: dict[str, object] = {}


@dataclass(slots=True)
class _MatchDetails:
    key: tuple[str, tuple[str, ...]]
    query: str | None
    score: float


class SemanticAnalyzer:
    """Wrapper around SentenceTransformer with simple caching and cosine scoring."""

    def __init__(
        self,
        model_name: str,
        *,
        models_path: str | Path | None = None,
        device: str | None = None,
        zero_shot_model: str | None = None,
        zero_shot_labels: Sequence[str] | None = None,
        zero_shot_threshold: float = 0.5,
        model_loader: Callable[[str, str | None], SentenceTransformer] | None = None,
    ) -> None:
        self._models_path = Path(models_path) if models_path else None
        self._model_name = model_name
        self._device = device
        self._zero_shot_model = zero_shot_model
        self._zero_shot_threshold = float(zero_shot_threshold)
        self._zero_shot_labels = tuple(zero_shot_labels or ("procurement of equipment", "other"))
        self._query_cache: dict[str, np.ndarray] = {}
        self._details_lock = threading.Lock()
        self._last_details: _MatchDetails | None = None
        self._model_loader = model_loader or self._load_model_default
        self._model = self._get_or_load_model(self._model_name)

    # --- Public API -----------------------------------------------------

    def encode(self, text: str) -> np.ndarray:
        """Return a normalized embedding for *text*."""

        cleaned = (text or "").strip()
        if not cleaned:
            return np.zeros(1, dtype=np.float32)
        embedding = self._model.encode(
            cleaned,
            device=self._device,
            convert_to_numpy=True,
            normalize_embeddings=False,
        )
        vector = np.array(embedding, dtype=np.float32).ravel()
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return np.zeros_like(vector)
        return vector / norm

    def is_relevant(self, text: str, queries: Sequence[str], threshold: float) -> tuple[bool, float]:
        normalized_queries = tuple(q.strip() for q in queries if q and q.strip())
        best_query, score = self._score(text, normalized_queries)
        with self._details_lock:
            self._last_details = _MatchDetails(key=(text, normalized_queries), query=best_query, score=score)
        return (score >= float(threshold), score)

    def explain_last_match(self, text: str, queries: Sequence[str]) -> tuple[str | None, float] | None:
        normalized_queries = tuple(q.strip() for q in queries if q and q.strip())
        key = (text, normalized_queries)
        with self._details_lock:
            details = self._last_details
        if details and details.key == key:
            return details.query, details.score
        return None

    def most_similar(self, text: str, queries: Sequence[str]) -> tuple[str | None, float]:
        normalized_queries = tuple(q.strip() for q in queries if q and q.strip())
        return self._score(text, normalized_queries)

    def is_procurement_fact(self, text: str) -> tuple[bool, float]:
        if not self._zero_shot_model:
            return False, 0.0
        pipeline = self._get_zero_shot_pipeline()
        if pipeline is None:
            return False, 0.0
        labels = list(self._zero_shot_labels)
        result = pipeline(text, candidate_labels=labels, multi_label=False)
        scores = {label: float(score) for label, score in zip(result["labels"], result["scores"])}
        positive_label = labels[0]
        score = scores.get(positive_label, 0.0)
        return score >= self._zero_shot_threshold, score

    # --- Internal helpers ------------------------------------------------

    def _score(self, text: str, queries: Sequence[str]) -> tuple[str | None, float]:
        cleaned_text = (text or "").strip()
        if not cleaned_text or not queries:
            return None, 0.0
        text_vector = self.encode(cleaned_text)
        if not np.any(text_vector):
            return None, 0.0
        best_query: str | None = None
        best_score = -1.0
        for query in queries:
            vector = self._query_cache.get(query)
            if vector is None:
                vector = self.encode(query)
                self._query_cache[query] = vector
            score = float(np.dot(text_vector, vector))
            if score > best_score:
                best_score = score
                best_query = query
        if best_score < 0.0:
            return None, 0.0
        return best_query, best_score

    def _resolve_model_path(self, model_name: str) -> str:
        if self._models_path:
            candidate = self._models_path / model_name
            if candidate.exists():
                return str(candidate)
        return model_name

    def _get_or_load_model(self, model_name: str) -> SentenceTransformer:
        resolved = self._resolve_model_path(model_name)
        with _MODEL_LOCK:
            model = _MODEL_CACHE.get(resolved)
            if model is None:
                model = self._model_loader(resolved, self._device)
                _MODEL_CACHE[resolved] = model
        return model

    @staticmethod
    def _load_model_default(model_name: str, device: str | None) -> SentenceTransformer:
        return SentenceTransformer(model_name, device=device)

    def _get_zero_shot_pipeline(self) -> Callable[[str, Sequence[str], bool], dict] | None:
        if not self._zero_shot_model:
            return None
        try:
            from transformers import pipeline  # type: ignore
        except ImportError:  # pragma: no cover - optional dependency
            return None
        resolved = self._resolve_model_path(self._zero_shot_model)
        with _MODEL_LOCK:
            classifier = _ZERO_SHOT_CACHE.get(resolved)
            if classifier is None:
                device_index = -1
                if self._device and self._device not in {"cpu", "auto"}:
                    device_index = 0
                classifier = pipeline(
                    "zero-shot-classification",
                    model=resolved,
                    tokenizer=resolved,
                    device=device_index,
                )
                _ZERO_SHOT_CACHE[resolved] = classifier
        return classifier  # type: ignore[return-value]

