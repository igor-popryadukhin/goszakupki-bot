from __future__ import annotations

from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from ..config import AppConfig
from .auth_state import AuthState


class AuthMiddleware(BaseMiddleware):
    """Blocks all updates except /start, /help and /login when auth is enabled
    and chat is not authorized.
    """

    def __init__(self, auth: AppConfig.AuthConfig, state: AuthState) -> None:
        super().__init__()
        self._auth = auth
        self._state = state

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        # If auth is not configured, allow everything
        if not self._auth.enabled:
            return await handler(event, data)

        # Authorization is mandatory: block everything until /login succeeds
        
        chat_id = None
        text = ""
        user_id = None
        if isinstance(event, Message):
            chat_id = event.chat.id
            text = (event.text or "").strip()
            if event.from_user:
                user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            if event.message:
                chat_id = event.message.chat.id
            text = (event.data or "").strip()
            if event.from_user:
                user_id = event.from_user.id

        # Allow only auth-related commands before login
        if isinstance(event, Message):
            # Allow login/help/start commands
            if text.startswith("/login") or text.startswith("/start") or text.startswith("/help"):
                return await handler(event, data)
            # Allow messages while in login wizard states
            state: FSMContext | None = data.get("state")
            if state is not None:
                st = await state.get_state()
                if st and "LoginForm" in st:
                    return await handler(event, data)
        if isinstance(event, CallbackQuery):
            # Block all callbacks until authorized
            if text == "cancel_login":
                return await handler(event, data)

        if chat_id is None:
            return await handler(event, data)

        if self._state.is_authorized(chat_id, user_id=user_id):
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer("Доступ к боту ограничен. Выполните авторизацию: /login <логин> <пароль>")
        elif isinstance(event, CallbackQuery):
            await event.answer("Авторизуйтесь: /login <логин> <пароль>", show_alert=True)
        return None
