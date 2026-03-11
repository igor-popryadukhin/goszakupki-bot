from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import LoggingConfig
from ..db.repo import Repository
from .joke_service import JokeService

LOGGER = logging.getLogger(__name__)


class JokeScheduler:
    def __init__(
        self,
        *,
        service: JokeService,
        repository: Repository,
        logging_config: LoggingConfig,
        interval_seconds: int = 60 * 60 * 3,
    ) -> None:
        self._service = service
        self._repo = repository
        self._scheduler = AsyncIOScheduler(timezone=logging_config.timezone)
        self._job = None
        self._interval_seconds = max(interval_seconds, 60 * 10)

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
                LOGGER.info("Joke job stopped: disabled")
            return
        trigger = IntervalTrigger(seconds=self._interval_seconds, start_date=datetime.now(self._scheduler.timezone))
        if self._job is None:
            self._job = self._scheduler.add_job(self._service.run_send, trigger=trigger)
            LOGGER.info("Joke job scheduled", extra={"interval": self._interval_seconds})
        else:
            self._job.reschedule(trigger=trigger)
            LOGGER.info("Joke job rescheduled", extra={"interval": self._interval_seconds})
