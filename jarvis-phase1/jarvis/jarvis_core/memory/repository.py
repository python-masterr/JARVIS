from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..db.models import AuditRecord, MemoryRecord
from ..db.session import utcnow
from .crypto import FieldCipher
from .privacy import MemoryPrivacyPolicy
from .schemas import Classification, Memory, MemoryCreate, MemoryUpdate

_WORD = re.compile(r"[\w']{3,}", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return list(dict.fromkeys(w.lower() for w in _WORD.findall(text)))


class MemoryNotFound(KeyError):
    pass


class MemoryRepository:
    """Persistence only. Encrypts non-PUBLIC rows; PUBLIC rows stay queryable.

    Search runs in Python over decrypted candidates, which is fine at personal-memory scale
    on SQLite. For PostgreSQL swap in tsvector / pg_trgm for PUBLIC rows."""

    def __init__(self, session_factory: sessionmaker, cipher: FieldCipher, policy: MemoryPrivacyPolicy | None = None):
        self._sf = session_factory
        self._cipher = cipher
        self._policy = policy or MemoryPrivacyPolicy()

    # -- conversion -------------------------------------------------------
    def _seal(self, rec: MemoryRecord, m: Memory) -> None:
        enc = self._policy.encrypts_at_rest(m.classification)
        tags = json.dumps(m.tags)
        rec.encrypted = enc
        rec.title = self._cipher.encrypt(m.title) if enc else m.title
        rec.content = self._cipher.encrypt(m.content) if enc else m.content
        rec.tags = self._cipher.encrypt(tags) if enc else tags
        rec.classification = m.classification.value
        rec.created_at, rec.updated_at = m.created_at, m.updated_at
        rec.expires_at, rec.deleted_at = m.expires_at, m.deleted_at

    def _open(self, rec: MemoryRecord) -> Memory:
        dec = self._cipher.decrypt if rec.encrypted else (lambda s: s)
        return Memory(
            id=rec.id, title=dec(rec.title), content=dec(rec.content),
            classification=Classification(rec.classification), tags=json.loads(dec(rec.tags)),
            created_at=rec.created_at, updated_at=rec.updated_at,
            expires_at=rec.expires_at, deleted_at=rec.deleted_at,
        )

    @staticmethod
    def _audit(s: Session, action: str, memory_id: str | None = None, detail: str = "") -> None:
        s.add(AuditRecord(action=action, memory_id=memory_id, detail=detail[:200]))

    # -- CRUD -------------------------------------------------------------
    def create(self, data: MemoryCreate, now: datetime | None = None) -> Memory:
        now = now or utcnow()
        m = Memory(id=str(uuid.uuid4()), title=data.title, content=data.content,
                   classification=data.classification, tags=data.tags,
                   created_at=now, updated_at=now, expires_at=data.expires_at)
        with self._sf.begin() as s:
            rec = MemoryRecord(id=m.id)
            self._seal(rec, m)
            s.add(rec)
            self._audit(s, "create", m.id, data.classification.value)
        return m

    def get(self, memory_id: str, include_deleted: bool = False) -> Memory:
        with self._sf() as s:
            rec = s.get(MemoryRecord, memory_id)
            if rec is None or (rec.deleted_at is not None and not include_deleted):
                raise MemoryNotFound(memory_id)
            return self._open(rec)

    def update(self, memory_id: str, patch: MemoryUpdate) -> Memory:
        with self._sf.begin() as s:
            rec = s.get(MemoryRecord, memory_id)
            if rec is None or rec.deleted_at is not None:
                raise MemoryNotFound(memory_id)
            m = self._open(rec)
            changes = patch.model_dump(exclude_unset=True, exclude={"clear_expiry"})
            changes = {k: v for k, v in changes.items() if v is not None}
            m = m.model_copy(update={**changes, "updated_at": utcnow()})
            if patch.clear_expiry:
                m = m.model_copy(update={"expires_at": None})
            self._seal(rec, m)
            self._audit(s, "update", memory_id, ",".join(sorted(changes)))
            return m

    def list(self, classifications: list[Classification] | None = None, include_deleted: bool = False,
             include_expired: bool = False, now: datetime | None = None) -> list[Memory]:
        now = now or utcnow()
        stmt = select(MemoryRecord).order_by(MemoryRecord.updated_at.desc())
        if classifications is not None:
            stmt = stmt.where(MemoryRecord.classification.in_([c.value for c in classifications]))
        if not include_deleted:
            stmt = stmt.where(MemoryRecord.deleted_at.is_(None))
        if not include_expired:
            stmt = stmt.where((MemoryRecord.expires_at.is_(None)) | (MemoryRecord.expires_at > now))
        with self._sf() as s:
            return [self._open(r) for r in s.scalars(stmt)]

    def search(self, query: str, classifications: list[Classification] | None = None,
               limit: int = 10, now: datetime | None = None) -> list[Memory]:
        terms = tokenize(query)
        if not terms:
            return []
        scored: list[tuple[int, Memory]] = []
        for m in self.list(classifications=classifications, now=now):
            hay = f"{m.title}\n{m.content}\n{' '.join(m.tags)}".lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, m))
        scored.sort(key=lambda p: (p[0], p[1].updated_at), reverse=True)
        return [m for _, m in scored[:limit]]

    # -- deletion / expiry -----------------------------------------------
    def soft_delete(self, memory_id: str) -> None:
        with self._sf.begin() as s:
            rec = s.get(MemoryRecord, memory_id)
            if rec is None or rec.deleted_at is not None:
                raise MemoryNotFound(memory_id)
            rec.deleted_at = utcnow()
            self._audit(s, "soft_delete", memory_id)

    def restore(self, memory_id: str) -> Memory:
        with self._sf.begin() as s:
            rec = s.get(MemoryRecord, memory_id)
            if rec is None or rec.deleted_at is None:
                raise MemoryNotFound(memory_id)
            rec.deleted_at = None
            self._audit(s, "restore", memory_id)
            return self._open(rec)

    def hard_delete(self, memory_id: str) -> None:
        with self._sf.begin() as s:
            rec = s.get(MemoryRecord, memory_id)
            if rec is None:
                raise MemoryNotFound(memory_id)
            s.delete(rec)
            self._audit(s, "hard_delete", memory_id)

    def purge_expired(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        with self._sf.begin() as s:
            recs = list(s.scalars(select(MemoryRecord).where(
                MemoryRecord.expires_at.is_not(None), MemoryRecord.expires_at <= now)))
            for r in recs:
                s.delete(r)
            self._audit(s, "purge_expired", None, f"count={len(recs)}")
            return len(recs)

    def audit_trail(self, limit: int = 100) -> list[dict]:
        with self._sf() as s:
            rows = s.scalars(select(AuditRecord).order_by(AuditRecord.id.desc()).limit(limit))
            return [{"ts": r.ts.isoformat(), "action": r.action, "memory_id": r.memory_id, "detail": r.detail} for r in rows]

    def record_audit(self, action: str, detail: str = "") -> None:
        with self._sf.begin() as s:
            self._audit(s, action, None, detail)
