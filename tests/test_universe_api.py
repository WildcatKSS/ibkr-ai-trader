"""
Tests for web/api/universe.py — approve / reject / history endpoints.

All DB work uses the in-memory SQLite fixture.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from db.models import UniverseSelection
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
def pending_row(patch_get_session):
    """Insert a pending selection and return its id."""
    row = UniverseSelection(
        scan_date=date(2026, 4, 14),
        candidates=[
            {"symbol": "AAPL", "score": 90.0, "analysis": "strong momentum"},
            {"symbol": "MSFT", "score": 85.0, "analysis": "near resistance"},
        ],
        selected_symbol=None,
        status="pending_approval",
        reasoning="Both look good.",
        created_at=datetime.now(tz=timezone.utc),
    )
    patch_get_session.add(row)
    patch_get_session.flush()
    return row.id


class TestPending:
    def test_no_pending_returns_null(self, client, patch_get_session):
        r = client.get("/api/universe/pending")
        assert r.status_code == 200
        assert r.json() is None

    def test_pending_returned(self, client, pending_row):
        r = client.get("/api/universe/pending")
        assert r.status_code == 200
        body = r.json()
        assert body is not None
        assert body["status"] == "pending_approval"
        assert len(body["candidates"]) == 2


class TestApprove:
    def test_happy_path(self, client, pending_row):
        r = client.post(
            "/api/universe/approve",
            json={"selection_id": pending_row, "symbol": "AAPL"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved"
        assert body["selected_symbol"] == "AAPL"
        assert body["decided_at"] is not None

    def test_unknown_symbol_returns_422(self, client, pending_row):
        r = client.post(
            "/api/universe/approve",
            json={"selection_id": pending_row, "symbol": "TSLA"},
        )
        assert r.status_code == 422

    def test_already_decided_returns_409(self, client, pending_row):
        # First approval succeeds; second must fail.
        client.post("/api/universe/approve",
                    json={"selection_id": pending_row, "symbol": "AAPL"})
        r = client.post("/api/universe/approve",
                        json={"selection_id": pending_row, "symbol": "MSFT"})
        assert r.status_code == 409

    def test_unknown_selection_id_returns_404(self, client, patch_get_session):
        r = client.post("/api/universe/approve",
                        json={"selection_id": 9999, "symbol": "AAPL"})
        assert r.status_code == 404


class TestReject:
    def test_happy_path(self, client, pending_row):
        r = client.post(
            "/api/universe/reject",
            json={"selection_id": pending_row, "reason": "market choppy"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "rejected"
        assert "choppy" in (body["reasoning"] or "")

    def test_already_decided_returns_409(self, client, pending_row):
        client.post("/api/universe/reject",
                    json={"selection_id": pending_row, "reason": ""})
        r = client.post("/api/universe/reject",
                        json={"selection_id": pending_row, "reason": ""})
        assert r.status_code == 409


class TestHistory:
    def test_history_ordering(self, client, patch_get_session):
        for i, d in enumerate([date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)]):
            patch_get_session.add(UniverseSelection(
                scan_date=d,
                candidates=[{"symbol": "X"}],
                status="autonomous" if i == 2 else "rejected",
                created_at=datetime.now(tz=timezone.utc),
            ))
        patch_get_session.flush()
        r = client.get("/api/universe/history")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 3
        # Newest first.
        assert body[0]["scan_date"] == "2026-04-03"
        assert body[-1]["scan_date"] == "2026-04-01"
