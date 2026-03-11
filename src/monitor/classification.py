from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable

from ..config import OllamaConfig
from ..db.repo import Repository
from .match import Keyword, find_matching_keywords
from .ollama_client import OllamaClient
from .text_normalization import normalize_procurement_text


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _clip_text(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit]


@dataclass(slots=True)
class TopicCandidate:
    topic_id: int
    code: str
    name: str
    parent_id: int | None
    rule_score: float
    embedding_score: float
    final_score: float
    matched_features: list[str]


@dataclass(slots=True)
class ClassificationResult:
    topic_id: int | None
    subtopic_id: int | None
    topic_code: str | None
    subtopic_code: str | None
    confidence: float
    decision_source: str
    summary: str
    reasoning: str
    matched_features: list[str]
    candidate_topics: list[dict[str, Any]]
    keyword_matches: list[str]
    is_keyword_relevant: bool
    raw_llm_response: str | None = None


class ClassificationError(RuntimeError):
    pass


class ProcurementClassifier:
    def __init__(self, *, repository: Repository, ollama_client: OllamaClient, config: OllamaConfig) -> None:
        self._repo = repository
        self._ollama = ollama_client
        self._config = config

    async def close(self) -> None:
        await self._ollama.close()

    async def classify(
        self,
        *,
        detection_id: int,
        title: str | None,
        detail_text: str,
        keywords: Iterable[Keyword],
        procedure_type: str | None = None,
        status: str | None = None,
        deadline: str | None = None,
        price: str | None = None,
    ) -> tuple[str, ClassificationResult]:
        normalized_text = normalize_procurement_text(
            title,
            detail_text,
            procedure_type,
            status,
            deadline,
            price,
        )
        if not normalized_text:
            raise ClassificationError("Normalized text is empty")
        normalized_text = _clip_text(normalized_text, self._config.max_chars)
        topics = await self._repo.list_active_topic_profiles()
        if not topics:
            raise ClassificationError("Topic catalog is empty")

        text_embedding = await self._get_cached_embedding(
            cache_key=f"text:{self._config.embedding_model}:{hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()}",
            source_type="detection_text",
            source_ref=str(detection_id),
            text=normalized_text,
        )

        candidates: list[TopicCandidate] = []
        for topic in topics:
            matched_features: list[str] = []
            rule_score = 0.0
            for feature in topic.keywords:
                if feature.casefold() in normalized_text:
                    rule_score += 0.18
                    matched_features.append(feature)
            for feature in topic.synonyms:
                if feature.casefold() in normalized_text:
                    rule_score += 0.12
                    matched_features.append(feature)
            for feature in topic.negative_keywords:
                if feature.casefold() in normalized_text:
                    rule_score -= 0.25
                    matched_features.append(f"!{feature}")
            topic_embedding = await self._get_cached_embedding(
                cache_key=f"topic:{self._config.embedding_model}:{topic.code}",
                source_type="topic_profile",
                source_ref=topic.code,
                text=topic.embedding_text,
            )
            embedding_score = _cosine_similarity(text_embedding, topic_embedding)
            final_score = max(0.0, min(1.0, embedding_score + rule_score))
            candidates.append(
                TopicCandidate(
                    topic_id=topic.id,
                    code=topic.code,
                    name=topic.name,
                    parent_id=topic.parent_id,
                    rule_score=max(rule_score, 0.0),
                    embedding_score=embedding_score,
                    final_score=final_score,
                    matched_features=sorted(set(matched_features)),
                )
            )

        candidates.sort(key=lambda item: item.final_score, reverse=True)
        top_candidates = candidates[: max(self._config.top_k_candidates, 1)]
        summary = self._build_summary(title=title, detail_text=detail_text, candidates=top_candidates)
        chosen = self._choose_candidate(top_candidates)
        raw_llm_response: str | None = None
        decision_source = "rules+embeddings"
        reasoning = ""
        if chosen is None:
            chosen, reasoning, raw_llm_response = await self._resolve_with_llm(normalized_text, top_candidates, summary)
            decision_source = "llm_resolver"
        else:
            reasoning = self._build_rules_reasoning(chosen)

        topic_choice = chosen
        subtopic_choice = chosen if chosen and chosen.parent_id is not None else None
        if subtopic_choice is not None:
            parent = next((item for item in candidates if item.topic_id == subtopic_choice.parent_id), None)
            topic_choice = parent or topic_choice

        compiled_keywords = list(keywords)
        keyword_matches = [item.raw for item in find_matching_keywords(normalized_text, compiled_keywords)]
        semantic_keyword_matches = await self._match_keywords_semantically(
            detection_id=detection_id,
            normalized_text=normalized_text,
            text_embedding=text_embedding,
            keywords=compiled_keywords,
        )
        all_keyword_matches = sorted(set(keyword_matches + semantic_keyword_matches))

        result = ClassificationResult(
            topic_id=topic_choice.topic_id if topic_choice else None,
            subtopic_id=subtopic_choice.topic_id if subtopic_choice else None,
            topic_code=topic_choice.code if topic_choice else None,
            subtopic_code=subtopic_choice.code if subtopic_choice else None,
            confidence=chosen.final_score if chosen else 0.0,
            decision_source=decision_source,
            summary=summary,
            reasoning=reasoning,
            matched_features=chosen.matched_features if chosen else [],
            candidate_topics=[
                {
                    "code": item.code,
                    "name": item.name,
                    "score": round(item.final_score, 4),
                    "rule_score": round(item.rule_score, 4),
                    "embedding_score": round(item.embedding_score, 4),
                }
                for item in top_candidates
            ],
            keyword_matches=all_keyword_matches,
            is_keyword_relevant=bool(all_keyword_matches),
            raw_llm_response=raw_llm_response,
        )
        return normalized_text, result

    def _build_summary(self, *, title: str | None, detail_text: str, candidates: list[TopicCandidate]) -> str:
        title_clean = " ".join((title or "").split()).strip()
        if title_clean:
            return title_clean[:220]
        first_line = " ".join(detail_text.split())
        if first_line:
            return first_line[:220]
        lead = candidates[0].name if candidates else "закупка"
        return f"Закупка относится к категории: {lead}."

    def _choose_candidate(self, candidates: list[TopicCandidate]) -> TopicCandidate | None:
        if not candidates:
            return None
        leader = candidates[0]
        runner_up_score = candidates[1].final_score if len(candidates) > 1 else 0.0
        if leader.final_score < self._config.confidence_threshold:
            return None
        if leader.final_score - runner_up_score < self._config.llm_trigger_margin:
            return None
        return leader

    async def _resolve_with_llm(
        self,
        normalized_text: str,
        candidates: list[TopicCandidate],
        summary: str,
    ) -> tuple[TopicCandidate, str, str]:
        if not candidates:
            raise ClassificationError("No candidates available for LLM resolver")
        schema = {
            "type": "object",
            "properties": {
                "selected_code": {"type": "string"},
                "reasoning": {"type": "string"},
            },
            "required": ["selected_code", "reasoning"],
        }
        candidate_lines = [
            f"- {item.code}: {item.name}; score={item.final_score:.3f}; признаки={', '.join(item.matched_features) or 'нет'}"
            for item in candidates
        ]
        prompt = (
            "Выбери наиболее подходящую тему закупки из списка кандидатов. "
            "Не придумывай новые коды. Учитывай текст закупки и краткое summary.\n\n"
            f"Summary: {summary}\n\n"
            f"Текст закупки:\n{normalized_text}\n\n"
            "Кандидаты:\n"
            f"{chr(10).join(candidate_lines)}\n\n"
            "Верни JSON с полями selected_code и reasoning."
        )
        parsed, raw = await self._ollama.structured_chat(
            prompt=prompt,
            schema=schema,
            system_prompt="Ты арбитр классификации закупок. Отвечай строго JSON-объектом.",
        )
        selected_code = str(parsed.get("selected_code") or "").strip()
        reasoning = str(parsed.get("reasoning") or "").strip()
        for candidate in candidates:
            if candidate.code == selected_code:
                boosted_score = max(candidate.final_score, self._config.confidence_threshold)
                candidate.final_score = min(1.0, boosted_score)
                return candidate, reasoning or "Выбрано LLM-арбитром", raw
        raise ClassificationError(f"LLM returned unknown topic code: {selected_code}")

    def _build_rules_reasoning(self, candidate: TopicCandidate) -> str:
        if candidate.matched_features:
            features = ", ".join(candidate.matched_features[:5])
            return f"Кандидат лидирует по совокупности признаков и embeddings; ключевые сигналы: {features}."
        return "Кандидат лидирует по embeddings и итоговому рейтингу."

    async def _match_keywords_semantically(
        self,
        *,
        detection_id: int,
        normalized_text: str,
        text_embedding: list[float],
        keywords: list[Keyword],
    ) -> list[str]:
        matches: list[str] = []
        for keyword in keywords:
            text = keyword.raw.strip()
            if not text:
                continue
            keyword_embedding = await self._get_cached_embedding(
                cache_key=f"keyword:{self._config.embedding_model}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
                source_type="keyword",
                source_ref=text,
                text=text,
            )
            similarity = _cosine_similarity(text_embedding, keyword_embedding)
            if similarity >= self._config.keyword_semantic_threshold:
                matches.append(keyword.raw)
            elif keyword.raw.casefold() in normalized_text:
                matches.append(keyword.raw)
        return matches

    async def _get_cached_embedding(
        self,
        *,
        cache_key: str,
        source_type: str,
        source_ref: str,
        text: str,
    ) -> list[float]:
        cached = await self._repo.get_embedding_cache(cache_key)
        if cached is not None:
            return cached
        vector = await self._ollama.embed(text)
        await self._repo.set_embedding_cache(
            cache_key=cache_key,
            source_type=source_type,
            source_ref=source_ref,
            model=self._config.embedding_model,
            vector=vector,
        )
        return vector
