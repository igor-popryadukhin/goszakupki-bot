from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..db.repo import Repository


@dataclass
class AuthState:
    login: str
    password: str
    repo: Repository
    authorized_chat_id: Optional[int] = None

    async def load(self) -> None:
        # Load persisted chat id (if any)
        chat_id = await self.repo.get_authorized_chat_id()
        if chat_id is not None:
            self.authorized_chat_id = chat_id

    async def try_login(self, chat_id: int, login: str, password: str) -> bool:
        if login == self.login and password == self.password:
            self.authorized_chat_id = chat_id
            await self.repo.set_authorized_chat_id(chat_id)
            return True
        return False

    def is_authorized(self, chat_id: int) -> bool:
        return self.authorized_chat_id == chat_id

    def authorized_target(self) -> Optional[int]:
        return self.authorized_chat_id

    async def logout(self) -> None:
        self.authorized_chat_id = None
        await self.repo.clear_authorized_chat_id()
