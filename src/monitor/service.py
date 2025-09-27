from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from aiogram import Bot

from ..config import ProviderConfig
from ..db.repo import Repository, AppPreferences
from ..provider.base import Listing, SourceProvider
from .match import Keyword, compile_keywords, find_matching_keywords

LOGGER = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        *,
        provider: SourceProvider,
        repository: Repository,
        bot: Bot,
        provider_config: ProviderConfig,
        auth_state: "AuthState",
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._bot = bot
        self._config = provider_config
        self._lock = asyncio.Lock()
        self._auth_state = auth_state

    async def run_check(self) -> None:
        async with self._lock:
            try:
                await self._run_check()
            except Exception:  # pragma: no cover - defensive logging
                LOGGER.exception("Error during monitor check")

    async def _run_check(self) -> None:
        prefs = await self._repo.get_preferences()
        if not prefs or not prefs.enabled:
            LOGGER.debug("Skip monitor iteration: disabled")
            return
        max_pages = prefs.pages if prefs.pages > 0 else self._config.pages_default
        LOGGER.debug(
            "Starting monitor iteration",
            extra={
                "mode": "global",
                "max_pages": max_pages,
                "source": self._config.source_id,
            },
        )
        keywords = compile_keywords(prefs.keywords)

        for page in range(1, max_pages + 1):
            listings = await self._provider.fetch_page(page)
            LOGGER.debug("Fetched page listings", extra={"page": page, "count": len(listings)})
            if not listings:
                continue
            await self._process_page(page, listings, prefs, keywords)

    async def _process_page(
        self,
        page: int,
        listings: Sequence[Listing],
        prefs: AppPreferences,
        keywords: list[Keyword],
    ) -> None:
        inserted = 0
        notified_total = 0
        for listing in listings:
            is_new = await self._repo.record_detection(
                source_id=self._config.source_id,
                external_id=listing.external_id,
                title=listing.title,
                url=listing.url,
                procedure_type=getattr(listing, "procedure_type", None),
                status=getattr(listing, "status", None),
                deadline=getattr(listing, "deadline", None),
                price=getattr(listing, "price", None),
            )
            if not is_new:
                continue
            inserted += 1
            notified = await self._notify_chats(page, listing, prefs, keywords)
            notified_total += notified
        LOGGER.debug(
            "Processed page",
            extra={"page": page, "inserted": inserted, "notified": notified_total, "total": len(listings)},
        )

    async def _notify_chats(
        self,
        page: int,
        listing: Listing,
        prefs: AppPreferences,
        keywords: list[Keyword],
    ) -> int:
        sent = 0
        if page > (prefs.pages or 0):
            LOGGER.debug("Skip: page out of range", extra={"page": page, "pages": prefs.pages})
            return 0
        if not keywords:
            LOGGER.debug("Skip: no keywords configured")
            return 0
        matched = find_matching_keywords(listing.title, keywords)
        if not matched:
            LOGGER.debug(
                "Skip: title has no keyword matches",
                extra={"title": listing.title, "id": listing.external_id},
            )
            return 0
        # Global dedupe
        if await self._repo.has_notification_global_sent(self._config.source_id, listing.external_id):
            LOGGER.debug("Skip: already notified globally", extra={"id": listing.external_id})
            return 0
        text = self._format_message(listing, matched_keywords=[k.raw for k in matched])
        target = self._auth_state.authorized_target()
        if target is None:
            LOGGER.debug("Skip: no authorized chat in session")
            return 0
        try:
            await self._bot.send_message(chat_id=target, text=text, disable_web_page_preview=False)
            sent = 1
        except Exception:
            LOGGER.exception("Failed to send notification", extra={"chat_id": target})
        if sent > 0:
            await self._repo.create_notification_global(self._config.source_id, listing.external_id, sent=True)
        return sent

    def _format_message(self, listing: Listing, matched_keywords: list[str] | None = None) -> str:
        title = listing.title or "Без названия"
        lines = [
            f"🛒 Новая закупка ({self._config.source_id})",
            f"Название: {title}",
            f"Ссылка: {listing.url}",
            f"Номер: {listing.external_id}",
        ]
        if matched_keywords:
            lines.append(f"Совпадение по: {self._format_keywords(matched_keywords)}")
        if getattr(listing, "procedure_type", None):
            lines.append(f"Вид: {listing.procedure_type}")
        if getattr(listing, "status", None):
            lines.append(f"Статус: {listing.status}")
        if getattr(listing, "deadline", None):
            lines.append(f"До: {listing.deadline}")
        if getattr(listing, "price", None):
            lines.append(f"Стоимость: {listing.price}")
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
