from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint
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


# AuthorizedChat removed: authorization is kept in-memory per bot session
