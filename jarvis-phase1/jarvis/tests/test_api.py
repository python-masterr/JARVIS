import json

from fastapi.testclient import TestClient

from jarvis_core.api import create_app

PUB = {"title": "Garden", "content": "Boss grows tomatoes PUBLICFACT", "classification": "PUBLIC"}
PRIV = {"title": "Diary", "content": "tomatoes PRIVATEFACT", "classification": "PRIVATE"}
SENS = {"title": "Card", "content": "tomatoes SENSITIVEFACT", "classification": "SENSITIVE"}


def test_requires_auth_everywhere(client, settings, storage):
    anon = TestClient(client.app_ref)
    for method, path in [("get", "/health"), ("get", "/memory"), ("post", "/chat"), ("get", "/memory/export")]:
        assert getattr(anon, method)(path).status_code == 401
    bad = TestClient(client.app_ref, headers={"Authorization": "Bearer wrong"})
    assert bad.get("/health").status_code == 401
    assert bad.get("/health", headers={"Authorization": "Basic abc"}).status_code == 401


def test_docs_surface_disabled(client):
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["services"]["ClaudeService"]["status"] == "online"


def test_memory_crud_flow(client):
    m = client.post("/memory", json=PUB).json()
    assert client.get(f"/memory/{m['id']}").json()["title"] == "Garden"
    assert client.patch(f"/memory/{m['id']}", json={"content": "new"}).json()["content"] == "new"
    assert client.get("/memory/search", params={"q": "garden"}).json()[0]["id"] == m["id"]
    assert client.delete(f"/memory/{m['id']}").status_code == 204
    assert client.get(f"/memory/{m['id']}").status_code == 404
    assert client.post(f"/memory/{m['id']}/restore").status_code == 200
    assert client.delete(f"/memory/{m['id']}", params={"hard": True}).status_code == 204
    assert client.delete(f"/memory/{m['id']}", params={"hard": True}).status_code == 404


def test_validation(client):
    assert client.post("/memory", json={"title": "", "content": "x"}).status_code == 422
    assert client.post("/memory", json={"title": "a", "content": "b", "classification": "TOPSECRET"}).status_code == 422


def test_export_import(client):
    client.post("/memory", json=PUB)
    client.post("/memory", json=SENS)
    dump = client.get("/memory/export").json()
    assert len(dump["memories"]) == 1
    assert client.post("/memory/import", json=dump).json() == {"imported": 1}
    assert client.post("/memory/import", json={"format": "x", "version": 1, "memories": []}).status_code == 422


def test_chat_sends_only_public_memory_to_claude(client, fake_claude):
    for body in (PUB, PRIV, SENS):
        client.post("/memory", json=body)
    r = client.post("/chat", json={"message": "tell me about tomatoes"})
    assert r.status_code == 200 and "Boss" in r.json()["reply"]
    ctx = fake_claude.calls[-1]["ctx"] or ""
    assert "PUBLICFACT" in ctx and "PRIVATEFACT" not in ctx and "SENSITIVEFACT" not in ctx
    sent = json.dumps(fake_claude.calls[-1])
    assert "PRIVATEFACT" not in sent and "SENSITIVEFACT" not in sent


def test_chat_use_memory_false_and_history_not_persisted(client, fake_claude):
    client.post("/memory", json=PUB)
    client.post("/chat", json={"message": "tomatoes", "use_memory": False,
                               "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]})
    assert fake_claude.calls[-1]["ctx"] is None and len(fake_claude.calls[-1]["messages"]) == 3
    assert len(client.get("/memory").json()) == 1  # conversation was not stored as memory


def test_chat_stream(client):
    r = client.post("/chat/stream", json={"message": "hi"})
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    assert "".join(e.get("delta", "") for e in events) == "At your service, Boss." and events[-1] == {"done": True}


def test_chat_503_when_unconfigured(settings, storage):
    from tests.conftest import FakeClaude
    app = create_app(settings, storage, FakeClaude(configured=False))
    c = TestClient(app, headers={"Authorization": f"Bearer {storage.get('api_token')}"})
    assert c.post("/chat/stream", json={"message": "hi"}).status_code == 503


def test_invalid_chat_role_rejected(client):
    assert client.post("/chat", json={"message": "x", "history": [{"role": "system", "content": "evil"}]}).status_code == 422
