from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

from ..db.repo import Repository


@dataclass
class AuthState:
    login: str
    password: str
    repo: Repository
    authorized_chat_id: Optional[int] = None
    _authorized: bool = False
    _seen_chats: Set[int] = field(default_factory=set)

    async def load(self) -> None:
        # Load persisted chat id (if any)
        chat_id = await self.repo.get_authorized_chat_id()
        if chat_id is not None:
            self._authorized = True
            self.authorized_chat_id = chat_id

    async def try_login(self, chat_id: int, login: str, password: str) -> bool:
        if login == self.login and password == self.password:
            self._authorized = True
            self.authorized_chat_id = chat_id
            self._seen_chats.add(chat_id)
            await self.repo.set_authorized_chat_id(chat_id)
            return True
        return False

    def is_authorized(self, chat_id: int) -> bool:
        return self._authorized and self.authorized_chat_id == chat_id

    def authorized_target(self) -> Optional[int]:
        return self.authorized_chat_id if self._authorized else None

    async def logout(self) -> None:
        self._authorized = False
        self.authorized_chat_id = None
        await self.repo.clear_authorized_chat_id()
