from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

from aiogram import Bot
from datetime import datetime, timedelta

from ..config import ProviderConfig
from ..db.repo import Repository, AppPreferences
from ..provider.base import SourceProvider
from .match import Keyword, compile_keywords
from .semantic import SemanticMatcher, SemanticMatch

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
        semantic_matcher: SemanticMatcher | None = None,
    ) -> None:
        self._providers = {entry.config.source_id: entry for entry in providers}
        self._repo = repository
        self._bot = bot
        self._lock = asyncio.Lock()
        self._auth_state = auth_state
        self._semantic_matcher = semantic_matcher

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
        keywords = compile_keywords(prefs.keywords) if (prefs and prefs.enabled) else []
        await self._process_item(item, prefs, keywords, entry)
        remaining = await self._repo.count_pending_detail()
        LOGGER.info("Detail scan tick", extra={"pulled": 1, "remaining": remaining})

    async def _process_item(
        self,
        item: Repository.PendingDetail,
        prefs: AppPreferences | None,
        keywords: list[Keyword],
        entry: "DetailScanService.ProviderEntry",
    ) -> None:
        text = ""
        try:
            # duck-typing: у провайдера может быть метод fetch_detail_text
            fetch_detail = getattr(entry.provider, "fetch_detail_text", None)
            if fetch_detail is None:
                LOGGER.warning("Provider has no fetch_detail_text; skipping detail scan")
                await self._repo.complete_detail_scan(item.id)
                return
            text = await fetch_detail(item.url)
            if text:
                await self._repo.mark_detail_loaded(item.id, True)
            else:
                await self._handle_retry(item, entry.config)
                return
        except Exception:  # pragma: no cover
            LOGGER.exception("Detail fetch failed", extra={"url": item.url})
            await self._handle_retry(item, entry.config)
            return

        notified = 0
        semantic_details: list[SemanticMatch] = []
        semantic_summary: str | None = None
        submission_deadline: str | None = None
        if prefs and prefs.enabled and keywords:
            matched: list[Keyword] = []
            if self._semantic_matcher and text:
                combined_text = self._combine_title_and_text(item.title, text)
                try:
                    analysis = await self._semantic_matcher.match_keywords(
                        combined_text,
                        [kw.raw for kw in keywords],
                    )
                except Exception:
                    LOGGER.exception("Semantic matcher failed")
                    analysis = None
                if analysis:
                    if analysis.submission_deadline:
                        submission_deadline = analysis.submission_deadline
                if analysis and analysis.matches:
                    lookup = {kw.raw.casefold(): kw for kw in keywords}
                    for match in analysis.matches:
                        keyword_obj = lookup.get(match.keyword.casefold())
                        if keyword_obj is None:
                            continue
                        if keyword_obj in matched:
                            continue
                        matched.append(keyword_obj)
                        reason = " ".join((match.reason or "").split())
                        semantic_details.append(
                            SemanticMatch(
                                keyword=keyword_obj.raw,
                                score=match.score,
                                reason=reason,
                            )
                        )
                    semantic_summary = (analysis.summary or "").strip() or None
            if matched and not await self._repo.has_notification_global_sent(item.source_id, item.external_id):
                message = self._format_message(
                    item.url,
                    item.external_id,
                    item.title,
                    source_id=item.source_id,
                    semantic_summary=semantic_summary,
                    semantic_details=semantic_details if semantic_details else None,
                    submission_deadline=submission_deadline,
                )
                # Collect all target chat ids: authorized chats plus user_ids (for private chats)
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

    @staticmethod
    def _combine_title_and_text(title: str | None, text: str) -> str:
        t = (title or "").strip()
        if not t:
            return text
        if t.casefold() in text.casefold():
            return text
        return f"{t}\n\n{text}"

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
        # clamp
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
        semantic_summary: str | None = None,
        semantic_details: list[SemanticMatch] | None = None,
        submission_deadline: str | None = None,
    ) -> str:
        t = title or "Без названия"
        title_text = html.escape(t)
        url_text = html.escape(url)
        external_id_text = html.escape(external_id)
        lines = [
            f"<b>🔎 Совпадение в тексте закупки ({html.escape(source_id)})</b>",
            f"<b>Название:</b> {title_text}",
            f"<b>Ссылка:</b> {url_text}",
            f"<b>Номер:</b> {external_id_text}",
        ]
        if submission_deadline:
            lines.append(
                f"<b>Приём сведений прекращается:</b> {html.escape(submission_deadline)}"
            )
        if semantic_summary:
            summary_clean = " ".join(semantic_summary.split())
            if len(summary_clean) > 280:
                summary_clean = summary_clean[:277] + "..."
            lines.append(f"<b>Суть:</b> {html.escape(summary_clean)}")
        if semantic_details:
            lines.append("<b>Семантические совпадения:</b>")
            for match in semantic_details:
                reason = self._humanize_reason(match.reason)
                if len(reason) > 180:
                    reason = reason[:177] + "..."
                score_label = self._format_score_label(match.score)
                score_text = f" ({score_label} релевантность)" if score_label else ""
                keyword_text = html.escape(match.keyword)
                reason_text = html.escape(reason)
                lines.append(f"• {keyword_text}: {reason_text}{score_text}")
        return "\n\n".join(lines)

    @staticmethod
    def _humanize_reason(reason: str) -> str:
        cleaned = " ".join((reason or "").split())
        if not cleaned:
            return "Похоже по смыслу."
        if cleaned.lower().startswith("похоже"):
            text = cleaned
        else:
            text = f"Похоже по смыслу: {cleaned}"
        if text[-1] not in ".!?":
            text += "."
        return text

    @staticmethod
    def _format_score_label(score: float) -> str:
        if score >= 0.8:
            return "высокая"
        if score >= 0.6:
            return "средняя"
        if score > 0:
            return "низкая"
        return ""
