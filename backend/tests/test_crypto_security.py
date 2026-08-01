from __future__ import annotations

import jwt
import pytest
from cryptography.fernet import Fernet

from app.core.crypto import TokenCipher
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_token_cipher_round_trip():
    cipher = TokenCipher(Fernet.generate_key().decode())
    secret = "access-sandbox-abc123"
    encrypted = cipher.encrypt(secret)
    assert encrypted != secret
    assert cipher.decrypt(encrypted) == secret


def test_token_cipher_wrong_key_fails():
    a = TokenCipher(Fernet.generate_key().decode())
    b = TokenCipher(Fernet.generate_key().decode())
    with pytest.raises(ValueError):
        b.decrypt(a.encrypt("secret"))


def test_password_hash_and_verify():
    h = hash_password("supersecret123")
    assert h != "supersecret123"
    assert verify_password("supersecret123", h)
    assert not verify_password("wrong", h)


def test_access_and_refresh_token_types():
    access = create_access_token("user-1")
    refresh = create_refresh_token("user-1")

    assert decode_token(access, expected_type="access")["sub"] == "user-1"
    assert decode_token(refresh, expected_type="refresh")["sub"] == "user-1"

    # An access token must not validate as a refresh token and vice versa.
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(access, expected_type="refresh")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(refresh, expected_type="access")
