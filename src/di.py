from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import AppConfig, ProviderConfig
from .db.repo import Repository, init_db
from .monitor.scheduler import MonitorScheduler
from .monitor.detail_service import DetailScanService
from .monitor.semantic import DeepSeekSemanticAnalyzer
from .monitor.detail_scheduler import DetailScanScheduler
from .monitor.service import MonitorService
from .provider.base import SourceProvider
from .provider.goszakupki_http import GoszakupkiHttpProvider
from .provider.icetrade_http import IcetradeHttpProvider
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
        self.providers: list[SourceProvider] = self._create_providers()
        self.auth_state = AuthState(login=config.auth.login or "", password=config.auth.password or "", repo=self.repository)
        if config.deepseek.enabled and config.deepseek.api_key:
            LOGGER.info(
                "DeepSeek semantic analysis enabled", extra={"model": config.deepseek.model}
            )
            self.semantic_matcher = DeepSeekSemanticAnalyzer(config.deepseek)
        else:
            self.semantic_matcher = None
        provider_entries = [
            MonitorService.ProviderEntry(provider=provider, config=provider_config)
            for provider, provider_config in zip(self.providers, config.providers)
        ]
        self.monitor_service = MonitorService(
            providers=provider_entries,
            repository=self.repository,
            bot=self.bot,
            auth_state=self.auth_state,
        )
        self.scheduler = MonitorScheduler(
            service=self.monitor_service,
            repository=self.repository,
            provider_configs=config.providers,
            logging_config=config.logging,
        )
        self.detail_service = DetailScanService(
            providers=[
                DetailScanService.ProviderEntry(provider=provider, config=provider_config)
                for provider, provider_config in zip(self.providers, config.providers)
            ],
            repository=self.repository,
            bot=self.bot,
            auth_state=self.auth_state,
            semantic_matcher=self.semantic_matcher,
        )
        self.detail_scheduler = DetailScanScheduler(
            service=self.detail_service,
            repository=self.repository,
            provider_configs=config.providers,
            logging_config=config.logging,
        )

    def _create_providers(self) -> list[SourceProvider]:
        providers: list[SourceProvider] = []
        for provider_config in self.config.providers:
            if provider_config.use_playwright:
                LOGGER.warning(
                    "Playwright provider requested but not fully implemented; falling back to HTTP provider",
                    extra={"source_id": provider_config.source_id},
                )
            providers.append(self._build_http_provider(provider_config))
        return providers

    @staticmethod
    def _build_http_provider(provider_config: ProviderConfig) -> SourceProvider:
        if provider_config.source_id == "icetrade.by":
            return IcetradeHttpProvider(provider_config)
        return GoszakupkiHttpProvider(provider_config)

    async def init_database(self) -> None:
        await init_db(self.engine)

    async def shutdown(self) -> None:
        try:
            for provider in self.providers:
                if hasattr(provider, "shutdown"):
                    await getattr(provider, "shutdown")()
        finally:
            if self.semantic_matcher is not None:
                await self.semantic_matcher.close()
            await self.engine.dispose()
            await self.bot.session.close()
