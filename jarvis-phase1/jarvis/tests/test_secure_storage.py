import os
import stat
import sys

import pytest

from jarvis_core.security.secure_storage import EncryptedFileStorage, get_secure_storage


def test_roundtrip_and_delete(storage):
    storage.set("anthropic_api_key", "sk-ant-secret-123")
    assert storage.get("anthropic_api_key") == "sk-ant-secret-123"
    storage.delete("anthropic_api_key")
    assert storage.get("anthropic_api_key") is None


def test_secret_never_plaintext_on_disk(tmp_path):
    s = EncryptedFileStorage(tmp_path)
    s.set("api_token", "PLAINTEXT-NEEDLE-987")
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert b"PLAINTEXT-NEEDLE-987" not in f.read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_master_key_file_is_owner_only(tmp_path):
    EncryptedFileStorage(tmp_path)
    mode = stat.S_IMODE(os.stat(tmp_path / "master.key").st_mode)
    assert mode == 0o600


@pytest.mark.parametrize("bad", ["../evil", "A B", "", "x" * 65, "a/b"])
def test_rejects_unsafe_secret_names(storage, bad):
    with pytest.raises(ValueError):
        storage.set(bad, "v")


def test_persists_across_instances(tmp_path):
    EncryptedFileStorage(tmp_path).set("k", "v")
    assert EncryptedFileStorage(tmp_path).get("k") == "v"


def test_factory_returns_a_backend(tmp_path):
    assert get_secure_storage(tmp_path).backend
