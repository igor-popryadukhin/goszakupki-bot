from __future__ import annotations

import asyncio
import logging
import signal
from typing import Awaitable, Callable

LOGGER = logging.getLogger(__name__)


def setup_signal_handlers(shutdown_func: Callable[[], Awaitable[None]]) -> None:
    loop = asyncio.get_event_loop()

    async def handler(sig: signal.Signals) -> None:
        LOGGER.info("Received signal, shutting down", extra={"signal": sig.name})
        await shutdown_func()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(handler(s)))
        except NotImplementedError:  # pragma: no cover - Windows
            signal.signal(sig, lambda *_: asyncio.create_task(handler(sig)))
