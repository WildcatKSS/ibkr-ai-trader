"""
Tests for the extended web API endpoints: trades, performance, portfolio, backtesting.

Uses FastAPI's TestClient. All DB calls are mocked.
Auth is bypassed via dependency_overrides.

Note: The ``cryptography`` library is broken in this test environment
(PyO3 panic), so we pre-mock the entire ``jwt`` module before importing
``web.api.auth``.  Since auth is bypassed via dependency_overrides,
the actual JWT functionality is not exercised.
"""

from __future__ import annotations

import sys
from types import ModuleType
from datetime import datetime, timezone, timedelta
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

# Pre-mock the jwt module (and its problematic cryptography dependency)
# to avoid the PyO3 panic from the broken cryptography library.
if "jwt" not in sys.modules:
    _jwt_mock = ModuleType("jwt")
    _jwt_mock.encode = MagicMock(return_value="mock.jwt.token")
    _jwt_mock.decode = MagicMock(return_value={"sub": "admin"})
    _jwt_mock.ExpiredSignatureError = type("ExpiredSignatureError", (Exception,), {})
    _jwt_mock.InvalidTokenError = type("InvalidTokenError", (Exception,), {})
    _jwt_mock.PyJWK = MagicMock()
    _jwt_mock.PyJWKSet = MagicMock()
    sys.modules["jwt"] = _jwt_mock
    sys.modules["jwt.exceptions"] = ModuleType("jwt.exceptions")
    sys.modules["jwt.exceptions"].ExpiredSignatureError = _jwt_mock.ExpiredSignatureError
    sys.modules["jwt.exceptions"].InvalidTokenError = _jwt_mock.InvalidTokenError

from fastapi.testclient import TestClient

from web.api.auth import require_auth
from web.api.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    """Skip JWT validation for all tests in this module."""
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_trade(
    id: int = 1,
    symbol: str = "AAPL",
    action: str = "long",
    status: str = "closed",
    pnl: float | None = 50.0,
):
    """Create a mock Trade object."""
    t = MagicMock()
    t.id = id
    t.symbol = symbol
    t.action = action
    t.trading_mode = "paper"
    t.status = status
    t.shares = 10
    t.entry_price = 174.50
    t.target_price = 177.00
    t.stop_price = 173.00
    t.fill_price = 174.55
    t.exit_price = 177.00 if status == "closed" else None
    t.pnl = pnl
    t.ibkr_order_id = 12345
    t.ml_label = "long"
    t.ml_probability = 0.72
    t.confirmed_15min = True
    t.explanation = "Strong bullish signal"
    t.created_at = datetime(2024, 3, 15, 14, 30, tzinfo=timezone.utc)
    t.filled_at = datetime(2024, 3, 15, 14, 31, tzinfo=timezone.utc)
    t.closed_at = datetime(2024, 3, 15, 15, 0, tzinfo=timezone.utc) if status == "closed" else None
    return t


def _patch_trade_db(rows, total=None):
    """Patch get_session for trade endpoints that use count + rows."""
    mock_session = MagicMock()

    # For list_trades: session.execute(count_q).scalar() and session.scalars(q).all()
    if total is not None:
        mock_session.execute.return_value.scalar.return_value = total
    else:
        mock_session.execute.return_value.scalar.return_value = len(rows)
    mock_session.scalars.return_value.all.return_value = rows

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("db.session.get_session", return_value=cm), mock_session


def _patch_trade_db_simple(rows):
    """Simpler patch for endpoints that just do scalars().all()."""
    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = rows

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("db.session.get_session", return_value=cm)


def _patch_trade_db_get(trade_obj):
    """Patch for endpoints using session.get(Trade, id)."""
    mock_session = MagicMock()
    mock_session.get.return_value = trade_obj

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("db.session.get_session", return_value=cm)


def _patch_portfolio_db(open_rows, today_pnl=0.0, today_count=0):
    """Patch for the portfolio endpoint with its three queries."""
    mock_session = MagicMock()
    mock_session.scalars.return_value.all.return_value = open_rows
    # Two session.execute calls: today_pnl, today_count
    mock_session.execute.return_value.scalar.side_effect = [today_pnl, today_count]

    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=mock_session)
    cm.__exit__ = MagicMock(return_value=False)
    return patch("db.session.get_session", return_value=cm)


# ---------------------------------------------------------------------------
# GET /api/trades
# ---------------------------------------------------------------------------


