from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from datetime import date
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp
from bs4 import BeautifulSoup

from ..config import ProviderConfig
from .base import Listing, SourceProvider

LOGGER = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"\b\d{4,}(?:[-/]\d+)?\b")
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
START_URL = "https://icetrade.by/tenders/all"


class IcetradeHttpProvider(SourceProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._config = config
        self.source_id = config.source_id
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(max(config.http_concurrency, 1))
        self._rate_lock = asyncio.Lock()
        self._min_interval = 1.0 / config.rate_limit_rps if config.rate_limit_rps > 0 else 0.0
        self._last_request = 0.0
        self._degraded = False
        self._listing_base_url: str | None = None

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
        LOGGER.warning(
            "TLS certificate verification disabled for provider (forced)", extra={"source_id": self.source_id}
        )
        ssl_context: ssl.SSLContext | bool = False
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        self._session = aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector)
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
        if not self._listing_base_url:
            self._listing_base_url = await self._resolve_listing_base_url(session)
        list_url = self._build_listing_url(page)
        LOGGER.debug("Fetching page", extra={"url": list_url, "page": page})
        html = await self._request(session, list_url)
        if not html:
            LOGGER.warning("Empty HTML received", extra={"url": list_url, "page": page})
            return []
        return self._parse_listings(html)

    async def fetch_detail_text(self, url: str) -> str:
        session = await self._ensure_session()
        LOGGER.debug("Fetching detail page", extra={"url": url})
        html = await self._request(session, url)
        if not html:
            return ""
        try:
            soup = BeautifulSoup(html, "lxml")
            for tag in soup.find_all(["script", "style", "noscript", "template"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            return text
        except Exception:  # pragma: no cover - устойчивость к кривой верстке
            LOGGER.exception("Failed to parse detail page", extra={"url": url})
            return ""

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.startup()
        if self._session is None:  # pragma: no cover
            raise RuntimeError("HTTP session is not initialized")
        return self._session

    async def _resolve_listing_base_url(self, session: aiohttp.ClientSession) -> str:
        attempt = 0
        backoff = 1.0
        url = START_URL
        while True:
            attempt += 1
            async with self._semaphore:
                await self._throttle()
                try:
                    async with session.get(url, allow_redirects=True) as response:
                        if response.status == 200:
                            await response.text()
                            final_url = str(response.url)
                            LOGGER.debug("Resolved listing base URL", extra={"url": final_url})
                            return final_url
                        if response.status in RETRYABLE_STATUS and attempt < 5:
                            LOGGER.warning(
                                "Retryable status %s from %s", response.status, url, extra={"attempt": attempt}
                            )
                            await asyncio.sleep(backoff)
                            backoff = min(backoff * 2, 30)
                            continue
                        LOGGER.error("Unexpected status %s from %s", response.status, url)
                        return self._config.base_url
                except aiohttp.ClientError as exc:
                    if attempt >= 5:
                        LOGGER.error("HTTP request failed after retries", exc_info=exc, extra={"url": url})
                        return self._config.base_url
                    LOGGER.warning("HTTP error, retrying", exc_info=exc, extra={"url": url, "attempt": attempt})
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

    def _build_listing_url(self, page: int) -> str:
        base_url = self._listing_base_url or self._config.base_url
        parsed = urlsplit(base_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        date_value = date.today().strftime("%d.%m.%Y")
        params["created_from"] = [date_value]
        params["created_to"] = [date_value]
        params["p"] = [str(page)]
        params["onPage"] = ["20"]
        query = urlencode(params, doseq=True)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))

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
        listings: list[Listing] = []
        if not self._config.prefer_table:
            listings = self._parse_card_listings(soup)
            if listings:
                return listings
        listings = self._parse_table_listings(soup)
        if listings:
            return listings
        if self._config.prefer_table:
            listings = self._parse_card_listings(soup)
        return listings

    def _parse_card_listings(self, soup: BeautifulSoup) -> list[Listing]:
        items = soup.select(self._config.selectors.list_item)
        listings: list[Listing] = []
        for item in items:
            link_el = item.select_one(self._config.selectors.link)
            if not link_el or not link_el.has_attr("href"):
                continue
            href = (link_el.get("href") or "").strip()
            if not href:
                continue
            url = urljoin(self._config.base_url, href)
            title_el = item.select_one(self._config.selectors.title)
            title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True) or None
            external_id = None
            if self._config.selectors.id_text:
                id_node = item.select_one(self._config.selectors.id_text)
                if id_node:
                    external_id = self._extract_external_id(id_node.get_text(" ", strip=True), href)
            if not external_id:
                external_id = self._extract_external_id(item.get_text(" ", strip=True), href)
            if not external_id:
                continue
            listings.append(Listing(external_id=external_id, title=title, url=url))
        return listings

    def _parse_table_listings(self, soup: BeautifulSoup) -> list[Listing]:
        selectors = self._config.table_selectors
        if selectors is None:
            return []
        rows = soup.select(selectors.row)
        listings: list[Listing] = []
        for row in rows:
            link_el = row.select_one(selectors.link)
            if not link_el or not link_el.has_attr("href"):
                continue
            href = (link_el.get("href") or "").strip()
            if not href:
                continue
            url = urljoin(self._config.base_url, href)
            title = None
            if selectors.title:
                title_el = row.select_one(selectors.title)
                title = title_el.get_text(strip=True) if title_el else None
            if not title:
                title = link_el.get_text(strip=True) or None
            id_text = ""
            if selectors.id_cell:
                id_cell = row.select_one(selectors.id_cell)
                if id_cell:
                    id_text = id_cell.get_text(" ", strip=True)
            external_id = self._extract_external_id(id_text, href)
            if not external_id:
                external_id = self._extract_external_id(row.get_text(" ", strip=True), href)
            if not external_id:
                continue
            listings.append(Listing(external_id=external_id, title=title, url=url))
        return listings

    def _extract_external_id(self, text: str, href: str) -> str | None:
        selectors = self._config.table_selectors
        if selectors and selectors.id_from_href:
            external_id = self._extract_external_id_from_href(href)
            if external_id:
                return external_id
        external_id = self._extract_external_id_from_text(text)
        if external_id:
            return external_id
        if self._config.selectors.id_from_href:
            external_id = self._extract_external_id_from_href(href)
            if external_id:
                return external_id
        return self._extract_external_id_from_href(href)

    def _extract_external_id_from_text(self, text: str) -> str | None:
        match = ID_PATTERN.search(text or "")
        if not match:
            return None
        return self._normalize_external_id(match.group(0))

    def _extract_external_id_from_href(self, href: str) -> str | None:
        if not href:
            return None
        parsed = urlsplit(href)
        params = parse_qs(parsed.query)
        for key in ("id", "tender_id", "tenderId", "purchaseId"):
            value = params.get(key)
            if value:
                return self._normalize_external_id(value[0])
        match = ID_PATTERN.search(href)
        if match:
            return self._normalize_external_id(match.group(0))
        return None

    @staticmethod
    def _normalize_external_id(value: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z_-]", "", value or "")
        return cleaned.lower()
