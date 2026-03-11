from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot

from ..config import ProviderConfig
from ..db.repo import AppPreferences, Repository
from ..provider.base import SourceProvider
from .analysis_pipeline import AnalysisPipeline
from .analysis_types import AnalysisResult, KeywordMatch
from .text_normalizer import TenderTextPayload

LOGGER = logging.getLogger(__name__)


class DetailScanService:
    @dataclass(slots=True)
    class ProviderEntry:
        provider: SourceProvider
        config: ProviderConfig

    def __init__(
        self,
        *,
        providers: list["DetailScanService.ProviderEntry"],
        repository: Repository,
        bot: Bot,
        auth_state: "AuthState",
        analysis_pipeline: AnalysisPipeline,
    ) -> None:
        self._providers = {entry.config.source_id: entry for entry in providers}
        self._repo = repository
        self._bot = bot
        self._lock = asyncio.Lock()
        self._auth_state = auth_state
        self._analysis_pipeline = analysis_pipeline

    async def run_scan(self) -> None:
        async with self._lock:
            try:
                await self._run_scan()
            except Exception:  # pragma: no cover
                LOGGER.exception("Error during detail scan")

    async def _run_scan(self) -> None:
        item = await self._repo.get_next_pending_detail()
        if not item:
            remaining = await self._repo.count_pending_detail()
            LOGGER.info("Detail scan tick", extra={"pulled": 0, "remaining": remaining})
            return
        entry = self._providers.get(item.source_id)
        if entry is None:
            LOGGER.warning("Detail scan skipped: unknown source_id", extra={"source_id": item.source_id})
            await self._repo.complete_detail_scan(item.id)
            return
        prefs = await self._repo.get_preferences()
        await self._process_item(item, prefs, entry)
        remaining = await self._repo.count_pending_detail()
        LOGGER.info("Detail scan tick", extra={"pulled": 1, "remaining": remaining})

    async def _process_item(
        self,
        item: Repository.PendingDetail,
        prefs: AppPreferences | None,
        entry: "DetailScanService.ProviderEntry",
    ) -> None:
        text = (item.detail_text_raw or "").strip() if item.detail_loaded else ""
        if not text:
            try:
                fetch_detail = getattr(entry.provider, "fetch_detail_text", None)
                if fetch_detail is None:
                    LOGGER.warning("Provider has no fetch_detail_text; skipping detail scan")
                    await self._repo.complete_detail_scan(item.id)
                    return
                text = await fetch_detail(item.url)
                if not text:
                    await self._handle_retry(item, entry.config)
                    return
            except Exception:  # pragma: no cover
                LOGGER.exception("Detail fetch failed", extra={"url": item.url})
                await self._handle_retry(item, entry.config)
                return

        notified = 0
        result: AnalysisResult | None = None
        if prefs and prefs.enabled:
            try:
                result = await self._analysis_pipeline.analyze_detection(
                    detection_id=item.id,
                    payload=TenderTextPayload(
                        title=item.title,
                        raw_detail_text=text,
                    ),
                )
            except Exception:
                LOGGER.exception("Analysis pipeline failed", extra={"detection_id": item.id})
                await self._repo.save_analysis_result(
                    item.id,
                    status="failed",
                    analysis_version=self._analysis_pipeline.analysis_version,
                    is_relevant=False,
                    confidence=0.0,
                    summary=None,
                    explanation="Analysis pipeline failed.",
                    decision_source="rules",
                    needs_review=False,
                    matches=[],
                )

        if (
            result is not None
            and result.is_relevant
            and not result.needs_review
            and not await self._repo.has_notification_global_sent(item.source_id, item.external_id)
        ):
            message = self._format_message(
                item.url,
                item.external_id,
                item.title,
                source_id=item.source_id,
                result=result,
            )
            targets_getter = getattr(self._auth_state, "all_targets", None)
            if callable(targets_getter):
                targets = list(targets_getter())
            else:
                targets = getattr(self._auth_state, "authorized_targets", lambda: [])()
            if not targets:
                LOGGER.debug("Detail skip: no authorized chats in session")
            else:
                for chat_id in targets:
                    try:
                        await self._bot.send_message(
                            chat_id=chat_id,
                            text=message,
                            disable_web_page_preview=False,
                            parse_mode="HTML",
                        )
                        notified += 1
                    except Exception:
                        LOGGER.exception("Failed to send detail notification", extra={"chat_id": chat_id})
            if notified > 0:
                await self._repo.create_notification_global(item.source_id, item.external_id, sent=True)

        LOGGER.debug(
            "Detail processed",
            extra={"id": item.id, "loaded": bool(text), "notified": notified},
        )
        await self._repo.complete_detail_scan(item.id)

    async def _handle_retry(self, item: Repository.PendingDetail, provider_config: ProviderConfig) -> None:
        cfg = provider_config.detail
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

    def _format_message(
        self,
        url: str,
        external_id: str,
        title: str | None,
        *,
        source_id: str,
        result: AnalysisResult,
    ) -> str:
        title_text = html.escape(title or "Без названия")
        url_text = html.escape(url)
        external_id_text = html.escape(external_id)
        confidence_pct = round(result.confidence * 100)
        lines = [
            f"<b>🔎 Совпадение в тексте закупки ({html.escape(source_id)})</b>",
            f"<b>Название:</b> {title_text}",
            f"<b>Ссылка:</b> {url_text}",
            f"<b>Номер:</b> {external_id_text}",
            f"<b>Источник решения:</b> {html.escape(result.decision_source)}",
            f"<b>Уверенность:</b> {confidence_pct}%",
        ]
        if result.summary:
            lines.append(f"<b>Суть:</b> {html.escape(self._trim(result.summary, 280))}")
        if result.explanation:
            lines.append(f"<b>Объяснение:</b> {html.escape(self._trim(result.explanation, 220))}")
        if result.needs_review:
            lines.append("<b>Ручной просмотр:</b> желателен")
        if result.matches:
            lines.append("<b>Совпадения:</b>")
            for match in result.matches[:5]:
                lines.append(self._format_match(match))
        return "\n\n".join(lines)

    def _format_match(self, match: KeywordMatch) -> str:
        keyword_text = html.escape(match.keyword)
        reason_text = html.escape(self._trim(match.reason, 180))
        match_type = html.escape(match.match_type)
        score = "" if match.score is None else f" ({round(match.score * 100)}%)"
        return f"• {keyword_text}: {reason_text} [{match_type}]{score}"

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 3] + "..."
