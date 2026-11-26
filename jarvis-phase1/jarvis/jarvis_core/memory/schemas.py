from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Classification(str, Enum):
    PUBLIC = "PUBLIC"        # Claude may access
    PRIVATE = "PRIVATE"      # local only
    SENSITIVE = "SENSITIVE"  # never sent to Claude


def _aware(v: datetime | None) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError("datetime must include a timezone (use UTC)")
    return v


class MemoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    classification: Classification = Classification.PRIVATE  # safe default
    tags: list[str] = Field(default_factory=list, max_length=20)
    expires_at: datetime | None = None

    _tz = field_validator("expires_at")(_aware)


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    classification: Classification | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    expires_at: datetime | None = None
    clear_expiry: bool = False

    _tz = field_validator("expires_at")(_aware)


class Memory(BaseModel):
    id: str
    title: str
    content: str
    classification: Classification
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
