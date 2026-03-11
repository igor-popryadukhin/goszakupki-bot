from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot

from ..config import ProviderConfig
from ..db.repo import Repository, AppPreferences
from ..provider.base import SourceProvider
from .classification import ClassificationError, ClassificationResult, ProcurementClassifier
from .match import Keyword, compile_keywords

LOGGER = logging.getLogger(__name__)


class DetailScanService:
    def __init__(
        self,
        *,
        provider: SourceProvider,
        repository: Repository,
        bot: Bot,
        provider_config: ProviderConfig,
        auth_state: "AuthState",
        classifier: ProcurementClassifier | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._bot = bot
        self._config = provider_config
        self._lock = asyncio.Lock()
        self._auth_state = auth_state
        self._classifier = classifier

    async def run_scan(self) -> None:
        async with self._lock:
            try:
                await self._run_scan()
            except Exception:
                LOGGER.exception("Error during detail scan")

    async def _run_scan(self) -> None:
        item = await self._repo.get_next_pending_detail()
        if not item:
            remaining = await self._repo.count_pending_detail()
            LOGGER.info("Detail scan tick", extra={"pulled": 0, "remaining": remaining})
            return
        prefs = await self._repo.get_preferences()
        keywords = compile_keywords(prefs.keywords) if (prefs and prefs.enabled) else []
        await self._process_item(item, prefs, keywords)
        remaining = await self._repo.count_pending_detail()
        LOGGER.info("Detail scan tick", extra={"pulled": 1, "remaining": remaining})

    async def _process_item(
        self,
        item: Repository.PendingDetail,
        prefs: AppPreferences | None,
        keywords: list[Keyword],
    ) -> None:
        text = ""
        try:
            fetch_detail = getattr(self._provider, "fetch_detail_text", None)
            if fetch_detail is None:
                LOGGER.warning("Provider has no fetch_detail_text; skipping detail scan")
                await self._repo.complete_detail_scan(item.id)
                return
            text = await fetch_detail(item.url)
            if text:
                await self._repo.mark_detail_loaded(item.id, True)
            else:
                await self._handle_retry(item)
                return
        except Exception:
            LOGGER.exception("Detail fetch failed", extra={"url": item.url})
            await self._handle_retry(item)
            return

        if self._classifier is None:
            LOGGER.warning("Classifier is not configured")
            await self._handle_retry(item)
            return

        try:
            normalized_text, result = await self._classifier.classify(
                detection_id=item.id,
                title=item.title,
                detail_text=text,
                keywords=keywords,
                procedure_type=item.procedure_type,
                status=item.status,
                deadline=item.deadline,
                price=item.price,
            )
        except ClassificationError as exc:
            LOGGER.warning("Classification failed", extra={"detection_id": item.id, "error": str(exc)})
            await self._repo.save_detection_classification(
                detection_id=item.id,
                normalized_text=text,
                status="error",
                topic_id=None,
                subtopic_id=None,
                confidence=None,
                decision_source=None,
                summary=None,
                reasoning=None,
                keyword_matches=[],
                matched_features=[],
                candidate_topics=[],
                raw_llm_response=None,
                classification_error=str(exc),
            )
            await self._handle_retry(item)
            return
        except Exception:
            LOGGER.exception("Unexpected classifier error", extra={"detection_id": item.id})
            await self._handle_retry(item)
            return

        await self._repo.save_detection_classification(
            detection_id=item.id,
            normalized_text=normalized_text,
            status="classified",
            topic_id=result.topic_id,
            subtopic_id=result.subtopic_id,
            confidence=result.confidence,
            decision_source=result.decision_source,
            summary=result.summary,
            reasoning=result.reasoning,
            keyword_matches=result.keyword_matches,
            matched_features=result.matched_features,
            candidate_topics=result.candidate_topics,
            raw_llm_response=result.raw_llm_response,
            classification_error=None,
        )

        notified = 0
        if prefs and prefs.enabled and result.is_keyword_relevant:
            if not await self._repo.has_notification_global_sent(self._config.source_id, item.external_id):
                message = self._format_message(item, result)
                targets_getter = getattr(self._auth_state, "all_targets", None)
                if callable(targets_getter):
                    targets = list(targets_getter())
                else:
                    targets = getattr(self._auth_state, "authorized_targets", lambda: [])()
                for chat_id in targets:
                    try:
                        await self._bot.send_message(chat_id=chat_id, text=message, disable_web_page_preview=False)
                        notified += 1
                    except Exception:
                        LOGGER.exception("Failed to send detail notification", extra={"chat_id": chat_id})
                if notified > 0:
                    await self._repo.create_notification_global(self._config.source_id, item.external_id, sent=True)

        LOGGER.debug(
            "Detail processed",
            extra={
                "id": item.id,
                "loaded": bool(text),
                "notified": notified,
                "keyword_relevant": result.is_keyword_relevant,
            },
        )
        await self._repo.complete_detail_scan(item.id)

    async def _handle_retry(self, item: Repository.PendingDetail) -> None:
        cfg = self._config.detail
        attempt_next = (item.retry_count or 0) + 1
        if attempt_next > max(cfg.max_retries, 0):
            LOGGER.warning(
                "Detail retries exhausted",
                extra={"id": item.id, "external_id": item.external_id, "retries": attempt_next - 1},
            )
            await self._repo.complete_detail_scan(item.id)
            return
        delay = cfg.backoff_base_seconds * (cfg.backoff_factor ** (attempt_next - 1))
        delay = max(1.0, min(delay, cfg.backoff_max_seconds))
        next_retry_at = datetime.utcnow() + timedelta(seconds=int(delay))
        new_count = await self._repo.schedule_detail_retry(item.id, next_retry_at)
        LOGGER.info(
            "Detail scheduled for retry",
            extra={
                "id": item.id,
                "external_id": item.external_id,
                "retry_count": new_count,
                "next_retry_at": next_retry_at.isoformat(),
            },
        )

    def _format_message(self, item: Repository.PendingDetail, result: ClassificationResult) -> str:
        title = item.title or "Без названия"
        lines = [
            f"🔎 Релевантная закупка ({self._config.source_id})",
            f"Название: {title}",
            f"Ссылка: {item.url}",
            f"Номер: {item.external_id}",
        ]
        if result.summary:
            lines.append(f"Суть: {' '.join(result.summary.split())}")
        if result.topic_code:
            topic_line = result.topic_code
            if result.subtopic_code:
                topic_line = f"{topic_line} / {result.subtopic_code}"
            lines.append(f"Классификация: {topic_line}")
        if result.keyword_matches:
            lines.append(f"Ключевые слова: {', '.join(result.keyword_matches[:6])}")
        if result.reasoning:
            reasoning = " ".join(result.reasoning.split())
            if len(reasoning) > 220:
                reasoning = reasoning[:217] + "..."
            lines.append(f"Почему релевантно: {reasoning}")
        return "\n".join(lines)
