from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set

from ..db.repo import Repository


@dataclass
class AuthState:
    login: str
    password: str
    repo: Repository
    authorized_chat_ids: Set[int] = field(default_factory=set)

    async def load(self) -> None:
        # Load multi-chat authorized list
        chats = await self.repo.list_authorized_chats()
        self.authorized_chat_ids = set(chats)
        # Migrate legacy single authorized chat if present
        legacy = await self.repo.get_authorized_chat_id()
        if legacy is not None and legacy not in self.authorized_chat_ids:
            await self.repo.add_authorized_chat(legacy)
            self.authorized_chat_ids.add(legacy)
            # Clear legacy slot to avoid future overwrites
            await self.repo.clear_authorized_chat_id()

    async def try_login(self, chat_id: int, login: str, password: str) -> bool:
        if login == self.login and password == self.password:
            if chat_id not in self.authorized_chat_ids:
                self.authorized_chat_ids.add(chat_id)
                await self.repo.add_authorized_chat(chat_id)
            return True
        return False

    def is_authorized(self, chat_id: int) -> bool:
        return chat_id in self.authorized_chat_ids

    def authorized_target(self) -> Optional[int]:
        # Backward-compatible single target (first in set if any)
        try:
            return next(iter(self.authorized_chat_ids))
        except StopIteration:
            return None

    def authorized_targets(self) -> list[int]:
        return sorted(self.authorized_chat_ids)

    async def logout(self) -> None:
        # Clear all authorizations
        self.authorized_chat_ids.clear()
        await self.repo.clear_all_authorized_chats()
