"""
Tests for web/api/service.py — bot service control endpoints.

subprocess.run is fully mocked.  No real systemctl is ever invoked.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web.api.auth import require_auth
from web.api.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app)


def _ok(stdout="active\n") -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail(rc=1, stderr="sudo: permission denied") -> MagicMock:
    r = MagicMock()
    r.returncode = rc
    r.stdout = ""
    r.stderr = stderr
    return r


class TestServiceStatus:
    def test_returns_active(self, client):
        with patch("web.api.service._run_systemctl", return_value=_ok("active\n")):
            r = client.get("/api/bot/service-status")
        assert r.status_code == 200
        body = r.json()
        assert body["active"] is True
        assert body["state"] == "active"
        assert body["unit"] == "ibkr-bot"

    def test_returns_inactive(self, client):
        with patch("web.api.service._run_systemctl", return_value=_ok("inactive\n")):
            r = client.get("/api/bot/service-status")
        assert r.status_code == 200
        assert r.json()["active"] is False
        assert r.json()["state"] == "inactive"

    def test_handles_timeout(self, client):
        with patch(
            "web.api.service._run_systemctl",
            side_effect=subprocess.TimeoutExpired(cmd="systemctl", timeout=5),
        ):
            r = client.get("/api/bot/service-status")
        assert r.status_code == 200
        assert r.json()["state"] == "error"


class TestStart:
    def test_success(self, client):
        with patch("web.api.service._run_systemctl", return_value=_ok("")):
            r = client.post("/api/bot/start", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["action"] == "start"

    def test_sudo_failure(self, client):
        with patch("web.api.service._run_systemctl", return_value=_fail()):
            r = client.post("/api/bot/start", json={})
        assert r.status_code == 500
        assert "sudoers" in r.json()["detail"].lower()


class TestStop:
    def test_requires_confirm(self, client):
        r = client.post("/api/bot/stop", json={})
        assert r.status_code == 422

    def test_requires_correct_confirm(self, client):
        r = client.post("/api/bot/stop", json={"confirm": "yes"})
        assert r.status_code == 422

    def test_success(self, client):
        with patch("web.api.service._run_systemctl", return_value=_ok("")):
            r = client.post("/api/bot/stop", json={"confirm": "STOP"})
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestRestart:
    def test_requires_confirm(self, client):
        r = client.post("/api/bot/restart", json={})
        assert r.status_code == 422

    def test_success(self, client):
        with patch("web.api.service._run_systemctl", return_value=_ok("")):
            r = client.post("/api/bot/restart", json={"confirm": "RESTART"})
        assert r.status_code == 200
        assert r.json()["action"] == "restart"


class TestWhitelist:
    def test_perform_rejects_invalid_action(self):
        from web.api.service import _perform

        with pytest.raises(Exception) as exc_info:
            _perform("shutdown", requested_by="test")
        assert "Invalid action" in str(exc_info.value)
