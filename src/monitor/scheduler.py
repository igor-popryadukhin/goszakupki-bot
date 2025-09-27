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
        provider_config: ProviderConfig,
        logging_config: LoggingConfig,
    ) -> None:
        self._service = service
        self._repo = repository
        self._provider_config = provider_config
        self._scheduler = AsyncIOScheduler(timezone=logging_config.timezone)
        self._job = None

    async def start(self) -> None:
        self._scheduler.start()
        await self.refresh_schedule()

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def refresh_schedule(self) -> None:
        # Если нет включённых чатов — останавливаем задачу полностью
        prefs = await self._repo.list_enabled_preferences()
        if not prefs:
            if self._job is not None:
                self._job.remove()
                self._job = None
                LOGGER.info("Monitor job stopped: no enabled chats")
            return
        interval = await self._determine_interval()
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
        prefs = await self._repo.list_enabled_preferences()
        intervals = [pref.interval_seconds for pref in prefs if pref.interval_seconds > 0]
        if intervals:
            return min(intervals)
        return max(self._provider_config.check_interval_default, 60)
