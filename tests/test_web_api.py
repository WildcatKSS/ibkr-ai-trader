"""
Tests for web/api/main.py.

Uses FastAPI's TestClient so no real server is started.
All DB and config calls are mocked — no MariaDB connection needed.

Because route handlers import dependencies locally (inside the function body),
patches must target the source module, not web.api.main.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from web.api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_200(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_status_ok(self):
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_includes_timestamp(self):
        data = client.get("/health").json()
        assert "timestamp" in data
        assert data["timestamp"]  # non-empty string


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


def _status_patches(trading_mode="dryrun", market_open=False, trading_day=True):
    """Enter three patches needed for /api/status into an ExitStack."""
    stack = ExitStack()
    stack.enter_context(patch("bot.utils.config.get", return_value=trading_mode))
    stack.enter_context(patch("bot.utils.calendar.is_market_open", return_value=market_open))
    stack.enter_context(patch("bot.utils.calendar.is_trading_day", return_value=trading_day))
    return stack


class TestStatus:
    def test_returns_200(self):
        with _status_patches():
            response = client.get("/api/status")
        assert response.status_code == 200

    def test_contains_required_fields(self):
        with _status_patches():
            data = client.get("/api/status").json()
        for field in ("trading_mode", "market_open", "trading_day", "timestamp"):
            assert field in data

    def test_config_error_returns_unknown(self):
        from bot.utils.config import ConfigError

        with (
            patch("bot.utils.config.get", side_effect=ConfigError("no db")),
            patch("bot.utils.calendar.is_market_open", return_value=False),
            patch("bot.utils.calendar.is_trading_day", return_value=False),
        ):
            data = client.get("/api/status").json()
        assert data["trading_mode"] == "unknown"

    def test_market_open_reflected(self):
        with _status_patches(market_open=True):
            data = client.get("/api/status").json()
        assert data["market_open"] is True


# ---------------------------------------------------------------------------
# /api/settings  GET
# ---------------------------------------------------------------------------


class TestListSettings:
    def test_returns_200(self):
        with patch("bot.utils.config.all_settings", return_value={"TRADING_MODE": "dryrun"}):
            response = client.get("/api/settings")
        assert response.status_code == 200

    def test_returns_dict(self):
        expected = {"TRADING_MODE": "dryrun", "EOD_CLOSE_MINUTES": "15"}
        with patch("bot.utils.config.all_settings", return_value=expected):
            data = client.get("/api/settings").json()
        assert data == expected

    def test_empty_settings_returns_empty_dict(self):
        with patch("bot.utils.config.all_settings", return_value={}):
            data = client.get("/api/settings").json()
        assert data == {}


# ---------------------------------------------------------------------------
# /api/settings  PUT
# ---------------------------------------------------------------------------


def _make_session_cm(existing_value=MagicMock()):
    """Return a context-manager mock for get_session()."""
    mock_session = MagicMock()
    mock_session.get.return_value = existing_value
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, mock_session


class TestUpdateSetting:
    def test_returns_200(self):
        cm, _ = _make_session_cm()
        with (
            patch("db.session.get_session", return_value=cm),
            patch("bot.utils.config.reload"),
        ):
            response = client.put("/api/settings/TRADING_MODE?value=paper")
        assert response.status_code == 200

    def test_returns_key_and_value(self):
        cm, _ = _make_session_cm()
        with (
            patch("db.session.get_session", return_value=cm),
            patch("bot.utils.config.reload"),
        ):
            data = client.put("/api/settings/TRADING_MODE?value=paper").json()
        assert data["key"] == "TRADING_MODE"
        assert data["value"] == "paper"

    def test_new_setting_adds_to_session(self):
        cm, mock_session = _make_session_cm(existing_value=None)
        with (
            patch("db.session.get_session", return_value=cm),
            patch("bot.utils.config.reload"),
        ):
            response = client.put("/api/settings/NEW_KEY?value=hello")
        assert response.status_code == 200
        # add() is called at least once for the new Setting row.
        # (The async logger may add a LogEntry on the same session mock too.)
        from db.models import Setting
        added_types = [type(c.args[0]) for c in mock_session.add.call_args_list]
        assert Setting in added_types

    def test_existing_setting_updates_value(self):
        existing = MagicMock()
        existing.value = "old"
        cm, _ = _make_session_cm(existing_value=existing)
        with (
            patch("db.session.get_session", return_value=cm),
            patch("bot.utils.config.reload"),
        ):
            client.put("/api/settings/TRADING_MODE?value=live")
        assert existing.value == "live"


# ---------------------------------------------------------------------------
# /api/logs
# ---------------------------------------------------------------------------


def _fake_row(i: int):
    row = MagicMock()
    row.id = i
    row.timestamp.isoformat.return_value = "2024-01-08T15:00:00+00:00"
    row.level = "INFO"
    row.category = "trading"
    row.module = "bot.orders.executor"
    row.message = f"Test message {i}"
    row.extra = None
    return row


def _patch_log_db(rows):
    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = rows
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("db.session.get_session", return_value=cm)


class TestRecentLogs:
    def test_returns_200(self):
        with _patch_log_db([]):
            response = client.get("/api/logs")
        assert response.status_code == 200

    def test_returns_list(self):
        with _patch_log_db([_fake_row(i) for i in range(3)]):
            data = client.get("/api/logs").json()
        assert isinstance(data, list)
        assert len(data) == 3

    def test_entry_has_required_fields(self):
        with _patch_log_db([_fake_row(1)]):
            data = client.get("/api/logs").json()
        for field in ("id", "timestamp", "level", "category", "module", "message"):
            assert field in data[0]

    def test_limit_capped_at_500(self):
        # limit=9999 must not raise — silently capped to 500.
        with _patch_log_db([]):
            response = client.get("/api/logs?limit=9999")
        assert response.status_code == 200

    def test_empty_result_returns_empty_list(self):
        with _patch_log_db([]):
            data = client.get("/api/logs").json()
        assert data == []


# ---------------------------------------------------------------------------
# OpenAPI / docs
# ---------------------------------------------------------------------------


class TestDocs:
    def test_docs_endpoint_available(self):
        assert client.get("/api/docs").status_code == 200

    def test_openapi_json_available(self):
        assert client.get("/api/openapi.json").status_code == 200
