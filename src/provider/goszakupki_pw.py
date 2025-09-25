from __future__ import annotations

import logging

from .base import Listing, SourceProvider

LOGGER = logging.getLogger(__name__)


class GoszakupkiPlaywrightProvider(SourceProvider):
    source_id = "goszakupki.by"

    async def fetch_page(self, page: int) -> list[Listing]:  # pragma: no cover - optional provider stub
        raise NotImplementedError("Playwright provider is not implemented in this build")

    async def startup(self) -> None:  # pragma: no cover - optional provider stub
        LOGGER.error("Playwright provider requested but not implemented")

    async def shutdown(self) -> None:  # pragma: no cover - optional provider stub
        LOGGER.info("Playwright provider shutdown")
