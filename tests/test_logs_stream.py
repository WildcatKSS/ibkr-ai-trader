"""
Tests for web/api/logs_stream.py — stream token issuance and consumption.

The SSE endpoint itself isn't exercised end-to-end (that would require an
event loop and a real DB) — instead we test the security-critical parts:
token generation, single-use semantics, IP binding, and expiry.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from web.api import logs_stream
from web.api.auth import require_auth
from web.api.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clear_tokens():
    """Reset the process-local token store between tests."""
    with logs_stream._tokens_lock:
        logs_stream._tokens.clear()
    yield
    with logs_stream._tokens_lock:
        logs_stream._tokens.clear()


@pytest.fixture()
def client():
    return TestClient(app)


class TestIssue:
    def test_endpoint_returns_token(self, client):
        r = client.post("/api/logs/stream-token", json={})
        assert r.status_code == 200
        body = r.json()
        assert "stream_token" in body
        assert len(body["stream_token"]) >= 20
        assert body["expires_in"] == logs_stream._TOKEN_TTL_SECONDS

    def test_issued_tokens_are_unique(self):
        t1 = logs_stream._issue_token("1.2.3.4")
        t2 = logs_stream._issue_token("1.2.3.4")
        assert t1 != t2


class TestConsume:
    def test_valid_token_consumes_once(self):
        tok = logs_stream._issue_token("1.2.3.4")
        assert logs_stream._consume_token(tok, "1.2.3.4") is True
        # Second attempt must fail: tokens are single-use.
        assert logs_stream._consume_token(tok, "1.2.3.4") is False

    def test_wrong_ip_rejects(self):
        tok = logs_stream._issue_token("1.2.3.4")
        assert logs_stream._consume_token(tok, "9.9.9.9") is False
        # The token has already been consumed (and discarded), so further
        # attempts from the correct IP also fail — fail-closed behaviour.
        assert logs_stream._consume_token(tok, "1.2.3.4") is False

    def test_expired_token_rejects(self, monkeypatch):
        tok = logs_stream._issue_token("1.2.3.4")
        # Advance the clock past the TTL.
        entry = logs_stream._tokens[tok]
        entry.created_at = time.monotonic() - (logs_stream._TOKEN_TTL_SECONDS + 5)
        assert logs_stream._consume_token(tok, "1.2.3.4") is False

    def test_unknown_token_rejects(self):
        assert logs_stream._consume_token("not-a-real-token", "1.2.3.4") is False


class TestStreamAuth:
    def test_missing_token_returns_422(self, client):
        r = client.get("/api/logs/stream")
        assert r.status_code == 422

    def test_invalid_token_returns_401(self, client):
        r = client.get("/api/logs/stream?stream_token=" + "x" * 32)
        assert r.status_code == 401
