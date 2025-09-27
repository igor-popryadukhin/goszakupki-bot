from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import AppConfig
from .db.repo import Repository, init_db
from .monitor.scheduler import MonitorScheduler
from .monitor.detail_service import DetailScanService
from .monitor.detail_scheduler import DetailScanScheduler
from .monitor.service import MonitorService
from .provider.base import SourceProvider
from .provider.goszakupki_http import GoszakupkiHttpProvider
from .tg.bot import create_bot, create_dispatcher
from .tg.auth_state import AuthState

LOGGER = logging.getLogger(__name__)


class Container:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.engine = create_async_engine(config.database.url, echo=False, future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.repository = Repository(self.session_factory)
        self.bot: Bot = create_bot(config.telegram.token)
        self.dispatcher: Dispatcher = create_dispatcher()
        self.provider: SourceProvider = self._create_provider()
        self.auth_state = AuthState(login=config.auth.login or "", password=config.auth.password or "")
        self.monitor_service = MonitorService(
            provider=self.provider,
            repository=self.repository,
            bot=self.bot,
            provider_config=config.provider,
            auth_state=self.auth_state,
        )
        self.scheduler = MonitorScheduler(
            service=self.monitor_service,
            repository=self.repository,
            provider_config=config.provider,
            logging_config=config.logging,
        )
        self.detail_service = DetailScanService(
            provider=self.provider,
            repository=self.repository,
            bot=self.bot,
            provider_config=config.provider,
            auth_state=self.auth_state,
        )
        self.detail_scheduler = DetailScanScheduler(
            service=self.detail_service,
            repository=self.repository,
            provider_config=config.provider,
            logging_config=config.logging,
        )

    def _create_provider(self) -> SourceProvider:
        if self.config.provider.use_playwright:
            LOGGER.warning("Playwright provider requested but not fully implemented; falling back to HTTP provider")
        return GoszakupkiHttpProvider(self.config.provider)

    async def init_database(self) -> None:
        await init_db(self.engine)

    async def shutdown(self) -> None:
        try:
            if hasattr(self.provider, "shutdown"):
                await getattr(self.provider, "shutdown")()
        finally:
            await self.engine.dispose()
            await self.bot.session.close()
