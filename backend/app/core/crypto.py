"""Symmetric encryption for sensitive data at rest (Plaid access tokens).

We never store bank credentials, and even the Plaid ``access_token`` — which
grants ongoing read access to a linked account — is encrypted with Fernet
(AES-128-CBC + HMAC) before it touches the database. The key comes from
configuration/secrets, never source control.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class TokenCipher:
    """Thin wrapper around Fernet with typed encrypt/decrypt helpers."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:  # pragma: no cover - defensive
            raise ValueError("Failed to decrypt token (invalid key or corrupt data)") from exc


@lru_cache
def get_cipher() -> TokenCipher:
    return TokenCipher(settings.encryption_key)