class TestListTrades:
    def test_returns_200(self):
        p, _ = _patch_trade_db([])
        with p:
            response = client.get("/api/trades")
        assert response.status_code == 200

    def test_returns_structure(self):
        p, _ = _patch_trade_db([])
        with p:
            data = client.get("/api/trades").json()
        assert "total" in data
        assert "trades" in data
        assert "limit" in data
        assert "offset" in data

    def test_returns_trades(self):
        trades = [_fake_trade(id=1), _fake_trade(id=2)]
        p, _ = _patch_trade_db(trades, total=2)
        with p:
            data = client.get("/api/trades").json()
        assert data["total"] == 2
        assert len(data["trades"]) == 2

    def test_trade_fields(self):
        p, _ = _patch_trade_db([_fake_trade()], total=1)
        with p:
            data = client.get("/api/trades").json()
        trade = data["trades"][0]
        for field in ("id", "symbol", "action", "status", "shares",
                       "entry_price", "pnl", "ml_label"):
            assert field in trade

    def test_symbol_filter(self):
        p, _ = _patch_trade_db([_fake_trade(symbol="AAPL")])
        with p:
            data = client.get("/api/trades?symbol=AAPL").json()
        assert isinstance(data["trades"], list)

    def test_status_filter(self):
        p, _ = _patch_trade_db([_fake_trade(status="closed")])
        with p:
            data = client.get("/api/trades?status_filter=closed").json()
        assert isinstance(data["trades"], list)

    def test_pagination(self):
        p, _ = _patch_trade_db([], total=100)
        with p:
            data = client.get("/api/trades?limit=10&offset=20").json()
        assert data["limit"] == 10
        assert data["offset"] == 20

    def test_limit_capped_at_500(self):
        p, _ = _patch_trade_db([])
        with p:
            data = client.get("/api/trades?limit=9999").json()
        assert data["limit"] == 500

    def test_negative_offset_clamped(self):
        p, _ = _patch_trade_db([])
        with p:
            data = client.get("/api/trades?offset=-5").json()
        assert data["offset"] == 0

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.get("/api/trades")
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None


# ---------------------------------------------------------------------------
# GET /api/trades/open
# ---------------------------------------------------------------------------


class TestOpenTrades:
    def test_returns_200(self):
        with _patch_trade_db_simple([]):
            response = client.get("/api/trades/open")
        assert response.status_code == 200

    def test_returns_list(self):
        with _patch_trade_db_simple([]):
            data = client.get("/api/trades/open").json()
        assert isinstance(data, list)

    def test_returns_open_positions(self):
        trades = [_fake_trade(id=1, status="filled"), _fake_trade(id=2, status="open")]
        with _patch_trade_db_simple(trades):
            data = client.get("/api/trades/open").json()
        assert len(data) == 2

    def test_trade_format(self):
        with _patch_trade_db_simple([_fake_trade(status="filled")]):
            data = client.get("/api/trades/open").json()
        assert "symbol" in data[0]
        assert "action" in data[0]

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.get("/api/trades/open")
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None


# ---------------------------------------------------------------------------
# GET /api/trades/{trade_id}
# ---------------------------------------------------------------------------


class TestGetTrade:
    def test_returns_200(self):
        with _patch_trade_db_get(_fake_trade()):
            response = client.get("/api/trades/1")
        assert response.status_code == 200

    def test_returns_trade_data(self):
        with _patch_trade_db_get(_fake_trade(id=42, symbol="MSFT")):
            data = client.get("/api/trades/42").json()
        assert data["id"] == 42
        assert data["symbol"] == "MSFT"

    def test_not_found_returns_404(self):
        with _patch_trade_db_get(None):
            response = client.get("/api/trades/999")
        assert response.status_code == 404

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.get("/api/trades/1")
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None


# ---------------------------------------------------------------------------
# GET /api/performance
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_returns_200_empty(self):
        with _patch_trade_db_simple([]):
            response = client.get("/api/performance")
        assert response.status_code == 200

    def test_empty_returns_zeros(self):
        with _patch_trade_db_simple([]):
            data = client.get("/api/performance").json()
        assert data["trade_count"] == 0
        assert data["total_pnl"] == 0.0

    def test_with_closed_trades(self):
        trades = [
            _fake_trade(id=1, pnl=100.0, status="closed"),
            _fake_trade(id=2, pnl=-30.0, status="closed"),
        ]
        with _patch_trade_db_simple(trades):
            data = client.get("/api/performance").json()
        assert data["trade_count"] == 2
        assert data["total_pnl"] == 70.0

    def test_period_filter_accepted(self):
        with _patch_trade_db_simple([]):
            for period in ("1d", "7d", "30d", "all"):
                response = client.get(f"/api/performance?period={period}")
                assert response.status_code == 200

    def test_win_rate_calculation(self):
        trades = [
            _fake_trade(id=1, pnl=100.0, status="closed"),
            _fake_trade(id=2, pnl=-30.0, status="closed"),
            _fake_trade(id=3, pnl=50.0, status="closed"),
        ]
        with _patch_trade_db_simple(trades):
            data = client.get("/api/performance").json()
        # 2 wins, 1 loss → 66.7%
        assert data["win_rate"] == pytest.approx(66.7, abs=0.1)

    def test_profit_factor(self):
        trades = [
            _fake_trade(id=1, pnl=100.0, status="closed"),
            _fake_trade(id=2, pnl=-50.0, status="closed"),
        ]
        with _patch_trade_db_simple(trades):
            data = client.get("/api/performance").json()
        assert data["profit_factor"] == 2.0

    def test_response_fields(self):
        with _patch_trade_db_simple([]):
            data = client.get("/api/performance").json()
        for field in ("period", "trade_count", "total_pnl", "win_rate",
                       "avg_pnl", "largest_win", "largest_loss", "profit_factor"):
            assert field in data

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.get("/api/performance")
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None


