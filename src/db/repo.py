from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import and_
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import (
    AnalysisMatch,
    AppSettings,
    AuthSession,
    AuthorizedChat,
    AuthorizedUser,
    Base,
    Detection,
    EmbeddingCache,
    KeywordEmbedding,
    KeywordEntry,
    Notification,
)


class RepositoryError(Exception):
    """Base class for repository-level errors."""


class StorageFullError(RepositoryError):
    """Raised when the database cannot commit changes due to disk exhaustion."""


@dataclass(slots=True)
class AppPreferences:
    keywords: list[str]
    interval_seconds: int
    pages: int
    enabled: bool
    keyword_version: int


@dataclass(slots=True)
class KeywordRecord:
    id: int
    source_phrase: str
    normalized_phrase: str
    synonyms: list[str]
    negative_contexts: list[str]
    weight: float
    version: int


@dataclass(slots=True)
class EmbeddingRecord:
    id: int
    cache_key: str
    model: str
    text_hash: str
    vector: list[float]


@dataclass(slots=True)
class KeywordEmbeddingRecord:
    keyword_id: int
    model: str
    vector: list[float]


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
                    keyword_version=1,
                )
                session.add(settings)
                await _commit(session)
            return AppPreferences(
                keywords=_split_keywords(settings.keywords),
                interval_seconds=settings.interval_seconds,
                pages=settings.pages,
                enabled=settings.enabled,
                keyword_version=getattr(settings, "keyword_version", 1) or 1,
            )

    async def update_keywords(self, keywords: Iterable[str]) -> None:
        items = [k.strip() for k in keywords if k.strip()]
        normalized = "\n".join(items)
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.keywords = normalized
            settings.keyword_version = (getattr(settings, "keyword_version", 1) or 1) + 1
            await self._replace_keyword_entries(session, items, settings.keyword_version)
            await _commit(session)

    async def add_keyword(self, keyword: str) -> bool:
        k = (keyword or "").strip()
        if not k:
            return False
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            items = _split_keywords(settings.keywords)
            # prevent duplicates (case-insensitive)
            low = {s.casefold() for s in items}
            if k.casefold() in low:
                return False
            items.append(k)
            settings.keywords = "\n".join(items)
            settings.keyword_version = (getattr(settings, "keyword_version", 1) or 1) + 1
            await self._replace_keyword_entries(session, items, settings.keyword_version)
            await _commit(session)
            return True

    async def remove_keyword(self, keyword: str) -> bool:
        k = (keyword or "").strip()
        if not k:
            return False
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            items = _split_keywords(settings.keywords)
            before = len(items)
            items = [s for s in items if s.casefold() != k.casefold()]
            if len(items) == before:
                return False
            settings.keywords = "\n".join(items)
            settings.keyword_version = (getattr(settings, "keyword_version", 1) or 1) + 1
            await self._replace_keyword_entries(session, items, settings.keyword_version)
            await _commit(session)
            return True

    async def clear_keywords(self) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.keywords = ""
            settings.keyword_version = (getattr(settings, "keyword_version", 1) or 1) + 1
            await self._replace_keyword_entries(session, [], settings.keyword_version)
            await _commit(session)

    async def set_interval(self, interval_seconds: int) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.interval_seconds = interval_seconds
            await _commit(session)

    async def set_pages(self, pages: int) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.pages = pages
            await _commit(session)

    async def set_enabled(self, enabled: bool) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.enabled = enabled
            await _commit(session)

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
                keyword_version=getattr(settings, "keyword_version", 1) or 1,
            )

    async def is_enabled(self) -> bool:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            return bool(settings and settings.enabled)

    async def sync_keyword_registry(self) -> int:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            version = getattr(settings, "keyword_version", 1) or 1
            await self._replace_keyword_entries(session, _split_keywords(settings.keywords), version)
            await _commit(session)
            return version

    async def list_active_keyword_records(self) -> list[KeywordRecord]:
        async with self._session_factory() as session:
            stmt = (
                select(KeywordEntry)
                .where(KeywordEntry.is_active.is_(True))
                .order_by(KeywordEntry.normalized_phrase.asc(), KeywordEntry.id.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [
                KeywordRecord(
                    id=row.id,
                    source_phrase=row.source_phrase,
                    normalized_phrase=row.normalized_phrase,
                    synonyms=_loads_json_list(row.synonyms_json),
                    negative_contexts=_loads_json_list(row.negative_contexts_json),
                    weight=row.weight,
                    version=row.version,
                )
                for row in rows
            ]

    async def get_embedding_cache(self, *, cache_key: str) -> EmbeddingRecord | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(EmbeddingCache).where(EmbeddingCache.cache_key == cache_key))
            if row is None:
                return None
            return EmbeddingRecord(
                id=row.id,
                cache_key=row.cache_key,
                model=row.model,
                text_hash=row.text_hash,
                vector=_loads_json_vector(row.vector_json),
            )

    async def upsert_embedding_cache(
        self,
        *,
        cache_key: str,
        model: str,
        text_hash: str,
        text_preview: str | None,
        vector: list[float],
    ) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(EmbeddingCache).where(EmbeddingCache.cache_key == cache_key))
            payload = json.dumps(vector)
            if row is None:
                row = EmbeddingCache(
                    cache_key=cache_key,
                    model=model,
                    text_hash=text_hash,
                    text_preview=text_preview,
                    vector_json=payload,
                )
                session.add(row)
            else:
                row.model = model
                row.text_hash = text_hash
                row.text_preview = text_preview
                row.vector_json = payload
                row.updated_at = datetime.utcnow()
            await _commit(session)

    async def get_keyword_embedding(self, *, keyword_id: int, model: str) -> KeywordEmbeddingRecord | None:
        async with self._session_factory() as session:
            stmt = select(KeywordEmbedding).where(
                KeywordEmbedding.keyword_id == keyword_id,
                KeywordEmbedding.model == model,
            )
            row = await session.scalar(stmt)
            if row is None:
                return None
            return KeywordEmbeddingRecord(
                keyword_id=row.keyword_id,
                model=row.model,
                vector=_loads_json_vector(row.vector_json),
            )

    async def upsert_keyword_embedding(self, *, keyword_id: int, model: str, vector: list[float]) -> None:
        async with self._session_factory() as session:
            stmt = select(KeywordEmbedding).where(
                KeywordEmbedding.keyword_id == keyword_id,
                KeywordEmbedding.model == model,
            )
            row = await session.scalar(stmt)
            payload = json.dumps(vector)
            if row is None:
                row = KeywordEmbedding(keyword_id=keyword_id, model=model, vector_json=payload)
                session.add(row)
            else:
                row.vector_json = payload
                row.updated_at = datetime.utcnow()
            await _commit(session)

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
            except OperationalError as exc:
                await session.rollback()
                _raise_storage_full(exc)
                raise

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
            await _commit(session)

    async def save_detail_content(
        self,
        detection_id: int,
        *,
        raw_text: str,
        normalized_text: str,
    ) -> None:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return
            det.detail_loaded = True
            det.detail_text_raw = raw_text
            det.normalized_text = normalized_text
            await _commit(session)

    async def save_analysis_result(
        self,
        detection_id: int,
        *,
        status: str,
        analysis_version: int,
        is_relevant: bool,
        confidence: float | None,
        summary: str | None,
        explanation: str | None,
        decision_source: str | None,
        needs_review: bool,
        matches: list[dict[str, object]],
    ) -> None:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return
            det.analysis_status = status
            det.analysis_version = analysis_version
            det.analysis_confidence = confidence
            det.analysis_summary = summary
            det.analysis_explanation = explanation
            det.analysis_decision_source = decision_source
            det.analysis_needs_review = needs_review
            det.analysis_completed_at = datetime.utcnow()
            await session.execute(delete(AnalysisMatch).where(AnalysisMatch.detection_id == detection_id))
            for index, match in enumerate(matches, start=1):
                session.add(
                    AnalysisMatch(
                        detection_id=detection_id,
                        keyword_id=_to_optional_int(match.get("keyword_id")),
                        matched_text=str(match.get("matched_text") or ""),
                        match_type=str(match.get("match_type") or ""),
                        score=_to_optional_float(match.get("score")),
                        reason=str(match.get("reason") or "") or None,
                        rank=index,
                    )
                )
            if not is_relevant and det.analysis_status == "completed":
                det.analysis_needs_review = needs_review
            await _commit(session)

    async def complete_detail_scan(self, detection_id: int) -> None:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return
            det.detail_scan_pending = False
            det.detail_scanned_at = datetime.utcnow()
            await _commit(session)

    async def schedule_detail_retry(self, detection_id: int, next_retry_at: datetime) -> int:
        async with self._session_factory() as session:
            det = await session.get(Detection, detection_id)
            if det is None:
                return 0
            det.detail_retry_count = (getattr(det, "detail_retry_count", 0) or 0) + 1
            det.detail_next_retry_at = next_retry_at
            await _commit(session)
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
            except OperationalError as exc:
                await session.rollback()
                _raise_storage_full(exc)
                raise

    # Global notifications (chat_id = 0)
    async def has_notification_global(self, source_id: str, external_id: str) -> bool:
        async with self._session_factory() as session:
            stmt = select(Notification.id).where(
                Notification.chat_id == 0,
                Notification.source_id == source_id,
                Notification.external_id == external_id,
            )
            return (await session.scalar(stmt)) is not None

    async def has_notification_global_sent(self, source_id: str, external_id: str) -> bool:
        async with self._session_factory() as session:
            stmt = select(Notification.id).where(
                Notification.chat_id == 0,
                Notification.source_id == source_id,
                Notification.external_id == external_id,
                Notification.sent.is_(True),
            )
            return (await session.scalar(stmt)) is not None

    async def create_notification_global(self, source_id: str, external_id: str, *, sent: bool) -> None:
        async with self._session_factory() as session:
            session.add(Notification(chat_id=0, source_id=source_id, external_id=external_id, sent=bool(sent)))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            except OperationalError as exc:
                await session.rollback()
                _raise_storage_full(exc)
                raise

    # --- Persisted auth session (single-user global auth) ---

    # --- Authorization (multi-chat). Legacy single-row helpers retained for migration ---

    async def get_authorized_chat_id(self) -> int | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.id == 1))
            return int(row.chat_id) if row and row.chat_id is not None else None

    async def set_authorized_chat_id(self, chat_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.id == 1))
            if row is None:
                row = AuthSession(id=1, chat_id=chat_id)
                session.add(row)
            else:
                row.chat_id = chat_id
            await _commit(session)

    async def clear_authorized_chat_id(self) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.id == 1))
            if row is not None:
                row.chat_id = None
                await _commit(session)

    # New multi-chat API
    async def list_authorized_chats(self) -> list[int]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(AuthorizedChat.chat_id))).scalars().all()
            return [int(r) for r in rows]

    async def add_authorized_chat(self, chat_id: int) -> None:
        async with self._session_factory() as session:
            if await session.get(AuthorizedChat, chat_id) is None:
                session.add(AuthorizedChat(chat_id=chat_id))
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                except OperationalError as exc:
                    await session.rollback()
                    _raise_storage_full(exc)
                    raise

    async def remove_authorized_chat(self, chat_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(AuthorizedChat, chat_id)
            if row is not None:
                await session.delete(row)
                await _commit(session)

    async def clear_all_authorized_chats(self) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(AuthorizedChat))
            await _commit(session)

    # Authorized users (by Telegram user_id)
    async def list_authorized_users(self) -> list[int]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(AuthorizedUser.user_id))).scalars().all()
            return [int(r) for r in rows]

    async def add_authorized_user(self, user_id: int) -> None:
        async with self._session_factory() as session:
            if await session.get(AuthorizedUser, user_id) is None:
                session.add(AuthorizedUser(user_id=user_id))
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                except OperationalError as exc:
                    await session.rollback()
                    _raise_storage_full(exc)
                    raise

    async def remove_authorized_user(self, user_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(AuthorizedUser, user_id)
            if row is not None:
                await session.delete(row)
                await _commit(session)

    async def clear_all_authorized_users(self) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(AuthorizedUser))
            await _commit(session)

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
            except OperationalError as exc:
                await session.rollback()
                _raise_storage_full(exc)
                raise
            return created

    async def requeue_outdated_analyses(self, *, analysis_version: int) -> int:
        async with self._session_factory() as session:
            stmt = (
                update(Detection)
                .where(
                    or_(
                        Detection.analysis_version < analysis_version,
                        Detection.analysis_status.in_(("failed", "pending")),
                    )
                )
                .values(
                    detail_scan_pending=True,
                    analysis_status="pending",
                    analysis_needs_review=False,
                    detail_next_retry_at=None,
                )
            )
            result = await session.execute(stmt)
            await _commit(session)
            return int(getattr(result, "rowcount", 0) or 0)

    # Authorization persistence removed; handled in-memory for this session

    # No persistent list of authorized chats in DB

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
            await _commit(session)
            return int(getattr(result, "rowcount", 0) or 0)

    async def list_detections(
        self,
        *,
        since: datetime,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[str, str, str | None, str, datetime]]:
        async with self._session_factory() as session:
            stmt = (
                select(
                    Detection.source_id,
                    Detection.external_id,
                    Detection.title,
                    Detection.url,
                    Detection.first_seen,
                )
                .where(Detection.first_seen >= since)
                .order_by(Detection.first_seen.desc(), Detection.id.desc())
                .limit(limit)
                .offset(offset)
            )
            if source_id:
                stmt = stmt.where(Detection.source_id == source_id)
            rows = (await session.execute(stmt)).all()
            return [
                (row[0], row[1], row[2], row[3], row[4])  # type: ignore[misc]
                for row in rows
            ]

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

    async def _replace_keyword_entries(
        self,
        session: AsyncSession,
        items: list[str],
        version: int,
    ) -> None:
        rows = (await session.execute(select(KeywordEntry))).scalars().all()
        existing = {row.normalized_phrase: row for row in rows}
        seen: set[str] = set()
        active_normalized: set[str] = set()
        for raw in items:
            normalized = _normalize_keyword_phrase(raw)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            active_normalized.add(normalized)
            row = existing.get(normalized)
            if row is None:
                session.add(
                    KeywordEntry(
                        source_phrase=raw,
                        normalized_phrase=normalized,
                        synonyms_json="[]",
                        negative_contexts_json="[]",
                        weight=1.0,
                        version=version,
                        is_active=True,
                    )
                )
                continue
            row.source_phrase = raw
            row.version = version
            row.is_active = True
            row.updated_at = datetime.utcnow()

        for normalized, row in existing.items():
            if normalized in active_normalized:
                continue
            await session.execute(delete(KeywordEmbedding).where(KeywordEmbedding.keyword_id == row.id))
            await session.delete(row)


