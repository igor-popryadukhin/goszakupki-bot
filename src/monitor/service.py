from __future__ import annotations

import asyncio
import logging
from typing import Sequence

from aiogram import Bot

from ..config import ProviderConfig
from ..db.repo import ChatPreferences, Repository
from ..provider.base import Listing, SourceProvider
from .match import Keyword, compile_keywords, match_title

LOGGER = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        *,
        provider: SourceProvider,
        repository: Repository,
        bot: Bot,
        provider_config: ProviderConfig,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._bot = bot
        self._config = provider_config
        self._lock = asyncio.Lock()

    async def run_check(self) -> None:
        async with self._lock:
            try:
                await self._run_check()
            except Exception:  # pragma: no cover - defensive logging
                LOGGER.exception("Error during monitor check")

    async def _run_check(self) -> None:
        prefs = await self._repo.list_enabled_preferences()
        if not prefs:
            return
        max_pages = max((pref.pages for pref in prefs if pref.pages > 0), default=self._config.pages_default)
        keyword_map: dict[int, list[Keyword]] = {
            pref.chat_id: compile_keywords(pref.keywords) for pref in prefs
        }

        for page in range(1, max_pages + 1):
            listings = await self._provider.fetch_page(page)
            if not listings:
                continue
            await self._process_page(page, listings, prefs, keyword_map)

    async def _process_page(
        self,
        page: int,
        listings: Sequence[Listing],
        prefs: Sequence[ChatPreferences],
        keyword_map: dict[int, list[Keyword]],
    ) -> None:
        for listing in listings:
            is_new = await self._repo.record_detection(
                source_id=self._config.source_id,
                external_id=listing.external_id,
                title=listing.title,
                url=listing.url,
            )
            if not is_new:
                continue
            await self._notify_chats(page, listing, prefs, keyword_map)

    async def _notify_chats(
        self,
        page: int,
        listing: Listing,
        prefs: Sequence[ChatPreferences],
        keyword_map: dict[int, list[Keyword]],
    ) -> None:
        for pref in prefs:
            if pref.pages <= 0:
                continue
            if page > pref.pages:
                continue
            if not match_title(listing.title, keyword_map.get(pref.chat_id, [])):
                continue
            if await self._repo.has_notification(pref.chat_id, self._config.source_id, listing.external_id):
                continue
            text = self._format_message(listing)
            try:
                await self._bot.send_message(chat_id=pref.chat_id, text=text, disable_web_page_preview=False)
            except Exception:  # pragma: no cover - network errors
                LOGGER.exception("Failed to send notification", extra={"chat_id": pref.chat_id})
                continue
            await self._repo.create_notification(pref.chat_id, self._config.source_id, listing.external_id)

    def _format_message(self, listing: Listing) -> str:
        title = listing.title or "Без названия"
        return (
            f"🛒 Новая закупка ({self._config.source_id})\n"
            f"Название: {title}\n"
            f"Ссылка: {listing.url}\n"
            f"Номер: {listing.external_id}"
        )
