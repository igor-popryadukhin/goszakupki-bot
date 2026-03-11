from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import AppConfig, ProviderConfig
from .db.repo import Repository, init_db
from .monitor.analysis_pipeline import AnalysisPipeline
from .monitor.scheduler import MonitorScheduler
from .monitor.detail_service import DetailScanService
from .monitor.embedding_service import EmbeddingService
from .monitor.keyword_registry import KeywordRegistry
from .monitor.detail_scheduler import DetailScanScheduler
from .monitor.jokes import DeepSeekJokeGenerator
from .monitor.joke_service import JokeService
from .monitor.joke_scheduler import JokeScheduler
from .monitor.llm_resolver import LlmResolver
from .monitor.rules_matcher import RulesMatcher
from .monitor.semantic_matcher import SemanticMatcher
from .monitor.service import MonitorService
from .monitor.text_normalizer import TextNormalizer
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
        self.keyword_registry = KeywordRegistry(self.repository)
        self.embedding_service = EmbeddingService(config=config.ollama, repository=self.repository)
        self.rules_matcher = RulesMatcher()
        self.semantic_matcher = SemanticMatcher(
            repository=self.repository,
            embedding_service=self.embedding_service,
            ollama_config=config.ollama,
            analysis_config=config.analysis,
        )
        self.analysis_pipeline = AnalysisPipeline(
            repository=self.repository,
            text_normalizer=TextNormalizer(),
            keyword_registry=self.keyword_registry,
            rules_matcher=self.rules_matcher,
            semantic_matcher=self.semantic_matcher,
            llm_resolver=LlmResolver(),
            config=config.analysis,
        )
        if config.deepseek.enabled and config.deepseek.api_key:
            self.joke_generator = DeepSeekJokeGenerator(config.deepseek)
        else:
            self.joke_generator = None
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
            analysis_pipeline=self.analysis_pipeline,
        )
        self.detail_scheduler = DetailScanScheduler(
            service=self.detail_service,
            repository=self.repository,
            provider_configs=config.providers,
            logging_config=config.logging,
        )
        if self.joke_generator is not None:
            self.joke_service = JokeService(
                generator=self.joke_generator,
                repository=self.repository,
                bot=self.bot,
                auth_state=self.auth_state,
            )
            self.joke_scheduler = JokeScheduler(
                service=self.joke_service,
                repository=self.repository,
                logging_config=config.logging,
            )
        else:
            self.joke_service = None
            self.joke_scheduler = None

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
        prefs = await self.repository.get_or_create_settings(
            default_interval=min(cfg.check_interval_default for cfg in self.config.providers),
            default_pages=min(cfg.pages_default for cfg in self.config.providers),
        )
        if not prefs.embedding_model:
            await self.repository.set_embedding_model(self.config.ollama.embedding_model)
            prefs = await self.repository.get_preferences() or prefs
        await self.repository.sync_keyword_registry()
        await self.repository.requeue_outdated_analyses(analysis_version=self.config.analysis.analysis_version)
        LOGGER.info(
            "Analysis pipeline initialized",
            extra={
                "keyword_version": prefs.keyword_version,
                "analysis_version": self.config.analysis.analysis_version,
                "embedding_model": prefs.embedding_model or self.config.ollama.embedding_model,
            },
        )

    async def shutdown(self) -> None:
        try:
            for provider in self.providers:
                if hasattr(provider, "shutdown"):
                    await getattr(provider, "shutdown")()
        finally:
            await self.embedding_service.close()
            if self.joke_generator is not None:
                await self.joke_generator.close()
            await self.engine.dispose()
            await self.bot.session.close()
