"""Secret storage. Secrets never touch disk in plaintext.

Windows : DPAPI (CryptProtectData), one blob file per secret.
macOS   : Keychain       (via `keyring`)
Linux   : Secret Service (via `keyring`)
Fallback: Fernet-encrypted file. Protects against casual disclosure only: any process
          running as the same OS user can read the master key. Prefer the OS stores.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
_SERVICE = "JARVIS"


class SecureStorageUnavailable(RuntimeError):
    pass


def _check_key(key: str) -> str:
    if not _KEY_RE.match(key):
        raise ValueError("invalid secret name")
    return key


class SecureStorage(ABC):
    backend: str

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def get_or_create(self, key: str, factory) -> str:
        value = self.get(key)
        if value is None:
            value = factory()
            self.set(key, value)
        return value


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class EncryptedFileStorage(SecureStorage):
    backend = "encrypted-file"

    def __init__(self, directory: Path):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._key_path = self._dir / "master.key"
        self._data_path = self._dir / "secrets.enc"
        if not self._key_path.exists():
            _write_private(self._key_path, Fernet.generate_key())
        self._fernet = Fernet(self._key_path.read_bytes())

    def _load(self) -> dict[str, str]:
        if not self._data_path.exists():
            return {}
        return json.loads(self._fernet.decrypt(self._data_path.read_bytes()))

    def _save(self, data: dict[str, str]) -> None:
        _write_private(self._data_path, self._fernet.encrypt(json.dumps(data).encode()))

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[_check_key(key)] = value
        self._save(data)

    def get(self, key: str) -> str | None:
        return self._load().get(_check_key(key))

    def delete(self, key: str) -> None:
        data = self._load()
        if data.pop(_check_key(key), None) is not None:
            self._save(data)


class KeyringStorage(SecureStorage):
    """macOS Keychain / Linux Secret Service through the `keyring` library."""

    def __init__(self):
        try:
            import keyring
            from keyring.backends import fail
        except Exception as exc:  # pragma: no cover
            raise SecureStorageUnavailable(str(exc)) from exc
        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring) or "null" in type(backend).__name__.lower():
            raise SecureStorageUnavailable("no usable keyring backend")
        self._kr = keyring
        self.backend = type(backend).__name__

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(_SERVICE, _check_key(key), value)

    def get(self, key: str) -> str | None:
        return self._kr.get_password(_SERVICE, _check_key(key))

    def delete(self, key: str) -> None:
        try:
            self._kr.delete_password(_SERVICE, _check_key(key))
        except self._kr.errors.PasswordDeleteError:
            pass


class WindowsDpapiStorage(SecureStorage):
    """DPAPI. machine_scope=True lets a service account and the desktop user both decrypt
    (CRYPTPROTECT_LOCAL_MACHINE); the default binds secrets to the current Windows user."""

    backend = "dpapi"
    _LOCAL_MACHINE = 0x4
    _ENTROPY = b"JARVIS-AI-OS-v1"

    def __init__(self, directory: Path, machine_scope: bool = False):
        if sys.platform != "win32":
            raise SecureStorageUnavailable("DPAPI requires Windows")
        self._dir = Path(directory) / "secrets"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._flags = self._LOCAL_MACHINE if machine_scope else 0

    @staticmethod
    def _blob_type():
        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        return DataBlob

    def _call(self, fn_name: str, data: bytes) -> bytes:
        Blob = self._blob_type()
        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        in_buf = ctypes.create_string_buffer(data, len(data))
        ent_buf = ctypes.create_string_buffer(self._ENTROPY, len(self._ENTROPY))
        in_blob = Blob(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
        ent_blob = Blob(len(self._ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
        out = Blob()
        fn = getattr(crypt32, fn_name)
        if fn_name == "CryptProtectData":
            ok = fn(ctypes.byref(in_blob), "JARVIS", ctypes.byref(ent_blob), None, None, self._flags, ctypes.byref(out))
        else:
            ok = fn(ctypes.byref(in_blob), None, ctypes.byref(ent_blob), None, None, self._flags, ctypes.byref(out))
        if not ok:
            raise ctypes.WinError()  # type: ignore[attr-defined]
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)

    def _path(self, key: str) -> Path:
        return self._dir / f"{_check_key(key)}.bin"

    def set(self, key: str, value: str) -> None:
        _write_private(self._path(key), self._call("CryptProtectData", value.encode()))

    def get(self, key: str) -> str | None:
        p = self._path(key)
        return self._call("CryptUnprotectData", p.read_bytes()).decode() if p.exists() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_secure_storage(data_dir: Path, machine_scope: bool = False) -> SecureStorage:
    if sys.platform == "win32":
        return WindowsDpapiStorage(data_dir, machine_scope=machine_scope)
    try:
        return KeyringStorage()
    except SecureStorageUnavailable:
        return EncryptedFileStorage(data_dir)
