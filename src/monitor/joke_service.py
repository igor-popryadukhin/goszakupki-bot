from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from ..db.repo import Repository
from .jokes import DeepSeekJokeGenerator

LOGGER = logging.getLogger(__name__)


class JokeService:
    def __init__(
        self,
        *,
        generator: DeepSeekJokeGenerator,
        repository: Repository,
        bot: Bot,
        auth_state: "AuthState",
    ) -> None:
        self._generator = generator
        self._repo = repository
        self._bot = bot
        self._lock = asyncio.Lock()
        self._auth_state = auth_state

    async def run_send(self) -> None:
        async with self._lock:
            try:
                await self._run_send()
            except Exception:  # pragma: no cover
                LOGGER.exception("Error during joke send")

    async def _run_send(self) -> None:
        if not await self._repo.is_enabled():
            LOGGER.debug("Skip joke send: disabled")
            return
        joke = await self._generator.generate_joke()
        if not joke:
            LOGGER.debug("Skip joke send: empty response")
            return
        targets_getter = getattr(self._auth_state, "all_targets", None)
        if callable(targets_getter):
            targets = list(targets_getter())
        else:
            targets = getattr(self._auth_state, "authorized_targets", lambda: [])()
        if not targets:
            LOGGER.debug("Joke skip: no authorized chats in session")
            return
        sent = 0
        for chat_id in targets:
            try:
                await self._bot.send_message(chat_id=chat_id, text=joke, disable_web_page_preview=True)
                sent += 1
            except Exception:
                LOGGER.exception("Failed to send joke", extra={"chat_id": chat_id})
        LOGGER.info("Joke send done", extra={"sent": sent})

    async def send_to_chat(self, chat_id: int) -> bool:
        joke = await self._generator.generate_joke()
        if not joke:
            return False
        try:
            await self._bot.send_message(chat_id=chat_id, text=joke, disable_web_page_preview=True)
            return True
        except Exception:
            LOGGER.exception("Failed to send joke", extra={"chat_id": chat_id})
            return False
