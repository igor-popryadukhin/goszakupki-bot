from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import select, or_, func, delete
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import Base, ChatSettings, Detection, Notification, User, AuthorizedChat, AppSettings


@dataclass(slots=True)
class AppPreferences:
    keywords: list[str]
    interval_seconds: int
    pages: int
    enabled: bool


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_or_create_settings(self, *, default_interval: int, default_pages: int) -> AppPreferences:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                settings = AppSettings(
                    keywords="",
                    interval_seconds=default_interval,
                    pages=default_pages,
                    enabled=False,
                )
                session.add(settings)
                await session.commit()
            return AppPreferences(
                keywords=_split_keywords(settings.keywords),
                interval_seconds=settings.interval_seconds,
                pages=settings.pages,
                enabled=settings.enabled,
            )

    async def update_keywords(self, keywords: Iterable[str]) -> None:
        normalized = "\n".join(k.strip() for k in keywords if k.strip())
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.keywords = normalized
            await session.commit()

    async def set_interval(self, interval_seconds: int) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.interval_seconds = interval_seconds
            await session.commit()

    async def set_pages(self, pages: int) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.pages = pages
            await session.commit()

    async def set_enabled(self, enabled: bool) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.enabled = enabled
            await session.commit()

    async def get_preferences(self) -> AppPreferences | None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                return None
            return AppPreferences(
                keywords=_split_keywords(settings.keywords),
                interval_seconds=settings.interval_seconds,
                pages=settings.pages,
                enabled=settings.enabled,
            )

    async def is_enabled(self) -> bool:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            return bool(settings and settings.enabled)

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
            session.add(Notification(chat_id=chat_id, source_id=source_id, external_id=external_id, sent=True))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    # Global notifications (chat_id = 0)
    async def has_notification_global(self, source_id: str, external_id: str) -> bool:
        async with self._session_factory() as session:
            stmt = select(Notification.id).where(
                Notification.chat_id == 0,
                Notification.source_id == source_id,
                Notification.external_id == external_id,
            )
            return (await session.scalar(stmt)) is not None

    async def create_notification_global(self, source_id: str, external_id: str, *, sent: bool) -> None:
        async with self._session_factory() as session:
            session.add(Notification(chat_id=0, source_id=source_id, external_id=external_id, sent=bool(sent)))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def seed_notifications_global_for_existing(self, source_id: str, *, limit: int | None = None) -> int:
        """Mark existing detections as already notified for the chat to avoid floods on enable.

        Returns number of created notifications.
        """
        async with self._session_factory() as session:
            # Select external_ids for which there is no notification yet
            now = datetime.utcnow()  # timestamp is implicit in Notification
            stmt = (
                select(Detection.external_id)
                .outerjoin(
                    Notification,
                    and_(
                        Notification.chat_id == 0,
                        Notification.source_id == Detection.source_id,
                        Notification.external_id == Detection.external_id,
                    ),
                )
                .where(Detection.source_id == source_id, Notification.id.is_(None))
            )
            if limit:
                stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return 0
            created = 0
            for ext_id in rows:
                session.add(Notification(chat_id=0, source_id=source_id, external_id=ext_id, sent=False))
                created += 1
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            return created

    # --- Авторизация чатов ---

    async def is_authorized(self, chat_id: int) -> bool:
        async with self._session_factory() as session:
            stmt = select(AuthorizedChat.id).where(AuthorizedChat.chat_id == chat_id)
            return (await session.scalar(stmt)) is not None

    async def authorize_chat(self, chat_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(AuthorizedChat).where(AuthorizedChat.chat_id == chat_id))
            if row is None:
                session.add(AuthorizedChat(chat_id=chat_id))
            await session.commit()

    async def list_authorized_chat_ids(self) -> list[int]:
        async with self._session_factory() as session:
            ids = (await session.execute(select(AuthorizedChat.chat_id))).scalars().all()
            return list(ids)

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

    async def clear_detections(self, *, source_id: str | None = None) -> int:
        async with self._session_factory() as session:
            if source_id:
                stmt = delete(Detection).where(Detection.source_id == source_id)
            else:
                stmt = delete(Detection)
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    # --- Статистика по detections/notifications ---
    async def count_detections(self, *, source_id: str | None = None, since: datetime | None = None) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count(Detection.id))
            if source_id:
                stmt = stmt.where(Detection.source_id == source_id)
            if since:
                stmt = stmt.where(Detection.first_seen >= since)
            return int(await session.scalar(stmt) or 0)

    async def last_detection_time(self, *, source_id: str | None = None) -> datetime | None:
        async with self._session_factory() as session:
            stmt = select(func.max(Detection.first_seen))
            if source_id:
                stmt = stmt.where(Detection.source_id == source_id)
            return await session.scalar(stmt)

    async def count_notifications_global(self, *, source_id: str | None = None, since: datetime | None = None) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count(Notification.id)).where(Notification.chat_id == 0, Notification.sent.is_(True))
            if source_id:
                stmt = stmt.where(Notification.source_id == source_id)
            if since:
                stmt = stmt.where(Notification.notified_at >= since)
            return int(await session.scalar(stmt) or 0)

    async def last_notification_time_global(self, *, source_id: str | None = None) -> datetime | None:
        async with self._session_factory() as session:
            stmt = select(func.max(Notification.notified_at)).where(Notification.chat_id == 0, Notification.sent.is_(True))
            if source_id:
                stmt = stmt.where(Notification.source_id == source_id)
            return await session.scalar(stmt)


def _split_keywords(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite: добавить недостающие колонки без миграций
        # detections
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
            pass
        # notifications
        try:
            result = await conn.exec_driver_sql("PRAGMA table_info('notifications')")
            ncols = {row[1] for row in result}
            if "sent" not in ncols:
                await conn.exec_driver_sql("ALTER TABLE notifications ADD COLUMN sent BOOLEAN NOT NULL DEFAULT 0")
        except Exception:
            pass
