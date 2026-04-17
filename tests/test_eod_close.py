"""
Dedicated tests for bot/orders/eod_close.py — the no-overnight-positions guarantee.

Covers: close_all_positions() and _mark_trade_closed() across all
branches: dryrun, no broker, empty positions, single/multi position
close, fill timeouts, order exceptions, alert notifications, and
DB trade record updates with PnL calculation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from bot.orders.eod_close import _mark_trade_closed, close_all_positions
from bot.orders.executor import IBKRBroker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _broker(
    positions: list[dict] | None = None,
    fill: str = "Filled",
    fill_price: float = 151.0,
) -> MagicMock:
    b = MagicMock(spec=IBKRBroker)
    b.get_positions.return_value = positions or []
    b.place_order.return_value = 5001
    b.get_order_status.return_value = (fill, fill_price if fill == "Filled" else None)
    return b


# ---------------------------------------------------------------------------
# close_all_positions — mode & broker edge cases
# ---------------------------------------------------------------------------


class TestDryrun:
    def test_returns_empty_list(self):
        assert close_all_positions(None, trading_mode="dryrun") == []

    def test_does_not_touch_broker(self):
        broker = _broker([{"symbol": "AAPL", "shares": 100, "action": "long"}])
        close_all_positions(broker, trading_mode="dryrun")
        broker.get_positions.assert_not_called()
        broker.place_order.assert_not_called()


class TestNoBroker:
    def test_returns_empty_list(self):
        assert close_all_positions(None, trading_mode="paper") == []

    def test_returns_empty_for_live(self):
        assert close_all_positions(None, trading_mode="live") == []

    def test_sends_alert(self):
        with patch("bot.alerts.notifier.notify") as mock_notify:
            close_all_positions(None, trading_mode="paper")
            mock_notify.assert_called_once()
            args = mock_notify.call_args
            assert args[0][0] == "eod_close_failed"
            assert "No broker" in args[0][1]["reason"]

    def test_alert_failure_does_not_raise(self):
        with patch("bot.alerts.notifier.notify", side_effect=RuntimeError("smtp down")):
            result = close_all_positions(None, trading_mode="paper")
        assert result == []


class TestNoPositions:
    def test_returns_empty_list(self):
        result = close_all_positions(_broker([]), trading_mode="paper")
        assert result == []

    def test_get_positions_exception(self):
        broker = _broker()
        broker.get_positions.side_effect = ConnectionError("disconnected")
        result = close_all_positions(broker, trading_mode="paper")
        assert result == []


# ---------------------------------------------------------------------------
# close_all_positions — single position
# ---------------------------------------------------------------------------


class TestSingleLong:
    def test_closes_with_sell(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 100, "action": "long"}],
            fill="Filled",
            fill_price=151.50,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert len(results) == 1
        assert results[0]["action"] == "SELL"
        assert results[0]["shares"] == 100
        assert results[0]["success"] is True
        assert results[0]["fill_price"] == 151.50

    def test_uses_market_order(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 50, "action": "long"}],
            fill="Filled",
            fill_price=150.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            close_all_positions(broker, trading_mode="paper")
        kw = broker.place_order.call_args.kwargs
        assert kw["order_type"] == "MKT"


class TestSingleShort:
    def test_closes_with_buy(self):
        broker = _broker(
            [{"symbol": "TSLA", "shares": 30, "action": "short"}],
            fill="Filled",
            fill_price=200.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["action"] == "BUY"
        assert results[0]["success"] is True


# ---------------------------------------------------------------------------
# close_all_positions — multiple positions
# ---------------------------------------------------------------------------


class TestMultiplePositions:
    def test_closes_all(self):
        broker = _broker(
            [
                {"symbol": "AAPL", "shares": 100, "action": "long"},
                {"symbol": "MSFT", "shares": 50, "action": "long"},
                {"symbol": "TSLA", "shares": 25, "action": "short"},
            ],
            fill="Filled",
            fill_price=100.0,
        )
        broker.place_order.side_effect = [5001, 5002, 5003]
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="live")
        assert len(results) == 3
        assert all(r["success"] for r in results)
        symbols = {r["symbol"] for r in results}
        assert symbols == {"AAPL", "MSFT", "TSLA"}

    def test_partial_failure(self):
        """First position fills, second order throws — both reported."""
        broker = _broker(
            [
                {"symbol": "AAPL", "shares": 100, "action": "long"},
                {"symbol": "MSFT", "shares": 50, "action": "long"},
            ],
        )
        broker.place_order.side_effect = [5001, ConnectionError("connection lost")]
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert "connection lost" in results[1]["reason"]


# ---------------------------------------------------------------------------
# close_all_positions — fill timeout
# ---------------------------------------------------------------------------


class TestFillTimeout:
    def test_timeout_marks_failure(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 10, "action": "long"}],
            fill="Submitted",
        )
        with (
            patch("bot.orders.eod_close._mark_trade_closed"),
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999]),
            patch("bot.orders.executor._POLL_INTERVAL", 0),
        ):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["success"] is False
        assert "timeout" in results[0]["reason"].lower()

    def test_timeout_sends_alert(self):
        broker = _broker(
            [{"symbol": "NVDA", "shares": 5, "action": "long"}],
            fill="Submitted",
        )
        with (
            patch("bot.orders.eod_close._mark_trade_closed"),
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999]),
            patch("bot.orders.executor._POLL_INTERVAL", 0),
            patch("bot.alerts.notifier.notify") as mock_notify,
        ):
            close_all_positions(broker, trading_mode="paper")
        mock_notify.assert_called_once()
        payload = mock_notify.call_args[0][1]
        assert payload["symbol"] == "NVDA"
        assert "timeout" in payload["reason"].lower()

    def test_timeout_alert_failure_does_not_raise(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 10, "action": "long"}],
            fill="Submitted",
        )
        with (
            patch("bot.orders.eod_close._mark_trade_closed"),
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999]),
            patch("bot.orders.executor._POLL_INTERVAL", 0),
            patch("bot.alerts.notifier.notify", side_effect=Exception("boom")),
        ):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["success"] is False


# ---------------------------------------------------------------------------
# close_all_positions — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zero_shares_skipped(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 0, "action": "long"}],
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert results == []
        broker.place_order.assert_not_called()

    def test_negative_shares_uses_abs(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": -50, "action": "short"}],
            fill="Filled",
            fill_price=100.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["shares"] == 50

    def test_missing_symbol_defaults_to_unknown(self):
        broker = _broker(
            [{"shares": 10, "action": "long"}],
            fill="Filled",
            fill_price=100.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["symbol"] == "UNKNOWN"

    def test_missing_action_defaults_to_long(self):
        broker = _broker(
            [{"symbol": "SPY", "shares": 10}],
            fill="Filled",
            fill_price=100.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["action"] == "SELL"

    def test_marks_trade_closed_called_on_fill(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 100, "action": "long"}],
            fill="Filled",
            fill_price=152.0,
        )
        with patch("bot.orders.eod_close._mark_trade_closed") as mock_mark:
            close_all_positions(broker, trading_mode="paper")
        mock_mark.assert_called_once_with("AAPL", 152.0)

    def test_marks_trade_closed_called_on_timeout(self):
        broker = _broker(
            [{"symbol": "AAPL", "shares": 10, "action": "long"}],
            fill="Submitted",
        )
        with (
            patch("bot.orders.eod_close._mark_trade_closed") as mock_mark,
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999]),
            patch("bot.orders.executor._POLL_INTERVAL", 0),
        ):
            close_all_positions(broker, trading_mode="paper")
        mock_mark.assert_called_once_with("AAPL", None)


# ---------------------------------------------------------------------------
# _mark_trade_closed — DB logic
# ---------------------------------------------------------------------------


class TestMarkTradeClosed:
    def _make_trade(self, action="long", fill_price=150.0, status="filled"):
        trade = MagicMock()
        trade.id = 42
        trade.action = action
        trade.fill_price = fill_price
        trade.shares = 100
        trade.status = status
        return trade

    def _mock_session(self, trade=None):
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = trade
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx, session

    @patch("db.session.get_session")
    def test_long_pnl_calculated(self, mock_get_session):
        trade = self._make_trade(action="long", fill_price=150.0)
        ctx, session = self._mock_session(trade)
        mock_get_session.return_value = ctx

        _mark_trade_closed("AAPL", exit_price=155.0)

        assert session.execute.call_count == 2
        update_call = session.execute.call_args_list[1]
        stmt = update_call[0][0]
        compiled = stmt.compile()
        params = compiled.params
        assert params["pnl"] == pytest.approx(500.0)
        assert params["status"] == "closed"
        assert params["exit_price"] == 155.0

    @patch("db.session.get_session")
    def test_short_pnl_calculated(self, mock_get_session):
        trade = self._make_trade(action="short", fill_price=200.0)
        ctx, session = self._mock_session(trade)
        mock_get_session.return_value = ctx

        _mark_trade_closed("TSLA", exit_price=190.0)

        update_call = session.execute.call_args_list[1]
        stmt = update_call[0][0]
        compiled = stmt.compile()
        params = compiled.params
        # short PnL: (fill - exit) * shares = (200 - 190) * 100 = 1000
        assert params["pnl"] == pytest.approx(1000.0)

    @patch("db.session.get_session")
    def test_no_exit_price_pnl_is_none(self, mock_get_session):
        trade = self._make_trade(action="long", fill_price=150.0)
        ctx, session = self._mock_session(trade)
        mock_get_session.return_value = ctx

        _mark_trade_closed("AAPL", exit_price=None)

        update_call = session.execute.call_args_list[1]
        stmt = update_call[0][0]
        compiled = stmt.compile()
        params = compiled.params
        assert params["pnl"] is None

    @patch("db.session.get_session")
    def test_no_matching_trade(self, mock_get_session):
        ctx, session = self._mock_session(None)
        mock_get_session.return_value = ctx

        _mark_trade_closed("AAPL", exit_price=155.0)

        # Only the SELECT query, no UPDATE
        assert session.execute.call_count == 1

    @patch("db.session.get_session")
    def test_db_exception_does_not_raise(self, mock_get_session):
        mock_get_session.side_effect = RuntimeError("DB unavailable")
        _mark_trade_closed("AAPL", exit_price=155.0)

    @patch("db.session.get_session")
    def test_no_fill_price_on_trade_pnl_is_none(self, mock_get_session):
        trade = self._make_trade(action="long", fill_price=None)
        ctx, session = self._mock_session(trade)
        mock_get_session.return_value = ctx

        _mark_trade_closed("AAPL", exit_price=155.0)

        update_call = session.execute.call_args_list[1]
        stmt = update_call[0][0]
        compiled = stmt.compile()
        params = compiled.params
        assert params["pnl"] is None
