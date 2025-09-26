from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select, or_, func
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

    async def record_detection(
        self,
        *,
        source_id: str,
        external_id: str,
        title: str | None,
        url: str,
        procedure_type: str | None = None,
        status: str | None = None,
        deadline: str | None = None,
        price: str | None = None,
    ) -> bool:
        async with self._session_factory() as session:
            detection = Detection(
                source_id=source_id,
                external_id=external_id,
                title=title,
                url=url,
                procedure_type=procedure_type,
                status=status,
                deadline=deadline,
                price=price,
                detail_scan_pending=True,
                detail_loaded=False,
            )
            session.add(detection)
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    # --- Детальный скан: выборка и отметки ---

    @dataclass(slots=True)
    class PendingDetail:
        id: int
        source_id: str
        external_id: str
        url: str
        title: str | None
        retry_count: int
        next_retry_at: datetime | None

    async def list_pending_detail(self, *, limit: int = 50) -> list["Repository.PendingDetail"]:
        async with self._session_factory() as session:
            now = datetime.utcnow()
            stmt = (
                select(
                    Detection.id,
                    Detection.source_id,
                    Detection.external_id,
                    Detection.url,
                    Detection.title,
                    Detection.detail_retry_count,
                    Detection.detail_next_retry_at,
                )
                .where(
                    Detection.detail_scan_pending.is_(True),
                    or_(Detection.detail_next_retry_at.is_(None), Detection.detail_next_retry_at <= now),
                )
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
            return [Repository.PendingDetail(*row) for row in rows]

    async def get_next_pending_detail(self) -> "Repository.PendingDetail | None":
        async with self._session_factory() as session:
            now = datetime.utcnow()
            stmt = (
                select(
                    Detection.id,
                    Detection.source_id,
                    Detection.external_id,
                    Detection.url,
                    Detection.title,
                    Detection.detail_retry_count,
                    Detection.detail_next_retry_at,
                )
                .where(
                    Detection.detail_scan_pending.is_(True),
                    or_(Detection.detail_next_retry_at.is_(None), Detection.detail_next_retry_at <= now),
                )
                .order_by(Detection.detail_next_retry_at.is_(None).desc(), Detection.detail_next_retry_at.asc(), Detection.id.asc())
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            return Repository.PendingDetail(*row) if row else None

    async def mark_detail_loaded(self, detection_id: int, success: bool) -> None:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return
            det.detail_loaded = bool(success)
            await session.commit()

    async def complete_detail_scan(self, detection_id: int) -> None:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return
            det.detail_scan_pending = False
            det.detail_scanned_at = datetime.utcnow()
            await session.commit()

    async def schedule_detail_retry(self, detection_id: int, next_retry_at: datetime) -> int:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return 0
            det.detail_retry_count = (getattr(det, "detail_retry_count", 0) or 0) + 1
            det.detail_next_retry_at = next_retry_at
            await session.commit()
            return det.detail_retry_count

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

    # --- Статистика детскана ---
    async def count_pending_detail(self) -> int:
        async with self._session_factory() as session:
            now = datetime.utcnow()
            stmt = (
                select(func.count(Detection.id))
                .where(
                    Detection.detail_scan_pending.is_(True),
                    or_(Detection.detail_next_retry_at.is_(None), Detection.detail_next_retry_at <= now),
                )
            )
            return int(await session.scalar(stmt) or 0)


def _split_keywords(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite: добавить недостающие колонки в detections без миграций
        try:
            result = await conn.exec_driver_sql("PRAGMA table_info('detections')")
            cols = {row[1] for row in result}
            alters: list[str] = []
            if "procedure_type" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN procedure_type TEXT")
            if "status" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN status TEXT")
            if "deadline" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN deadline VARCHAR(64)")
            if "price" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN price VARCHAR(128)")
            if "detail_scan_pending" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN detail_scan_pending BOOLEAN NOT NULL DEFAULT 1")
            if "detail_loaded" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN detail_loaded BOOLEAN NOT NULL DEFAULT 0")
            if "detail_scanned_at" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN detail_scanned_at DATETIME NULL")
            if "detail_retry_count" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN detail_retry_count INTEGER NOT NULL DEFAULT 0")
            if "detail_next_retry_at" not in cols:
                alters.append("ALTER TABLE detections ADD COLUMN detail_next_retry_at DATETIME NULL")
            for sql in alters:
                await conn.exec_driver_sql(sql)
        except Exception:
            # Безопасно игнорируем, если не SQLite или PRAGMA недоступен
            pass
