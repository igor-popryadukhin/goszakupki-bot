from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..config import DeepSeekConfig, LoggingConfig
from .deepseek_balance import DeepSeekBalanceService

LOGGER = logging.getLogger(__name__)


class DeepSeekBalanceScheduler:
    def __init__(
        self,
        *,
        service: DeepSeekBalanceService | None,
        deepseek_config: DeepSeekConfig,
        logging_config: LoggingConfig,
    ) -> None:
        self._service = service
        self._config = deepseek_config
        self._scheduler = AsyncIOScheduler(timezone=logging_config.timezone)
        self._job = None

    async def start(self) -> None:
        self._scheduler.start()
        await self.refresh_schedule()

    async def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    async def refresh_schedule(self) -> None:
        if self._service is None or not self._service.enabled:
            if self._job is not None:
                self._job.remove()
                self._job = None
                LOGGER.info("DeepSeek balance job stopped: disabled")
            return

        interval = max(self._config.balance_check_interval_seconds, 60)
        trigger = IntervalTrigger(seconds=interval, start_date=datetime.now(self._scheduler.timezone))
        if self._job is None:
            self._job = self._scheduler.add_job(self._service.run_check, trigger=trigger)
            LOGGER.info("DeepSeek balance job scheduled", extra={"interval": interval})
            await self._service.run_check()
        else:
            self._job.reschedule(trigger=trigger)
            LOGGER.info("DeepSeek balance job rescheduled", extra={"interval": interval})
