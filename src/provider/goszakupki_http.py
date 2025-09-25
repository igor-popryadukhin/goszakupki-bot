from __future__ import annotations

import asyncio
import logging
import re
import time
import ssl
from typing import Any
from urllib.parse import urljoin, urlencode

import aiohttp
import certifi
from bs4 import BeautifulSoup

from ..config import ProviderConfig
from .base import Listing, SourceProvider

LOGGER = logging.getLogger(__name__)
AUC_PATTERN = re.compile(r"auc[\s\-_]?\d{6,}", re.IGNORECASE)
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
        ssl_context: ssl.SSLContext | bool
        if self._config.http_verify_ssl:
            ssl_context = ssl.create_default_context()
            try:
                ssl_context.load_verify_locations(cafile=certifi.where())
            except Exception:  # pragma: no cover - extremely unlikely
                LOGGER.warning("Failed to load certifi CA bundle, using system defaults")
            if self._config.http_ca_bundle:
                self._load_extra_ca(ssl_context)
        else:
            LOGGER.warning(
                "TLS certificate verification disabled for provider", extra={"source_id": self.source_id}
            )
            if self._config.http_ca_bundle:
                ssl_context = ssl.create_default_context()
                self._load_extra_ca(ssl_context)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            else:
                ssl_context = False

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
        params = {"page": page}
        url = f"{self._config.base_url}?{urlencode(params)}"
        LOGGER.debug("Fetching page", extra={"url": url, "page": page})
        html = await self._request(session, url)
        if not html:
            LOGGER.warning("Empty HTML received", extra={"url": url, "page": page})
            return []
        return self._parse_listings(html)

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            await self.startup()
        if self._session is None:  # pragma: no cover
            raise RuntimeError("HTTP session is not initialized")
        return self._session

    def _load_extra_ca(self, ssl_context: ssl.SSLContext) -> None:
        ca_path = self._config.http_ca_bundle
        if ca_path is None:
            return
        try:
            path_str = str(ca_path)
            if ca_path.is_dir():
                ssl_context.load_verify_locations(capath=path_str)
            else:
                ssl_context.load_verify_locations(cafile=path_str)
        except FileNotFoundError as exc:
            LOGGER.warning(
                "Custom CA bundle not found", exc_info=exc, extra={"ca_bundle": path_str}
            )
        except IsADirectoryError as exc:
            LOGGER.warning(
                "Custom CA bundle is a directory but not accessible", exc_info=exc, extra={"ca_bundle": path_str}
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            LOGGER.warning(
                "Failed to load custom CA bundle", exc_info=exc, extra={"ca_bundle": path_str}
            )

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
        total_items = len(items)
        listings: list[Listing] = []
        skipped_no_link = 0
        skipped_no_href = 0
        skipped_no_id = 0
        for item in items:
            link_el = item.select_one(self._config.selectors.link)
            if not link_el:
                skipped_no_link += 1
                continue
            if not link_el.has_attr("href"):
                skipped_no_href += 1
                continue
            href = link_el.get("href", "").strip()
            url = urljoin(self._config.base_url, href)
            title_el = item.select_one(self._config.selectors.title)
            title = title_el.get_text(strip=True) if title_el else None
            external_id = self._extract_id(item, link_el.get("href", ""))
            if not external_id:
                skipped_no_id += 1
                continue
            listings.append(Listing(external_id=external_id, title=title or None, url=url))

        if listings:
            LOGGER.debug(
                "Parsed listings via CSS selectors",
                extra={
                    "found_items": total_items,
                    "parsed": len(listings),
                    "skipped_no_link": skipped_no_link,
                    "skipped_no_href": skipped_no_href,
                    "skipped_no_id": skipped_no_id,
                    "list_item_selector": self._config.selectors.list_item,
                    "title_selector": self._config.selectors.title,
                    "link_selector": self._config.selectors.link,
                },
            )
            return listings
        else:
            LOGGER.info(
                "No listings found by CSS selectors; trying table fallback",
                extra={
                    "found_items": total_items,
                    "list_item_selector": self._config.selectors.list_item,
                    "title_selector": self._config.selectors.title,
                    "link_selector": self._config.selectors.link,
                },
            )

        # 2) Фолбэк: таблица с id=w0 -> w0/table/tbody/tr (CSS)
        table_wrapper = soup.select_one("#w0")
        if table_wrapper is None:
            LOGGER.info("Table wrapper #w0 not found")
            return []
        table = table_wrapper.select_one("table")
        if table is None:
            LOGGER.info("Table element under #w0 not found")
            return []
        rows = table.select("tbody tr") or table.select("tr")
        LOGGER.debug("Table rows discovered", extra={"rows": len(rows)})
        parsed_rows = 0
        skipped_rows_no_tds = 0
        skipped_rows_no_link = 0
        skipped_rows_no_href = 0
        skipped_rows_no_id = 0
        for row in rows:
            tds = row.select("td")
            if not tds or len(tds) < 2:
                skipped_rows_no_tds += 1
                continue

            # ID из первой колонки (Номер закупки), запасной путь — по всему ряду
            id_text = tds[0].get_text(" ", strip=True)
            external_id = self._extract_id_text(id_text)
            if not external_id:
                external_id = self._extract_id_text(row.get_text(" ", strip=True))

            # Ссылка/заголовок — во второй колонке есть <a>
            link_el = tds[1].select_one("a[href]") or row.select_one("a[href]")
            if not link_el:
                skipped_rows_no_link += 1
                continue
            href = (link_el.get("href") or "").strip()
            if not href:
                skipped_rows_no_href += 1
                continue
            url = urljoin(self._config.base_url, href)
            title = link_el.get_text(strip=True) or None

            # Доп. поля при наличии колонок: 2=Вид процедуры, 3=Статус, 4=До, 5=Стоимость
            procedure_type = (tds[2].get_text(" ", strip=True) if len(tds) > 2 else None) or None
            status = (tds[3].get_text(" ", strip=True) if len(tds) > 3 else None) or None
            deadline = (tds[4].get_text(" ", strip=True) if len(tds) > 4 else None) or None
            price = (tds[5].get_text(" ", strip=True) if len(tds) > 5 else None) or None

            if not external_id:
                skipped_rows_no_id += 1
                continue

            listings.append(
                Listing(
                    external_id=external_id,
                    title=title,
                    url=url,
                    procedure_type=procedure_type,
                    status=status,
                    deadline=deadline,
                    price=price,
                )
            )
            parsed_rows += 1

        LOGGER.log(
            logging.INFO if parsed_rows == 0 else logging.DEBUG,
            "Parsed listings via table fallback",
            extra={
                "rows": len(rows),
                "parsed": parsed_rows,
                "skipped_no_link": skipped_rows_no_link,
                "skipped_no_href": skipped_rows_no_href,
                "skipped_no_id": skipped_rows_no_id,
                "skipped_no_tds": skipped_rows_no_tds,
            },
        )
        if listings:
            return listings

        # 3) Фолбэк XPATH: //*[@id="w0"]/table//tr
        try:
            from lxml import html as lxml_html  # already in deps

            doc = lxml_html.fromstring(html)
            xpath_rows = doc.xpath('//*[@id="w0"]/table//tr')
            LOGGER.debug("XPath rows discovered", extra={"rows": len(xpath_rows)})
            parsed = 0
            for row in xpath_rows:
                tds = row.xpath('./td')
                # ID из первой колонки
                id_text = ''
                if tds:
                    try:
                        id_text = tds[0].text_content().strip()
                    except Exception:
                        id_text = ''
                external_id = self._extract_id_text(id_text) or self._extract_id_text(row.text_content())

                link_els = (tds[1].xpath('.//a[@href]') if len(tds) > 1 else []) or row.xpath('.//a[@href]')
                if not link_els:
                    continue
                href = (link_els[0].get('href') or '').strip()
                if not href:
                    continue
                url = urljoin(self._config.base_url, href)
                title = (link_els[0].text_content() or '').strip() or None
                if not external_id:
                    continue
                # Доп. поля, если есть ячейки
                procedure_type = (tds[2].text_content().strip() if len(tds) > 2 else None) or None
                status = (tds[3].text_content().strip() if len(tds) > 3 else None) or None
                deadline = (tds[4].text_content().strip() if len(tds) > 4 else None) or None
                price = (tds[5].text_content().strip() if len(tds) > 5 else None) or None
                listings.append(
                    Listing(
                        external_id=external_id,
                        title=title,
                        url=url,
                        procedure_type=procedure_type,
                        status=status,
                        deadline=deadline,
                        price=price,
                    )
                )
                parsed += 1
            LOGGER.log(
                logging.INFO if parsed == 0 else logging.DEBUG,
                "Parsed listings via XPath fallback",
                extra={"rows": len(xpath_rows), "parsed": parsed},
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("XPath fallback failed", extra={"error": str(exc)})
        return listings

    def _extract_id(self, item: Any, href: str) -> str | None:
        if self._config.selectors.id_from_href:
            match = AUC_PATTERN.search(href or "")
            if match:
                return self._normalize_auc(match.group(0))
        if self._config.selectors.id_text:
            node = item.select_one(self._config.selectors.id_text)
            if node:
                match = AUC_PATTERN.search(node.get_text(" ", strip=True))
                if match:
                    return self._normalize_auc(match.group(0))
        match = AUC_PATTERN.search(href or "")
        return self._normalize_auc(match.group(0)) if match else None

    def _extract_id_text(self, text: str) -> str | None:
        match = AUC_PATTERN.search(text or "")
        return self._normalize_auc(match.group(0)) if match else None

    @staticmethod
    def _normalize_auc(value: str) -> str:
        # Приводим к виду: "auc" + цифры, без разделителей
        v = (value or "").lower()
        v = re.sub(r"[^a-z0-9]", "", v)
        return v
