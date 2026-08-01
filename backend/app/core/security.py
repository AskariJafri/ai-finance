"""Authentication primitives: password hashing and JWT access/refresh tokens.

Passwords are hashed with Argon2id (argon2-cffi). Tokens are stateless JWTs
signed with HS256; the ``type`` claim distinguishes access from refresh tokens
so a refresh token can never be used to authenticate a request directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


# --- Passwords ---
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # pragma: no cover - malformed hash
        return False


# --- Tokens ---
def _create_token(subject: str, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str) -> str:
    return _create_token(subject, "access", timedelta(minutes=settings.access_token_expire_minutes))


def create_refresh_token(subject: str) -> str:
    return _create_token(subject, "refresh", timedelta(days=settings.refresh_token_expire_days))


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode & validate a JWT. Raises ``jwt.InvalidTokenError`` on failure."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected {expected_type} token, got {payload.get('type')}")
    return payload
