from __future__ import annotations

import logging
from datetime import datetime, timedelta, time
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import LoggingConfig
from ..db.repo import Repository
from .weekly_report_service import WeeklyReportService

LOGGER = logging.getLogger(__name__)


class WeeklyReportScheduler:
    def __init__(
        self,
        *,
        service: WeeklyReportService,
        repository: Repository,
        logging_config: LoggingConfig,
        report_time: Optional[time] = None,
    ) -> None:
        self._service = service
        self._repo = repository
        self._scheduler = AsyncIOScheduler(timezone=logging_config.timezone)
        self._job = None
        self._report_time = report_time

    async def start(self) -> None:
        self._scheduler.start()
        await self.refresh_schedule()

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def refresh_schedule(self) -> None:
        if not await self._repo.is_enabled():
            if self._job is not None:
                self._job.remove()
                self._job = None
                LOGGER.info("Weekly report job stopped: disabled")
            return

        trigger = IntervalTrigger(days=7, start_date=self._start_date())
        if self._job is None:
            self._job = self._scheduler.add_job(self._service.send_report, trigger=trigger)
            LOGGER.info("Weekly report job scheduled")
        else:
            self._job.reschedule(trigger=trigger)
            LOGGER.info("Weekly report job rescheduled")

    def _start_date(self) -> datetime:
        now = datetime.now(self._scheduler.timezone)
        if not self._report_time:
            return now

        candidate = datetime.combine(now.date(), self._report_time, tzinfo=self._scheduler.timezone)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
