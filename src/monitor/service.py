from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Sequence

from aiogram import Bot

from ..config import ProviderConfig
from ..db.repo import Repository, AppPreferences
from ..provider.base import Listing, SourceProvider
from .match import Keyword  # kept for compatibility; _notify_chats is disabled

LOGGER = logging.getLogger(__name__)


class MonitorService:
    @dataclass(slots=True)
    class ProviderEntry:
        provider: SourceProvider
        config: ProviderConfig

    def __init__(
        self,
        *,
        providers: Sequence["MonitorService.ProviderEntry"],
        repository: Repository,
        bot: Bot,
        auth_state: "AuthState",
    ) -> None:
        self._providers = list(providers)
        self._repo = repository
        self._bot = bot
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
        for entry in self._providers:
            max_pages = prefs.pages if prefs.pages > 0 else entry.config.pages_default
            LOGGER.debug(
                "Starting monitor iteration",
                extra={
                    "mode": "global",
                    "max_pages": max_pages,
                    "source": entry.config.source_id,
                },
            )

            for page in range(1, max_pages + 1):
                listings = await entry.provider.fetch_page(page)
                LOGGER.debug("Fetched page listings", extra={"page": page, "count": len(listings)})
                if not listings:
                    continue
                await self._process_page(page, listings, prefs, entry.config)

    async def _process_page(
        self,
        page: int,
        listings: Sequence[Listing],
        prefs: AppPreferences,
        provider_config: ProviderConfig,
    ) -> None:
        inserted = 0
        notified_total = 0
        for listing in listings:
            is_new = await self._repo.record_detection(
                source_id=provider_config.source_id,
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
            # Notifications on list pages are disabled; handled by detail scanner after text load
            # Keep counter for symmetry
            notified = 0
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
        provider_config: ProviderConfig,
    ) -> int:
        # Disabled: notifications are only sent after detail text scan
        LOGGER.debug(
            "List-stage notifications disabled; waiting for detail scan",
            extra={"id": listing.external_id, "page": page, "source": provider_config.source_id},
        )
        return 0

    def _format_message(
        self,
        listing: Listing,
        provider_config: ProviderConfig,
        matched_keywords: list[str] | None = None,
    ) -> str:
        title = listing.title or "Без названия"
        lines = [
            f"🛒 Новая закупка ({provider_config.source_id})",
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
