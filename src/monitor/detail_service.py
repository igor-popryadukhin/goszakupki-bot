from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from datetime import datetime, timedelta

from ..config import ProviderConfig
from ..db.repo import Repository, AppPreferences
from ..provider.base import SourceProvider
from .match import Keyword, compile_keywords, find_matching_keywords
from .semantic import SemanticMatcher, SemanticMatch

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
        semantic_matcher: SemanticMatcher | None = None,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._bot = bot
        self._config = provider_config
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
            # duck-typing: у провайдера может быть метод fetch_detail_text
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
        except Exception:  # pragma: no cover
            LOGGER.exception("Detail fetch failed", extra={"url": item.url})
            await self._handle_retry(item)
            return

        notified = 0
        semantic_details: list[SemanticMatch] = []
        semantic_summary: str | None = None
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
            if not matched and text:
                matched = find_matching_keywords(text, keywords)
            if not matched and item.title:
                matched = find_matching_keywords(item.title, keywords)
            if matched and not await self._repo.has_notification_global_sent(self._config.source_id, item.external_id):
                message = self._format_message(
                    item.url,
                    item.external_id,
                    item.title,
                    [k.raw for k in matched],
                    semantic_summary=semantic_summary,
                    semantic_details=semantic_details if semantic_details else None,
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
                            await self._bot.send_message(chat_id=chat_id, text=message, disable_web_page_preview=False)
                            notified += 1
                        except Exception:
                            LOGGER.exception("Failed to send detail notification", extra={"chat_id": chat_id})
                if notified > 0:
                    await self._repo.create_notification_global(self._config.source_id, item.external_id, sent=True)

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
        matched_keywords: list[str] | None,
        *,
        semantic_summary: str | None = None,
        semantic_details: list[SemanticMatch] | None = None,
    ) -> str:
        t = title or "Без названия"
        lines = [
            f"🔎 Совпадение в тексте закупки ({self._config.source_id})",
            f"Название: {t}",
            f"Ссылка: {url}",
            f"Номер: {external_id}",
        ]
        if semantic_summary:
            summary_clean = " ".join(semantic_summary.split())
            if len(summary_clean) > 280:
                summary_clean = summary_clean[:277] + "..."
            lines.append(f"Суть: {summary_clean}")
        if semantic_details:
            lines.append("Семантические совпадения:")
            for match in semantic_details:
                reason = match.reason or "Совпадение по смыслу"
                reason = " ".join(reason.split())
                if len(reason) > 180:
                    reason = reason[:177] + "..."
                score_text = f" (оценка {match.score:.2f})" if match.score > 0 else ""
                lines.append(f"• {match.keyword}: {reason}{score_text}")
        elif matched_keywords:
            lines.append(f"Совпадение по: {self._format_keywords(matched_keywords)}")
        return "\n".join(lines)

    @staticmethod
    def _format_keywords(keywords: list[str], *, limit: int = 5) -> str:
        seen: set[str] = set()
        uniq: list[str] = []
        for k in keywords:
            s = (k or "").strip()
            if not s:
                continue
            key = s.casefold()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(s)
        if len(uniq) <= limit:
            return ", ".join(uniq)
        rest = len(uniq) - limit
        return f"{', '.join(uniq[:limit])} (и ещё {rest})"
