from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import LoggingConfig, ProviderConfig
from ..db.repo import Repository
from .detail_service import DetailScanService

LOGGER = logging.getLogger(__name__)


class DetailScanScheduler:
    def __init__(
        self,
        *,
        service: DetailScanService,
        repository: Repository,
        provider_configs: list[ProviderConfig],
        logging_config: LoggingConfig,
    ) -> None:
        self._service = service
        self._repo = repository
        self._provider_configs = provider_configs
        self._scheduler = AsyncIOScheduler(timezone=logging_config.timezone)
        self._job = None

    async def start(self) -> None:
        self._scheduler.start()
        await self.refresh_schedule()

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def refresh_schedule(self) -> None:
        # Останавливаем детсканер, если глобально выключено
        if not await self._repo.is_enabled():
            if self._job is not None:
                self._job.remove()
                self._job = None
                LOGGER.info("Detail scan job stopped: disabled")
            return
        interval = await self._determine_interval()
        trigger = IntervalTrigger(seconds=interval, start_date=datetime.now(self._scheduler.timezone))
        if self._job is None:
            self._job = self._scheduler.add_job(self._service.run_scan, trigger=trigger)
            LOGGER.info("Detail scan job scheduled", extra={"interval": interval})
        else:
            self._job.reschedule(trigger=trigger)
            LOGGER.info("Detail scan job rescheduled", extra={"interval": interval})

    async def _determine_interval(self) -> int:
        # Жёстко используем значение из конфигурации, минимум 1 сек.
        if not self._provider_configs:
            return 1
        interval = min(cfg.detail.interval_seconds for cfg in self._provider_configs)
        return max(interval, 1)
