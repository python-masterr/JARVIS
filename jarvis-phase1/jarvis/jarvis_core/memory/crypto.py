from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class FieldCipher:
    def __init__(self, key: str | bytes):
        self._f = Fernet(key)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("memory decryption failed (wrong key or corrupted data)") from exc
