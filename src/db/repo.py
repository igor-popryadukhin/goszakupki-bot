from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Iterable

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ..monitor.topic_seed import DEFAULT_TOPIC_PROFILES
from .models import (
    AppSettings,
    AuthorizedChat,
    AuthorizedUser,
    AuthSession,
    Base,
    DeepSeekBalanceState,
    Detection,
    EmbeddingCache,
    Notification,
    TenderClassification,
    TopicProfile,
)


@dataclass(slots=True)
class AppPreferences:
    keywords: list[str]
    interval_seconds: int
    pages: int
    enabled: bool


@dataclass(slots=True)
class BalanceAlertState:
    last_checked_at: datetime | None
    last_alert_date: str | None
    last_alert_status: str | None
    last_snapshot: dict | None


@dataclass(slots=True)
class TopicProfileRecord:
    id: int
    code: str
    name: str
    parent_id: int | None
    description: str
    synonyms: list[str]
    keywords: list[str]
    negative_keywords: list[str]
    embedding_text: str
    is_active: bool


@dataclass(slots=True)
class ClassificationSnapshot:
    detection_id: int
    topic_code: str | None
    subtopic_code: str | None
    confidence: float | None
    decision_source: str | None
    summary: str | None
    reasoning: str | None
    keyword_matches: list[str]
    matched_features: list[str]
    candidate_topics: list[dict[str, Any]]
    classification_error: str | None


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

    async def add_keyword(self, keyword: str) -> bool:
        k = (keyword or "").strip()
        if not k:
            return False
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            items = _split_keywords(settings.keywords)
            low = {s.casefold() for s in items}
            if k.casefold() in low:
                return False
            items.append(k)
            settings.keywords = "\n".join(items)
            await session.commit()
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
            await session.commit()
            return True

    async def clear_keywords(self) -> None:
        async with self._session_factory() as session:
            settings = await session.scalar(select(AppSettings).limit(1))
            if settings is None:
                raise ValueError("App settings not initialized")
            settings.keywords = ""
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

    @dataclass(slots=True)
    class PendingDetail:
        id: int
        source_id: str
        external_id: str
        url: str
        title: str | None
        procedure_type: str | None
        status: str | None
        deadline: str | None
        price: str | None
        retry_count: int
        next_retry_at: datetime | None

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
                    Detection.procedure_type,
                    Detection.status,
                    Detection.deadline,
                    Detection.price,
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
            det.detail_retry_count = (det.detail_retry_count or 0) + 1
            det.detail_next_retry_at = next_retry_at
            await session.commit()
            return det.detail_retry_count

    async def save_detection_classification(
        self,
        *,
        detection_id: int,
        normalized_text: str,
        status: str,
        topic_id: int | None,
        subtopic_id: int | None,
        confidence: float | None,
        decision_source: str | None,
        summary: str | None,
        reasoning: str | None,
        keyword_matches: list[str],
        matched_features: list[str],
        candidate_topics: list[dict[str, Any]],
        raw_llm_response: str | None,
        classification_error: str | None,
    ) -> None:
        async with self._session_factory() as session:
            detection = await session.get(Detection, detection_id)
            if detection is None:
                return
            detection.normalized_text = normalized_text
            detection.classification_status = status
            detection.classified_at = datetime.utcnow()
            detection.classification_error = classification_error

            row = await session.scalar(
                select(TenderClassification).where(TenderClassification.detection_id == detection_id).limit(1)
            )
            if row is None:
                row = TenderClassification(detection_id=detection_id)
                session.add(row)
            row.topic_id = topic_id
            row.subtopic_id = subtopic_id
            row.confidence = confidence
            row.decision_source = decision_source
            row.summary = summary
            row.reasoning = reasoning
            row.keyword_matches_json = json.dumps(keyword_matches, ensure_ascii=False)
            row.matched_features_json = json.dumps(matched_features, ensure_ascii=False)
            row.candidate_topics_json = json.dumps(candidate_topics, ensure_ascii=False)
            row.raw_llm_response = raw_llm_response
            row.classification_error = classification_error
            await session.commit()

    async def get_latest_classification(self, detection_id: int) -> ClassificationSnapshot | None:
        async with self._session_factory() as session:
            classification = await session.scalar(
                select(TenderClassification).where(TenderClassification.detection_id == detection_id).limit(1)
            )
            if classification is None:
                return None
            topic_code = None
            subtopic_code = None
            if classification.topic_id:
                topic = await session.get(TopicProfile, classification.topic_id)
                topic_code = topic.code if topic else None
            if classification.subtopic_id:
                subtopic = await session.get(TopicProfile, classification.subtopic_id)
                subtopic_code = subtopic.code if subtopic else None
            return ClassificationSnapshot(
                detection_id=detection_id,
                topic_code=topic_code,
                subtopic_code=subtopic_code,
                confidence=classification.confidence,
                decision_source=classification.decision_source,
                summary=classification.summary,
                reasoning=classification.reasoning,
                keyword_matches=_json_list(classification.keyword_matches_json),
                matched_features=_json_list(classification.matched_features_json),
                candidate_topics=_json_list_of_dicts(classification.candidate_topics_json),
                classification_error=classification.classification_error,
            )

    async def list_active_topic_profiles(self) -> list[TopicProfileRecord]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(TopicProfile).where(TopicProfile.is_active.is_(True)).order_by(TopicProfile.parent_id.asc(), TopicProfile.id.asc())
                )
            ).scalars().all()
            return [
                TopicProfileRecord(
                    id=row.id,
                    code=row.code,
                    name=row.name,
                    parent_id=row.parent_id,
                    description=row.description,
                    synonyms=_json_list(row.synonyms_json),
                    keywords=_json_list(row.keywords_json),
                    negative_keywords=_json_list(row.negative_keywords_json),
                    embedding_text=row.embedding_text,
                    is_active=row.is_active,
                )
                for row in rows
            ]

    async def seed_default_topics(self) -> int:
        async with self._session_factory() as session:
            existing_codes = set((await session.execute(select(TopicProfile.code))).scalars().all())
            created = 0
            code_to_id: dict[str, int] = {}
            existing_rows = (await session.execute(select(TopicProfile))).scalars().all()
            for row in existing_rows:
                code_to_id[row.code] = row.id
            for seed in DEFAULT_TOPIC_PROFILES:
                if seed.code in existing_codes:
                    continue
                row = TopicProfile(
                    code=seed.code,
                    name=seed.name,
                    description=seed.description,
                    synonyms_json=json.dumps(list(seed.synonyms), ensure_ascii=False),
                    keywords_json=json.dumps(list(seed.keywords), ensure_ascii=False),
                    negative_keywords_json=json.dumps(list(seed.negative_keywords), ensure_ascii=False),
                    embedding_text=_build_embedding_text(seed),
                    is_active=True,
                )
                if seed.parent_code and seed.parent_code in code_to_id:
                    row.parent_id = code_to_id[seed.parent_code]
                session.add(row)
                await session.flush()
                code_to_id[row.code] = row.id
                created += 1
            if created:
                for seed in DEFAULT_TOPIC_PROFILES:
                    if not seed.parent_code:
                        continue
                    row = await session.scalar(select(TopicProfile).where(TopicProfile.code == seed.code).limit(1))
                    parent = await session.scalar(select(TopicProfile).where(TopicProfile.code == seed.parent_code).limit(1))
                    if row and parent and row.parent_id != parent.id:
                        row.parent_id = parent.id
                await session.commit()
            return created

    async def get_embedding_cache(self, cache_key: str) -> list[float] | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(EmbeddingCache).where(EmbeddingCache.cache_key == cache_key).limit(1))
            if row is None:
                return None
            try:
                payload = json.loads(row.vector_json)
            except json.JSONDecodeError:
                return None
            if not isinstance(payload, list):
                return None
            return [float(item) for item in payload]

    async def set_embedding_cache(
        self,
        *,
        cache_key: str,
        source_type: str,
        source_ref: str,
        model: str,
        vector: list[float],
    ) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(EmbeddingCache).where(EmbeddingCache.cache_key == cache_key).limit(1))
            if row is None:
                row = EmbeddingCache(
                    cache_key=cache_key,
                    source_type=source_type,
                    source_ref=source_ref,
                    model=model,
                    vector_json=json.dumps(vector, ensure_ascii=False),
                )
                session.add(row)
            else:
                row.source_type = source_type
                row.source_ref = source_ref
                row.model = model
                row.vector_json = json.dumps(vector, ensure_ascii=False)
            await session.commit()

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
            await session.commit()

    async def clear_authorized_chat_id(self) -> None:
        async with self._session_factory() as session:
            row = await session.scalar(select(AuthSession).where(AuthSession.id == 1))
            if row is not None:
                row.chat_id = None
                await session.commit()

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

    async def remove_authorized_chat(self, chat_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(AuthorizedChat, chat_id)
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def clear_all_authorized_chats(self) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(AuthorizedChat))
            await session.commit()

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

    async def remove_authorized_user(self, user_id: int) -> None:
        async with self._session_factory() as session:
            row = await session.get(AuthorizedUser, user_id)
            if row is not None:
                await session.delete(row)
                await session.commit()

    async def clear_all_authorized_users(self) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(AuthorizedUser))
            await session.commit()

    async def seed_notifications_global_for_existing(self, source_id: str, *, limit: int | None = None) -> int:
        async with self._session_factory() as session:
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
            for ext_id in rows:
                session.add(Notification(chat_id=0, source_id=source_id, external_id=ext_id, sent=False))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            return len(rows)

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

    async def count_detections(self, *, source_id: str | None = None, since: datetime | None = None) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count(Detection.id))
            if source_id:
                stmt = stmt.where(Detection.source_id == source_id)
            if since:
                stmt = stmt.where(Detection.first_seen >= since)
            return int(await session.scalar(stmt) or 0)

    async def count_classified_detections(self, *, source_id: str | None = None, since: datetime | None = None) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count(Detection.id)).where(Detection.classification_status == "classified")
            if source_id:
                stmt = stmt.where(Detection.source_id == source_id)
            if since:
                stmt = stmt.where(Detection.classified_at >= since)
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

    async def get_balance_alert_state(self) -> BalanceAlertState:
        async with self._session_factory() as session:
            state = await self._get_or_create_balance_state(session)
            snapshot = None
            if state.last_snapshot_json:
                try:
                    snapshot = json.loads(state.last_snapshot_json)
                except json.JSONDecodeError:
                    snapshot = None
            return BalanceAlertState(
                last_checked_at=state.last_checked_at,
                last_alert_date=state.last_alert_date,
                last_alert_status=state.last_alert_status,
                last_snapshot=snapshot,
            )

    async def update_balance_alert_state(
        self,
        *,
        last_checked_at: datetime,
        last_snapshot: dict,
        last_alert_date: str | None = None,
        last_alert_status: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            state = await self._get_or_create_balance_state(session)
            state.last_checked_at = last_checked_at
            state.last_snapshot_json = json.dumps(last_snapshot, ensure_ascii=False)
            if last_alert_date is not None:
                state.last_alert_date = last_alert_date
            if last_alert_status is not None:
                state.last_alert_status = last_alert_status
            await session.commit()

    async def _get_or_create_balance_state(self, session: AsyncSession) -> DeepSeekBalanceState:
        state = await session.get(DeepSeekBalanceState, 1)
        if state is None:
            state = DeepSeekBalanceState(id=1)
            session.add(state)
            await session.flush()
        return state


def _split_keywords(text_value: str) -> list[str]:
    return [line.strip() for line in text_value.splitlines() if line.strip()]


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _json_list_of_dicts(value: str | None) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _build_embedding_text(seed_topic: Any) -> str:
    parts = [
        seed_topic.name,
        seed_topic.description,
        " ".join(seed_topic.synonyms),
        " ".join(seed_topic.keywords),
    ]
    return ". ".join(part.strip() for part in parts if part and part.strip())


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_sqlite_migrations(conn)


async def _apply_sqlite_migrations(conn: Any) -> None:
    existing = await conn.execute(text("PRAGMA table_info(detections)"))
    columns = {row[1] for row in existing.fetchall()}
    statements: list[str] = []
    if "normalized_text" not in columns:
        statements.append("ALTER TABLE detections ADD COLUMN normalized_text TEXT")
    if "classification_status" not in columns:
        statements.append("ALTER TABLE detections ADD COLUMN classification_status VARCHAR(32)")
    if "classified_at" not in columns:
        statements.append("ALTER TABLE detections ADD COLUMN classified_at DATETIME")
    if "classification_error" not in columns:
        statements.append("ALTER TABLE detections ADD COLUMN classification_error TEXT")
    for statement in statements:
        await conn.execute(text(statement))
