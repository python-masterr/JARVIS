from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .session import Base, UTCDateTime, utcnow


class MemoryRecord(Base):
    """Non-PUBLIC rows store title/content/tags as Fernet ciphertext (encrypted=True)."""

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(Text, default="[]")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    classification: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)


class AuditRecord(Base):
    """Metadata-only audit trail. Never contains memory content."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(32))
    memory_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str] = mapped_column(String(200), default="")