def _raise_storage_full(exc: OperationalError) -> None:
    if _is_storage_full_error(exc):
        raise StorageFullError("Database storage is full") from exc


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except OperationalError as exc:
        await session.rollback()
        _raise_storage_full(exc)
        raise


def _is_storage_full_error(exc: OperationalError) -> bool:
    patterns = (
        "database or disk is full",
        "database is full",
        "disk full",
        "sqlite_full",
    )

    def _has_pattern(value: object) -> bool:
        if isinstance(value, str):
            lowered = value.lower()
            return any(pattern in lowered for pattern in patterns)
        return False

    orig = getattr(exc, "orig", None)
    if isinstance(orig, sqlite3.Error):
        code = getattr(orig, "sqlite_errorcode", None)
        if code == sqlite3.SQLITE_FULL:
            return True
        errno = getattr(orig, "errno", None)
        if errno == sqlite3.SQLITE_FULL:
            return True
        name = getattr(orig, "sqlite_errorname", None)
        if _has_pattern(name):
            return True
        args = getattr(orig, "args", None)
        if args:
            for arg in args:
                if _has_pattern(arg):
                    return True
        if _has_pattern(str(orig)):
            return True

    # Fallback to inspecting the SQLAlchemy wrapper itself
    if _has_pattern(str(exc)):
        return True
    args = getattr(exc, "args", None)
    if args:
        for arg in args:
            if _has_pattern(arg):
                return True
    return False


