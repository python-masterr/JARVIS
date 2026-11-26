import pytest
from fastapi.testclient import TestClient

from jarvis_core.api import create_app
from jarvis_core.config import Settings
from jarvis_core.db.session import create_db_engine, init_db, make_session_factory
from jarvis_core.memory import MemoryPrivacyPolicy, MemoryRepository, MemoryService
from jarvis_core.memory.crypto import FieldCipher
from jarvis_core.security.secure_storage import EncryptedFileStorage


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path)


@pytest.fixture
def storage(tmp_path):
    return EncryptedFileStorage(tmp_path / "sec")


@pytest.fixture
def repo(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path/'t.db'}")
    init_db(engine)
    return MemoryRepository(make_session_factory(engine), FieldCipher(FieldCipher.generate_key()), MemoryPrivacyPolicy())


@pytest.fixture
def service(repo):
    return MemoryService(repo)


class FakeClaude:
    def __init__(self, configured=True):
        self.configured, self.calls = configured, []

    def is_configured(self):
        return self.configured

    def health(self):
        return {"status": "online" if self.configured else "offline"}

    async def complete(self, messages, model="sonnet", memory_context=None, max_tokens=None):
        self.calls.append({"messages": messages, "model": model, "ctx": memory_context})
        return "At your service, Boss."

    async def stream(self, messages, model="sonnet", memory_context=None, max_tokens=None):
        self.calls.append({"messages": messages, "model": model, "ctx": memory_context})
        for part in ("At your ", "service, Boss."):
            yield part


@pytest.fixture
def fake_claude():
    return FakeClaude()


@pytest.fixture
def client(settings, storage, fake_claude):
    app = create_app(settings, storage, fake_claude)
    token = storage.get("api_token")
    c = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    c.app_ref = app
    return c
