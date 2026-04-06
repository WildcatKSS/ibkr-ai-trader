"""
Tests for web/api/auth.py — JWT login and require_auth dependency.

No real .env is needed: SECRET_KEY and WEB_PASSWORD are injected via
monkeypatch/os.environ patching in every test.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

from web.api.auth import (
    _ALGORITHM,
    _create_token,
    _decode_token,
    require_auth,
)
from web.api.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-secret-key-not-for-production"
_TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    """Inject test credentials into os.environ for every test."""
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET)
    monkeypatch.setenv("WEB_PASSWORD", _TEST_PASSWORD)


@pytest.fixture()
def client():
    """TestClient with auth overrides cleared (real auth active)."""
    app.dependency_overrides.clear()
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def valid_token() -> str:
    return _create_token()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


class TestTokenHelpers:
    def test_create_and_decode_roundtrip(self):
        token = _create_token()
        payload = _decode_token(token)
        assert "exp" in payload
        assert payload.get("sub") == "admin"

    def test_expired_token_raises(self):
        exp = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        token = jwt.encode({"exp": exp}, _TEST_SECRET, algorithm=_ALGORITHM)
        with pytest.raises(jwt.ExpiredSignatureError):
            _decode_token(token)

    def test_wrong_secret_raises(self):
        token = jwt.encode({"exp": 9999999999}, "wrong-secret", algorithm=_ALGORITHM)
        with pytest.raises(jwt.PyJWTError):
            _decode_token(token)


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_correct_password_returns_200(self, client):
        response = client.post("/api/auth/login", json={"password": _TEST_PASSWORD})
        assert response.status_code == 200

    def test_returns_bearer_token(self, client):
        data = client.post("/api/auth/login", json={"password": _TEST_PASSWORD}).json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_token_is_valid_jwt(self, client):
        data = client.post("/api/auth/login", json={"password": _TEST_PASSWORD}).json()
        payload = _decode_token(data["access_token"])
        assert "exp" in payload

    def test_wrong_password_returns_401(self, client):
        response = client.post("/api/auth/login", json={"password": "wrong"})
        assert response.status_code == 401

    def test_empty_password_returns_401(self, client):
        response = client.post("/api/auth/login", json={"password": ""})
        assert response.status_code == 401

    def test_missing_web_password_env_raises(self, client, monkeypatch):
        monkeypatch.delenv("WEB_PASSWORD", raising=False)
        response = client.post("/api/auth/login", json={"password": _TEST_PASSWORD})
        assert response.status_code == 500

    def test_rate_limit_blocks_after_max_failures(self, monkeypatch):
        """After _MAX_ATTEMPTS wrong passwords the endpoint returns 429."""
        import web.api.auth as auth_module

        # Reset rate-limit state so this test is independent of ordering.
        with auth_module._rl_lock:
            auth_module._failed_attempts.clear()

        app.dependency_overrides.clear()
        c = TestClient(app, raise_server_exceptions=False)

        for _ in range(auth_module._MAX_ATTEMPTS):
            c.post("/api/auth/login", json={"password": "wrong"})

        response = c.post("/api/auth/login", json={"password": "wrong"})
        assert response.status_code == 429

        # Restore for subsequent tests.
        with auth_module._rl_lock:
            auth_module._failed_attempts.clear()
        app.dependency_overrides[require_auth] = lambda: None

    def test_correct_password_not_rate_limited(self, client, monkeypatch):
        """A correct password after failed attempts still succeeds."""
        import web.api.auth as auth_module

        with auth_module._rl_lock:
            auth_module._failed_attempts.clear()

        # Fail up to but not including the limit.
        for _ in range(auth_module._MAX_ATTEMPTS - 1):
            client.post("/api/auth/login", json={"password": "wrong"})

        response = client.post("/api/auth/login", json={"password": _TEST_PASSWORD})
        assert response.status_code == 200

        with auth_module._rl_lock:
            auth_module._failed_attempts.clear()


# ---------------------------------------------------------------------------
# require_auth dependency — via a protected route
# ---------------------------------------------------------------------------


class TestRequireAuth:
    def test_no_token_returns_401(self, client):
        # FastAPI HTTPBearer returns 401 when the Authorization header is absent.
        response = client.get("/api/settings")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self, client):
        response = client.get(
            "/api/settings",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert response.status_code == 401

    def test_expired_token_returns_401(self, client):
        exp = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        expired = jwt.encode({"exp": exp}, _TEST_SECRET, algorithm=_ALGORITHM)
        response = client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401

    def test_valid_token_passes_through(self, client, valid_token):
        with patch("bot.utils.config.all_settings", return_value={}):
            response = client.get(
                "/api/settings",
                headers={"Authorization": f"Bearer {valid_token}"},
            )
        assert response.status_code == 200

    def test_health_requires_no_token(self, client):
        """GET /health must always be public — Nginx depends on it."""
        response = client.get("/health")
        assert response.status_code == 200
