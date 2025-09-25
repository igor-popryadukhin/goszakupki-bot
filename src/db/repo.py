from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import Base, ChatSettings, Detection, Notification, User


@dataclass(slots=True)
class ChatPreferences:
    chat_id: int
    username: str | None
    keywords: list[str]
    interval_seconds: int
    pages: int
    enabled: bool


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_user(self, chat_id: int, username: str | None, *, default_interval: int, default_pages: int) -> ChatPreferences:
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.chat_id == chat_id))
            if user is None:
                user = User(chat_id=chat_id, username=username)
                session.add(user)
                await session.flush()
                settings = ChatSettings(
                    user_id=user.id,
                    keywords="",
                    interval_seconds=default_interval,
                    pages=default_pages,
                    enabled=False,
                )
                session.add(settings)
            else:
                if username and user.username != username:
                    user.username = username
                settings = await session.scalar(select(ChatSettings).where(ChatSettings.user_id == user.id))
                if settings is None:
                    settings = ChatSettings(
                        user_id=user.id,
                        keywords="",
                        interval_seconds=default_interval,
                        pages=default_pages,
                        enabled=False,
                    )
                    session.add(settings)
            await session.commit()
            return ChatPreferences(
                chat_id=chat_id,
                username=user.username,
                keywords=_split_keywords(settings.keywords),
                interval_seconds=settings.interval_seconds,
                pages=settings.pages,
                enabled=settings.enabled,
            )

    async def update_keywords(self, chat_id: int, keywords: Iterable[str]) -> None:
        normalized = "\n".join(k.strip() for k in keywords if k.strip())
        async with self._session_factory() as session:
            settings = await self._get_settings_for_chat(session, chat_id)
            settings.keywords = normalized
            await session.commit()

    async def set_interval(self, chat_id: int, interval_seconds: int) -> None:
        async with self._session_factory() as session:
            settings = await self._get_settings_for_chat(session, chat_id)
            settings.interval_seconds = interval_seconds
            await session.commit()

    async def set_pages(self, chat_id: int, pages: int) -> None:
        async with self._session_factory() as session:
            settings = await self._get_settings_for_chat(session, chat_id)
            settings.pages = pages
            await session.commit()

    async def set_enabled(self, chat_id: int, enabled: bool) -> None:
        async with self._session_factory() as session:
            settings = await self._get_settings_for_chat(session, chat_id)
            settings.enabled = enabled
            await session.commit()

    async def get_preferences(self, chat_id: int) -> ChatPreferences | None:
        async with self._session_factory() as session:
            stmt = (
                select(User, ChatSettings)
                .join(ChatSettings, ChatSettings.user_id == User.id)
                .where(User.chat_id == chat_id)
            )
            result = await session.execute(stmt)
            row = result.first()
            if not row:
                return None
            user, settings = row
            return ChatPreferences(
                chat_id=user.chat_id,
                username=user.username,
                keywords=_split_keywords(settings.keywords),
                interval_seconds=settings.interval_seconds,
                pages=settings.pages,
                enabled=settings.enabled,
            )

    async def list_enabled_preferences(self) -> list[ChatPreferences]:
        async with self._session_factory() as session:
            stmt = (
                select(User, ChatSettings)
                .join(ChatSettings, ChatSettings.user_id == User.id)
                .where(ChatSettings.enabled.is_(True))
            )
            rows = (await session.execute(stmt)).all()
            prefs: list[ChatPreferences] = []
            for user, settings in rows:
                prefs.append(
                    ChatPreferences(
                        chat_id=user.chat_id,
                        username=user.username,
                        keywords=_split_keywords(settings.keywords),
                        interval_seconds=settings.interval_seconds,
                        pages=settings.pages,
                        enabled=settings.enabled,
                    )
                )
            return prefs

    async def record_detection(self, *, source_id: str, external_id: str, title: str | None, url: str) -> bool:
        async with self._session_factory() as session:
            detection = Detection(source_id=source_id, external_id=external_id, title=title, url=url)
            session.add(detection)
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def has_notification(self, chat_id: int, source_id: str, external_id: str) -> bool:
        async with self._session_factory() as session:
            stmt = select(Notification.id).where(
                Notification.chat_id == chat_id,
                Notification.source_id == source_id,
                Notification.external_id == external_id,
            )
            return (await session.scalar(stmt)) is not None

    async def create_notification(self, chat_id: int, source_id: str, external_id: str) -> None:
        async with self._session_factory() as session:
            session.add(Notification(chat_id=chat_id, source_id=source_id, external_id=external_id))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def _get_settings_for_chat(self, session: AsyncSession, chat_id: int) -> ChatSettings:
        stmt = (
            select(ChatSettings)
            .join(User, User.id == ChatSettings.user_id)
            .where(User.chat_id == chat_id)
        )
        settings = await session.scalar(stmt)
        if settings is None:
            raise ValueError(f"Chat {chat_id} is not registered")
        return settings


def _split_keywords(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
