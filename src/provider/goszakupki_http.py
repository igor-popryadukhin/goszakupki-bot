from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlencode

import aiohttp
from bs4 import BeautifulSoup

from ..config import ProviderConfig
from .base import Listing, SourceProvider

LOGGER = logging.getLogger(__name__)
AUC_PATTERN = re.compile(r"(auc\d{7,})", re.IGNORECASE)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class GoszakupkiHttpProvider(SourceProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self.source_id = config.source_id
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(max(config.http_concurrency, 1))
        self._rate_lock = asyncio.Lock()
        self._min_interval = 1.0 / config.rate_limit_rps if config.rate_limit_rps > 0 else 0.0
        self._last_request = 0.0
        self._degraded = False

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    async def startup(self) -> None:
        if self._session is not None:
            return
        timeout = aiohttp.ClientTimeout(total=self._config.http_timeout_seconds)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept": "text/html",
        }
        self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        try:
            listings = await self.fetch_page(1)
            if not listings:
                self._degraded = True
                LOGGER.error("No listings found during provider self-test", extra={"source_id": self.source_id})
        except Exception as exc:  # pragma: no cover - defensive logging
            self._degraded = True
            LOGGER.exception("Provider self-test failed", extra={"source_id": self.source_id, "error": str(exc)})

    async def shutdown(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_page(self, page: int) -> list[Listing]:
        if page < 1:
            raise ValueError("Page index must start from 1")
        session = await self._ensure_session()
        params = {"page": page}
        url = f"{self._config.base_url}?{urlencode(params)}"
        html = await self._request(session, url)
        if not html:
            return []
        return self._parse_listings(html)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.startup()
        if self._session is None:  # pragma: no cover
            raise RuntimeError("HTTP session is not initialized")
        return self._session

    async def _request(self, session: aiohttp.ClientSession, url: str) -> str:
        attempt = 0
        backoff = 1.0
        while True:
            attempt += 1
            async with self._semaphore:
                await self._throttle()
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            return await response.text()
                        if response.status in RETRYABLE_STATUS and attempt < 5:
                            LOGGER.warning(
                                "Retryable status %s from %s", response.status, url, extra={"attempt": attempt}
                            )
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 30)
                            continue
                        LOGGER.error("Unexpected status %s from %s", response.status, url)
                        return ""
                except aiohttp.ClientError as exc:
                    if attempt >= 5:
                        LOGGER.error("HTTP request failed after retries", exc_info=exc, extra={"url": url})
                        return ""
                    LOGGER.warning("HTTP error, retrying", exc_info=exc, extra={"url": url, "attempt": attempt})
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            sleep_for = self._last_request + self._min_interval - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._last_request = time.monotonic()

    def _parse_listings(self, html: str) -> list[Listing]:
        soup = BeautifulSoup(html, "lxml")
        items = soup.select(self._config.selectors.list_item)
        listings: list[Listing] = []
        for item in items:
            link_el = item.select_one(self._config.selectors.link)
            if not link_el or not link_el.has_attr("href"):
                continue
            href = link_el.get("href", "").strip()
            url = urljoin(self._config.base_url, href)
            title_el = item.select_one(self._config.selectors.title)
            title = title_el.get_text(strip=True) if title_el else None
            external_id = self._extract_id(item, link_el.get("href", ""))
            if not external_id:
                continue
            listings.append(Listing(external_id=external_id, title=title or None, url=url))
        return listings

    def _extract_id(self, item: Any, href: str) -> str | None:
        if self._config.selectors.id_from_href:
            match = AUC_PATTERN.search(href or "")
            if match:
                return match.group(1).lower()
        if self._config.selectors.id_text:
            node = item.select_one(self._config.selectors.id_text)
            if node:
                match = AUC_PATTERN.search(node.get_text(" ", strip=True))
                if match:
                    return match.group(1).lower()
        match = AUC_PATTERN.search(href or "")
        return match.group(1).lower() if match else None