def _split_keywords(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_keyword_phrase(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


def _loads_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _loads_json_vector(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    result: list[float] = []
    for item in parsed:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result


def _to_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_legacy_columns(conn)


async def _ensure_legacy_columns(conn) -> None:
    await _ensure_columns(
        conn,
        "app_settings",
        {
            "keyword_version": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    await _ensure_columns(
        conn,
        "detections",
        {
            "detail_text_raw": "TEXT",
            "normalized_text": "TEXT",
            "analysis_status": "VARCHAR(32) NOT NULL DEFAULT 'pending'",
            "analysis_version": "INTEGER NOT NULL DEFAULT 0",
            "analysis_confidence": "FLOAT",
            "analysis_summary": "TEXT",
            "analysis_explanation": "TEXT",
            "analysis_decision_source": "VARCHAR(32)",
            "analysis_needs_review": "BOOLEAN NOT NULL DEFAULT 0",
            "analysis_completed_at": "DATETIME",
        },
    )


async def _ensure_columns(conn, table_name: str, columns: dict[str, str]) -> None:
    result = await conn.execute(text(f"PRAGMA table_info('{table_name}')"))
    existing = {str(row[1]) for row in result.fetchall()}
    for column_name, ddl in columns.items():
        if column_name in existing:
            continue
        await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
