"""
Tests for web/api/ml_admin.py.

We stub out the actual training call (bot.ml.trainer.train) and data-fetch
helpers so no LightGBM or IBKR code runs.  Job state is checked via the
in-memory SQLite database provided by the patch_get_session fixture.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pandas as pd
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


@pytest.fixture()
def _db(patch_get_session):
    """Ensure the DB fixture is active for ml-admin tests."""
    yield patch_get_session


class TestVersionEndpoints:
    def test_current_returns_string_or_none(self, client, _db):
        with patch("web.api.ml_admin.get_current_version", return_value=None):
            r = client.get("/api/ml/current")
        assert r.status_code == 200
        assert r.json() == {"version": None}

    def test_versions_marks_current(self, client, _db):
        versions = [
            {"version": "v1", "trained_at": "2025-01-01", "n_samples": 100, "metrics": {}},
            {"version": "v2", "trained_at": "2025-01-02", "n_samples": 200, "metrics": {}},
        ]
        with patch("web.api.ml_admin.list_versions", return_value=versions), \
             patch("web.api.ml_admin.get_current_version", return_value="v2"):
            r = client.get("/api/ml/versions")
        assert r.status_code == 200
        body = r.json()
        assert body[0]["version"] == "v1"
        assert body[0]["is_current"] is False
        assert body[1]["is_current"] is True


class TestRetrain:
    def test_invalid_body(self, client, _db):
        r = client.post("/api/ml/retrain", json={"symbol": ""})
        assert r.status_code == 422

    def test_success_creates_job(self, client, _db):
        bars = pd.DataFrame(
            {"open": [1] * 600, "high": [1] * 600, "low": [1] * 600,
             "close": [1] * 600, "volume": [1] * 600}
        )
        with patch("web.api.ml_admin._fetch_training_bars", return_value=bars), \
             patch("bot.ml.trainer.train", return_value="v_test"), \
             patch("web.api.ml_admin.list_versions", return_value=[
                 {"version": "v_test", "metrics": {"val_accuracy": 0.9}},
             ]):
            r = client.post(
                "/api/ml/retrain",
                json={
                    "symbol": "SPY",
                    "n_bars": 600,
                    "forward_bars": 6,
                    "long_threshold_pct": 0.3,
                    "short_threshold_pct": 0.3,
                },
            )
            assert r.status_code == 202
            job_id = r.json()["job_id"]

            # Give the background thread a moment.
            for _ in range(50):
                job_r = client.get(f"/api/ml/jobs/{job_id}")
                if job_r.json().get("status") in ("done", "failed"):
                    break
                time.sleep(0.05)
            body = client.get(f"/api/ml/jobs/{job_id}").json()
            assert body["status"] == "done"
            assert body["version"] == "v_test"

    def test_insufficient_data_fails_job(self, client, _db):
        with patch("web.api.ml_admin._fetch_training_bars", return_value=None):
            r = client.post("/api/ml/retrain", json={"symbol": "SPY"})
            assert r.status_code == 202
            job_id = r.json()["job_id"]
            for _ in range(50):
                body = client.get(f"/api/ml/jobs/{job_id}").json()
                if body["status"] in ("done", "failed"):
                    break
                time.sleep(0.05)
            body = client.get(f"/api/ml/jobs/{job_id}").json()
            assert body["status"] == "failed"
            assert "training data" in (body["error"] or "").lower() or \
                   "ibkr" in (body["error"] or "").lower()


class TestRollback:
    def test_success(self, client, _db):
        with patch("web.api.ml_admin.rollback") as mock_rb:
            r = client.post("/api/ml/rollback", json={"version": "v_test"})
            assert r.status_code == 200
            job_id = r.json()["job_id"]
            mock_rb.assert_called_once_with("v_test")

            body = client.get(f"/api/ml/jobs/{job_id}").json()
            assert body["status"] == "done"
            assert body["version"] == "v_test"

    def test_unknown_version_returns_422(self, client, _db):
        with patch(
            "web.api.ml_admin.rollback",
            side_effect=ValueError("Unknown version 'v_nope'."),
        ):
            r = client.post("/api/ml/rollback", json={"version": "v_nope"})
            assert r.status_code == 422


class TestJobsList:
    def test_empty(self, client, _db):
        r = client.get("/api/ml/jobs")
        assert r.status_code == 200
        assert r.json() == []

    def test_not_found(self, client, _db):
        r = client.get("/api/ml/jobs/999")
        assert r.status_code == 404
