from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import LoggingConfig, ProviderConfig
from ..db.repo import Repository
from .service import MonitorService

LOGGER = logging.getLogger(__name__)


class MonitorScheduler:
    def __init__(
        self,
        *,
        service: MonitorService,
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
        # Если глобальные настройки выключены — останавливаем задачу полностью
        if not await self._repo.is_enabled():
            if self._job is not None:
                self._job.remove()
                self._job = None
                LOGGER.info("Monitor job stopped: disabled")
            return
        prefs = await self._repo.get_preferences()
        interval = prefs.interval_seconds if prefs and prefs.interval_seconds > 0 else await self._determine_interval()
        trigger = IntervalTrigger(seconds=interval, start_date=datetime.now(self._scheduler.timezone))
        if self._job is None:
            self._job = self._scheduler.add_job(self._service.run_check, trigger=trigger)
            LOGGER.info("Monitor job scheduled", extra={"interval": interval})
            # Выполнить первую проверку немедленно, чтобы заполнить detections при первом включении
            try:
                await self._service.run_check()
            except Exception:  # pragma: no cover - дополнительная устойчивость
                LOGGER.exception("Immediate monitor run failed after scheduling")
        else:
            self._job.reschedule(trigger=trigger)
            LOGGER.info("Monitor job rescheduled", extra={"interval": interval})

    async def _determine_interval(self) -> int:
        prefs = await self._repo.get_preferences()
        if prefs and prefs.interval_seconds > 0:
            return prefs.interval_seconds
        if not self._provider_configs:
            return 60
        interval = min(cfg.check_interval_default for cfg in self._provider_configs)
        return max(interval, 60)
