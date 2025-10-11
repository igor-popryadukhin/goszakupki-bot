from __future__ import annotations

import numpy as np
import pytest

from src.semantic import SemanticAnalyzer


class DummyModel:
    def __init__(self, vectors: dict[str, np.ndarray]) -> None:
        self._vectors = vectors
        self.calls: list[str] = []

    def encode(self, sentences, **kwargs):  # type: ignore[override]
        if isinstance(sentences, str):
            self.calls.append(sentences)
            return self._vectors.get(sentences, np.zeros(3, dtype=np.float32))
        outputs = []
        for sentence in sentences:
            self.calls.append(sentence)
            outputs.append(self._vectors.get(sentence, np.zeros(3, dtype=np.float32)))
        return np.array(outputs)


@pytest.fixture()
def analyzer_and_model() -> tuple[SemanticAnalyzer, DummyModel]:
    vectors = {
        "поставка серверного оборудования": np.array([1.0, 0.0, 0.0], dtype=np.float32),
        "закупка оборудования": np.array([0.9, 0.1, 0.0], dtype=np.float32),
        "ремонт": np.array([0.0, 1.0, 0.0], dtype=np.float32),
        "случайный текст": np.array([0.1, 0.9, 0.0], dtype=np.float32),
    }
    dummy = DummyModel(vectors)
    model_name = f"dummy-{id(dummy)}"
    analyzer = SemanticAnalyzer(
        model_name,
        model_loader=lambda name, device=None: dummy,
    )
    return analyzer, dummy


def test_encode_normalizes(analyzer_and_model: tuple[SemanticAnalyzer, DummyModel]) -> None:
    analyzer, _ = analyzer_and_model
    vec = analyzer.encode("поставка серверного оборудования")
    assert pytest.approx(np.linalg.norm(vec), rel=1e-6) == 1.0


def test_is_relevant_matches_best_query(analyzer_and_model: tuple[SemanticAnalyzer, DummyModel]) -> None:
    analyzer, _ = analyzer_and_model
    text = "поставка серверного оборудования"
    queries = ["закупка оборудования", "ремонт"]
    relevant, score = analyzer.is_relevant(text, queries, 0.5)
    assert relevant is True
    assert score > 0.9
    query, detail_score = analyzer.explain_last_match(text, queries) or (None, None)
    assert query == "закупка оборудования"
    assert detail_score is not None and detail_score == pytest.approx(score)


def test_query_embeddings_cached(analyzer_and_model: tuple[SemanticAnalyzer, DummyModel]) -> None:
    analyzer, dummy = analyzer_and_model
    text = "поставка серверного оборудования"
    query = ["закупка оборудования"]
    analyzer.is_relevant(text, query, 0.5)
    analyzer.is_relevant(text, query, 0.5)
    assert dummy.calls.count("закупка оборудования") == 1
    assert dummy.calls.count(text) == 2
