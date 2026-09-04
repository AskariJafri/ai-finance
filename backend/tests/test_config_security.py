"""Configuration that must not be deployable in an insecure shape.

Every setting here has a permissive default so `docker compose up` needs no
setup. That convenience is also the risk: a forgotten env var should fail
loudly at boot rather than serve traffic quietly wide open.
"""

from __future__ import annotations

import pytest

from app.core.config import DEV_JWT_SECRET, Settings


def _settings(**over) -> Settings:
    base = {
        "environment": "production",
        "jwt_secret": "x" * 48,
        "encryption_key": "y" * 44,
        "cors_origins": "https://app.example.com",
    }
    return Settings(**{**base, **over})


def test_production_refuses_the_development_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        _settings(jwt_secret=DEV_JWT_SECRET)


def test_production_refuses_wildcard_cors():
    """A deployed API that answers any origin is a finding on its own."""
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(cors_origins="*")


def test_production_accepts_an_explicit_origin_list():
    s = _settings(cors_origins="https://app.example.com, https://admin.example.com")
    assert s.cors_origin_list == ["https://app.example.com", "https://admin.example.com"]


def test_local_still_boots_with_the_permissive_defaults():
    """The whole point of the defaults: no setup for local work."""
    s = Settings(environment="local", cors_origins="*")
    assert s.cors_origin_list == ["*"]
