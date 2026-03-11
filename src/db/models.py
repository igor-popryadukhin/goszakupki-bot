from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


"""Multi-user models removed for global single-user mode."""


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    keyword_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Single-row table: row id=1 holds current authorized chat id
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AuthorizedChat(Base):
    __tablename__ = "authorized_chats"

    # Allow multiple authorized chats; one row per chat
    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class AuthorizedUser(Base):
    __tablename__ = "authorized_users"

    # Authorize by Telegram user id (stable across devices and chats)
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
    # Детальный скан: план/результат
    detail_scan_pending: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail_loaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail_scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    detail_text_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    analysis_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    analysis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_decision_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    analysis_completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("chat_id", "source_id", "external_id", name="ux_notification"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    notified_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Marks whether a message was actually sent to chat (True) or just seeded to suppress floods (False)
    sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DeepSeekBalanceState(Base):
    __tablename__ = "deepseek_balance_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_alert_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_alert_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_snapshot_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class KeywordEntry(Base):
    __tablename__ = "keyword_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_phrase: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_phrase: Mapped[str] = mapped_column(Text, nullable=False)
    synonyms_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    negative_contexts_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"
    __table_args__ = (UniqueConstraint("model", "text_hash", name="ux_embedding_cache_model_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KeywordEmbedding(Base):
    __tablename__ = "keyword_embeddings"
    __table_args__ = (UniqueConstraint("keyword_id", "model", name="ux_keyword_embeddings_keyword_model"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword_id: Mapped[int] = mapped_column(ForeignKey("keyword_entries.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    vector_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AnalysisMatch(Base):
    __tablename__ = "analysis_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(ForeignKey("detections.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword_id: Mapped[int | None] = mapped_column(ForeignKey("keyword_entries.id", ondelete="SET NULL"), nullable=True)
    matched_text: Mapped[str] = mapped_column(Text, nullable=False)
    match_type: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
