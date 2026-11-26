from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, ValidationError

from ..db.session import utcnow
from .privacy import MemoryPrivacyPolicy
from .repository import MemoryRepository
from .schemas import Classification, Memory, MemoryCreate, MemoryUpdate

EXPORT_FORMAT = "jarvis-memory-export"
MAX_IMPORT_ITEMS = 10_000


class ImportError_(ValueError):
    pass


class _ImportEnvelope(BaseModel):
    format: str
    version: int
    memories: list[MemoryCreate] = Field(max_length=MAX_IMPORT_ITEMS)


class MemoryService:
    name = "MemoryService"

    def __init__(self, repo: MemoryRepository, policy: MemoryPrivacyPolicy | None = None):
        self._repo = repo
        self._policy = policy or MemoryPrivacyPolicy()
        self._running = False

    # lifecycle contract used by the Phase 2 ServiceManager
    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def health(self) -> dict:
        try:
            count = len(self._repo.list())
            return {"status": "healthy" if self._running else "stopped", "memories": count}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "error": type(exc).__name__}

    # operations
    def create(self, data: MemoryCreate) -> Memory:
        return self._repo.create(data)

    def get(self, memory_id: str) -> Memory:
        return self._repo.get(memory_id)

    def update(self, memory_id: str, patch: MemoryUpdate) -> Memory:
        return self._repo.update(memory_id, patch)

    def list(self, classification: Classification | None = None, include_deleted: bool = False) -> list[Memory]:
        cls = [classification] if classification else None
        return self._repo.list(classifications=cls, include_deleted=include_deleted)

    def search(self, query: str, limit: int = 10) -> list[Memory]:
        return self._repo.search(query, limit=limit)

    def soft_delete(self, memory_id: str) -> None:
        self._repo.soft_delete(memory_id)

    def restore(self, memory_id: str) -> Memory:
        return self._repo.restore(memory_id)

    def hard_delete(self, memory_id: str) -> None:
        self._repo.hard_delete(memory_id)

    def purge_expired(self) -> int:
        return self._repo.purge_expired()

    def export_memories(self, include_sensitive: bool = False) -> dict:
        items = [m for m in self._repo.list() if self._policy.can_export(m, include_sensitive)]
        self._repo.record_audit("export", f"count={len(items)} sensitive={include_sensitive}")
        return {
            "format": EXPORT_FORMAT, "version": 1, "exported_at": utcnow().isoformat(),
            "memories": [
                {"title": m.title, "content": m.content, "classification": m.classification.value,
                 "tags": m.tags, "expires_at": m.expires_at.isoformat() if m.expires_at else None}
                for m in items
            ],
        }

    def import_memories(self, payload: dict) -> int:
        try:
            env = _ImportEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ImportError_(f"invalid import file: {exc.error_count()} validation error(s)") from exc
        if env.format != EXPORT_FORMAT or env.version != 1:
            raise ImportError_("unsupported import format/version")
        now, n = utcnow(), 0
        for item in env.memories:
            if item.expires_at is not None and item.expires_at <= now:
                continue  # already expired
            self._repo.create(item)
            n += 1
        self._repo.record_audit("import", f"count={n}")
        return n
