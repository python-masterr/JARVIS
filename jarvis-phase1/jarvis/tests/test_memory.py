from datetime import timedelta

import pytest

from jarvis_core.db.session import utcnow
from jarvis_core.memory import Classification, ImportError_, MemoryCreate, MemoryNotFound, MemoryUpdate


def mk(service, title="Coffee", content="Boss likes flat white", cls=Classification.PUBLIC, **kw):
    return service.create(MemoryCreate(title=title, content=content, classification=cls, **kw))


def test_default_classification_is_private():
    assert MemoryCreate(title="a", content="b").classification is Classification.PRIVATE


def test_create_get_update(service):
    m = mk(service)
    assert service.get(m.id).content == "Boss likes flat white"
    u = service.update(m.id, MemoryUpdate(content="Boss likes cortado", tags=["drink"]))
    assert u.content == "Boss likes cortado" and u.tags == ["drink"] and u.updated_at >= m.updated_at


def test_non_public_encrypted_at_rest(service, repo):
    from sqlalchemy import select
    from jarvis_core.db.models import MemoryRecord
    mk(service, "Bank", "PIN 4821 NEEDLE", Classification.SENSITIVE)
    mk(service, "Notes", "PRIVATE NEEDLE", Classification.PRIVATE)
    mk(service, "Public", "PUBLIC NEEDLE", Classification.PUBLIC)
    with repo._sf() as s:
        rows = {r.classification: r for r in s.scalars(select(MemoryRecord))}
    assert "NEEDLE" not in rows["SENSITIVE"].content + rows["SENSITIVE"].title
    assert "NEEDLE" not in rows["PRIVATE"].content
    assert "NEEDLE" in rows["PUBLIC"].content


def test_reclassify_reencrypts(service, repo):
    from jarvis_core.db.models import MemoryRecord
    m = mk(service, content="secret-ish NEEDLE", cls=Classification.PUBLIC)
    service.update(m.id, MemoryUpdate(classification=Classification.SENSITIVE))
    with repo._sf() as s:
        assert "NEEDLE" not in s.get(MemoryRecord, m.id).content
    assert service.get(m.id).content == "secret-ish NEEDLE"


def test_search_ranks_and_ignores_short_terms(service):
    mk(service, "Pizza", "Boss loves pizza margherita")
    mk(service, "Pasta", "Boss loves pasta")
    hits = service.search("pizza margherita please")
    assert hits[0].title == "Pizza"
    assert service.search("a b") == []


def test_search_covers_sensitive_locally(service):
    mk(service, "Passport", "number X1234567", Classification.SENSITIVE)
    assert [m.title for m in service.search("passport")] == ["Passport"]


def test_soft_delete_restore_hard_delete(service):
    m = mk(service)
    service.soft_delete(m.id)
    with pytest.raises(MemoryNotFound):
        service.get(m.id)
    assert service.list() == [] and len(service.list(include_deleted=True)) == 1
    assert service.restore(m.id).deleted_at is None
    service.hard_delete(m.id)
    with pytest.raises(MemoryNotFound):
        service.restore(m.id)
    assert service.list(include_deleted=True) == []


def test_expiry_hidden_then_purged(service, repo):
    live = mk(service, "Live", "stays around")
    mk(service, "Old", "expires soon", expires_at=utcnow() + timedelta(seconds=1))
    later = utcnow() + timedelta(hours=1)
    assert [m.id for m in repo.list(now=later)] == [live.id]
    assert repo.search("expires", now=later) == []
    assert repo.purge_expired(now=later) == 1
    assert len(service.list()) == 1


def test_naive_expiry_rejected():
    from datetime import datetime
    with pytest.raises(ValueError):
        MemoryCreate(title="a", content="b", expires_at=datetime(2030, 1, 1))


def test_export_excludes_sensitive_by_default(service):
    mk(service, "Pub", "p", Classification.PUBLIC)
    mk(service, "Sens", "s", Classification.SENSITIVE)
    assert [m["title"] for m in service.export_memories()["memories"]] == ["Pub"]
    assert len(service.export_memories(include_sensitive=True)["memories"]) == 2


def test_import_roundtrip_and_validation(service):
    mk(service, "A", "alpha", Classification.PRIVATE, tags=["x"])
    dump = service.export_memories()
    assert service.import_memories(dump) == 1 and len(service.list()) == 2
    with pytest.raises(ImportError_):
        service.import_memories({"format": "nope", "version": 1, "memories": []})
    with pytest.raises(ImportError_):
        service.import_memories({"format": "jarvis-memory-export", "version": 1, "memories": [{"title": ""}]})


def test_import_skips_expired(service):
    payload = {"format": "jarvis-memory-export", "version": 1, "memories": [
        {"title": "x", "content": "y", "expires_at": "2000-01-01T00:00:00+00:00"}]}
    assert service.import_memories(payload) == 0


def test_audit_never_contains_content(service, repo):
    m = mk(service, "T", "CONTENT-NEEDLE", Classification.SENSITIVE)
    service.soft_delete(m.id)
    assert "NEEDLE" not in str(repo.audit_trail())
