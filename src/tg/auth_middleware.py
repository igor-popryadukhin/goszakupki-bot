from __future__ import annotations

from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from ..config import AppConfig
from ..db.repo import Repository


class AuthMiddleware(BaseMiddleware):
    """Blocks all updates except /start, /help and /login when auth is enabled
    and chat is not authorized.
    """

    def __init__(self, repo: Repository, auth: AppConfig.AuthConfig) -> None:
        super().__init__()
        self._repo = repo
        self._auth = auth

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        if not self._auth.enabled:
            return await handler(event, data)

        chat_id = None
        text = ""
        if isinstance(event, Message):
            chat_id = event.chat.id
            text = (event.text or "").strip()
        elif isinstance(event, CallbackQuery):
            if event.message:
                chat_id = event.message.chat.id
            text = (event.data or "").strip()

        # Allow only auth-related commands before login
        if isinstance(event, Message):
            if text.startswith("/login") or text.startswith("/start") or text.startswith("/help"):
                return await handler(event, data)
        if isinstance(event, CallbackQuery):
            # Block all callbacks until authorized
            pass

        if chat_id is None:
            return await handler(event, data)

        if await self._repo.is_authorized(chat_id):
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer("Доступ к боту ограничен. Выполните авторизацию: /login <логин> <пароль>")
        elif isinstance(event, CallbackQuery):
            await event.answer("Авторизуйтесь: /login <логин> <пароль>", show_alert=True)
        return None

