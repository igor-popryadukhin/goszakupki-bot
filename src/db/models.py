from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuthorizedChat(Base):
    __tablename__ = "authorized_chats"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class AuthorizedUser(Base):
    __tablename__ = "authorized_users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (UniqueConstraint("source_id", "external_id", name="ux_detection"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    procedure_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    detail_scan_pending: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail_loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    classification_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TopicProfile(Base):
    __tablename__ = "topic_profiles"
    __table_args__ = (UniqueConstraint("code", name="ux_topic_profiles_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("topic_profiles.id"), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    synonyms_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    keywords_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    negative_keywords_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    embedding_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TenderClassification(Base):
    __tablename__ = "tender_classifications"
    __table_args__ = (UniqueConstraint("detection_id", name="ux_tender_classifications_detection"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(ForeignKey("detections.id"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topic_profiles.id"), nullable=True)
    subtopic_id: Mapped[int | None] = mapped_column(ForeignKey("topic_profiles.id"), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_matches_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    matched_features_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    candidate_topics_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    raw_llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("cache_key", name="ux_embedding_cache_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("chat_id", "source_id", "external_id", name="ux_notification"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    notified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DeepSeekBalanceState(Base):
    __tablename__ = "deepseek_balance_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_alert_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_alert_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)