# ---------------------------------------------------------------------------
# GET /api/portfolio
# ---------------------------------------------------------------------------


class TestPortfolio:
    def test_returns_200(self):
        with _patch_portfolio_db([]):
            response = client.get("/api/portfolio")
        assert response.status_code == 200

    def test_empty_portfolio(self):
        with _patch_portfolio_db([]):
            data = client.get("/api/portfolio").json()
        assert data["position_count"] == 0
        assert data["open_positions"] == []

    def test_with_open_positions(self):
        positions = [_fake_trade(status="filled"), _fake_trade(id=2, status="open")]
        with _patch_portfolio_db(positions, today_pnl=150.0, today_count=5):
            data = client.get("/api/portfolio").json()
        assert data["position_count"] == 2
        assert data["daily_pnl"] == 150.0
        assert data["daily_trades"] == 5

    def test_position_fields(self):
        with _patch_portfolio_db([_fake_trade(status="filled")]):
            data = client.get("/api/portfolio").json()
        pos = data["open_positions"][0]
        for field in ("symbol", "action", "shares", "entry_price", "status"):
            assert field in pos

    def test_response_structure(self):
        with _patch_portfolio_db([]):
            data = client.get("/api/portfolio").json()
        for field in ("open_positions", "position_count", "daily_pnl",
                       "daily_trades", "timestamp"):
            assert field in data

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.get("/api/portfolio")
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None


# ---------------------------------------------------------------------------
# POST /api/backtesting/run
# ---------------------------------------------------------------------------


class TestBacktestingRun:
    def test_returns_422_when_no_data(self):
        with patch("web.api.main._fetch_backtest_data", return_value=None):
            response = client.post(
                "/api/backtesting/run",
                json={"symbol": "AAPL"},
            )
        assert response.status_code == 422

    def test_returns_200_with_result(self):
        import pandas as pd
        import numpy as np

        np.random.seed(0)
        bars = pd.DataFrame({
            "open": [100.0] * 100,
            "high": [101.0] * 100,
            "low": [99.0] * 100,
            "close": [100.0] * 100,
            "volume": [10000] * 100,
        }, index=pd.date_range("2024-01-02 09:30", periods=100, freq="5min"))

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {
            "symbol": "AAPL",
            "initial_capital": 100000,
            "final_equity": 100500,
            "trade_count": 5,
            "metrics": {},
            "parameters": {},
            "trades": [],
            "equity_curve": [],
        }

        with (
            patch("web.api.main._fetch_backtest_data", return_value=bars),
            patch("bot.backtesting.engine.BacktestEngine") as MockEngine,
        ):
            MockEngine.return_value.run.return_value = mock_result
            response = client.post(
                "/api/backtesting/run",
                json={"symbol": "AAPL", "initial_capital": 50000},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["symbol"] == "AAPL"

    def test_custom_parameters_passed(self):
        import pandas as pd

        bars = pd.DataFrame({
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0] * 10,
            "volume": [10000] * 10,
        }, index=pd.date_range("2024-01-02", periods=10, freq="5min"))

        mock_result = MagicMock()
        mock_result.to_dict.return_value = {"symbol": "SPY"}

        with (
            patch("web.api.main._fetch_backtest_data", return_value=bars),
            patch("bot.backtesting.engine.BacktestEngine") as MockEngine,
        ):
            MockEngine.return_value.run.return_value = mock_result
            client.post("/api/backtesting/run", json={
                "symbol": "SPY",
                "initial_capital": 50000,
                "position_size_pct": 5.0,
                "stop_loss_atr": 1.5,
                "take_profit_atr": 3.0,
                "ml_min_probability": 0.6,
            })
            MockEngine.assert_called_once_with(
                initial_capital=50000,
                position_size_pct=5.0,
                stop_loss_atr=1.5,
                take_profit_atr=3.0,
                ml_min_probability=0.6,
            )

    def test_invalid_capital_returns_422(self):
        response = client.post(
            "/api/backtesting/run",
            json={"symbol": "AAPL", "initial_capital": -1000},
        )
        assert response.status_code == 422

    def test_missing_symbol_returns_422(self):
        response = client.post("/api/backtesting/run", json={})
        assert response.status_code == 422

    def test_requires_auth(self):
        app.dependency_overrides.clear()
        response = client.post(
            "/api/backtesting/run", json={"symbol": "AAPL"}
        )
        assert response.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None
