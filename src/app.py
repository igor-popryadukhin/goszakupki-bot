from __future__ import annotations

import asyncio
import logging

from aiogram import Dispatcher

from .config import load_config
from .di import Container
from .logging_config import configure_logging
from .tg.handlers import create_router
from .tg.auth_middleware import AuthMiddleware
from .util.signals import setup_signal_handlers

LOGGER = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    configure_logging(config.logging.level)
    container = Container(config)
    await container.init_database()
    if hasattr(container.provider, "startup"):
        await getattr(container.provider, "startup")()

    router = create_router(
        container.repository,
        container.scheduler,
        container.detail_scheduler,
        container.detail_service,
        config.provider,
        config.auth,
        container.auth_state,
    )
    dispatcher: Dispatcher = container.dispatcher
    # Глобальная проверка авторизации
    dispatcher.message.outer_middleware(AuthMiddleware(config.auth, container.auth_state))
    dispatcher.callback_query.outer_middleware(AuthMiddleware(config.auth, container.auth_state))
    dispatcher.include_router(router)

    shutdown_called = False

    async def shutdown() -> None:
        nonlocal shutdown_called
        if shutdown_called:
            return
        shutdown_called = True
        try:
            dispatcher.stop_polling()
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("Failed to stop polling")
        try:
            await dispatcher.storage.close()
        except Exception:  # pragma: no cover
            LOGGER.exception("Failed to close dispatcher storage")
        await container.scheduler.shutdown()
        await container.detail_scheduler.shutdown()
        await container.shutdown()

    setup_signal_handlers(shutdown)

    await container.scheduler.start()
    await container.detail_scheduler.start()

    try:
        await dispatcher.start_polling(container.bot)
    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
