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
    authorized_user_ids: Set[int] = field(default_factory=set)

    async def load(self) -> None:
        # Load multi-chat authorized list
        chats = await self.repo.list_authorized_chats()
        self.authorized_chat_ids = set(chats)
        users = await self.repo.list_authorized_users()
        self.authorized_user_ids = set(users)
        # Migrate legacy single authorized chat if present
        legacy = await self.repo.get_authorized_chat_id()
        if legacy is not None and legacy not in self.authorized_chat_ids:
            await self.repo.add_authorized_chat(legacy)
            self.authorized_chat_ids.add(legacy)
            # Clear legacy slot to avoid future overwrites
            await self.repo.clear_authorized_chat_id()

    async def try_login(self, chat_id: int, login: str, password: str, *, user_id: Optional[int] = None) -> bool:
        if login == self.login and password == self.password:
            if chat_id not in self.authorized_chat_ids:
                self.authorized_chat_ids.add(chat_id)
                await self.repo.add_authorized_chat(chat_id)
            if user_id is not None and user_id not in self.authorized_user_ids:
                self.authorized_user_ids.add(user_id)
                await self.repo.add_authorized_user(user_id)
            return True
        return False

    def is_authorized(self, chat_id: int, *, user_id: Optional[int] = None) -> bool:
        if chat_id in self.authorized_chat_ids:
            return True
        if user_id is not None and user_id in self.authorized_user_ids:
            return True
        return False

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
        self.authorized_user_ids.clear()
        await self.repo.clear_all_authorized_users()
