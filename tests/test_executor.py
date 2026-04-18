"""
Tests for bot/orders/executor.py and bot/orders/eod_close.py

All IBKR broker calls and DB writes are mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.orders.executor import IBKRBroker, OrderResult, execute
from bot.orders.eod_close import close_all_positions
from bot.risk.manager import RiskDecision
from bot.signals.generator import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal(action: str = "long") -> Signal:
    return Signal(
        symbol="AAPL",
        action=action,
        entry_price=150.0,
        target_price=153.0,
        stop_price=148.5,
        confidence=0.75,
        explanation="Test signal",
        ml_label=action,
        ml_probability=0.75,
        confirmed_15min=True,
    )


def _decision(approved: bool = True) -> RiskDecision:
    return RiskDecision(
        approved=approved,
        shares=20,
        dollar_value=3000.0,
        stop_price=148.5,
        target_price=153.0,
        reason="Approved." if approved else "Rejected.",
    )


def _mock_broker(fill: str = "Filled", fill_price: float = 150.10) -> MagicMock:
    broker = MagicMock(spec=IBKRBroker)
    broker.place_order.return_value = 1001
    broker.get_order_status.return_value = (fill, fill_price if fill == "Filled" else None)
    broker.cancel_order.return_value = None
    broker.get_positions.return_value = []
    return broker


# ---------------------------------------------------------------------------
# execute() — dryrun
# ---------------------------------------------------------------------------


class TestExecuteDryrun:
    def test_dryrun_returns_success(self):
        with patch("bot.orders.executor._create_trade_record", return_value=42):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(), trading_mode="dryrun")
        assert result.success is True

    def test_dryrun_no_broker_needed(self):
        with patch("bot.orders.executor._create_trade_record", return_value=42):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(), trading_mode="dryrun", broker=None)
        assert result.success is True

    def test_dryrun_fill_price_is_entry(self):
        with patch("bot.orders.executor._create_trade_record", return_value=42):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(), trading_mode="dryrun")
        assert result.fill_price == 150.0

    def test_dryrun_correct_shares(self):
        with patch("bot.orders.executor._create_trade_record", return_value=42):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(approved=True), trading_mode="dryrun")
        assert result.shares == 20

    def test_dryrun_symbol_preserved(self):
        with patch("bot.orders.executor._create_trade_record", return_value=42):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(), trading_mode="dryrun")
        assert result.symbol == "AAPL"


# ---------------------------------------------------------------------------
# execute() — not approved
# ---------------------------------------------------------------------------


class TestExecuteNotApproved:
    def test_unapproved_returns_failure(self):
        result = execute(_signal(), _decision(approved=False), trading_mode="paper")
        assert result.success is False

    def test_unapproved_no_db_write(self):
        with patch("bot.orders.executor._create_trade_record") as mock_create:
            execute(_signal(), _decision(approved=False), trading_mode="paper")
        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# execute() — paper/live, successful fill
# ---------------------------------------------------------------------------


class TestExecutePaperFill:
    def test_successful_fill(self):
        broker = _mock_broker(fill="Filled", fill_price=150.10)
        with (
            patch("bot.orders.executor._create_trade_record", return_value=42),
            patch("bot.orders.executor._update_trade"),
            patch("bot.utils.config.get", return_value=60),
        ):
            result = execute(_signal(), _decision(), trading_mode="paper", broker=broker)
        assert result.success is True
        assert result.fill_price == 150.10

    def test_fill_calls_place_order(self):
        broker = _mock_broker()
        with (
            patch("bot.orders.executor._create_trade_record", return_value=1),
            patch("bot.orders.executor._update_trade"),
            patch("bot.utils.config.get", return_value=60),
        ):
            execute(_signal(), _decision(), trading_mode="paper", broker=broker)
        broker.place_order.assert_called_once()

    def test_buy_action_for_long(self):
        broker = _mock_broker()
        with (
            patch("bot.orders.executor._create_trade_record", return_value=1),
            patch("bot.orders.executor._update_trade"),
            patch("bot.utils.config.get", return_value=60),
        ):
            execute(_signal("long"), _decision(), trading_mode="paper", broker=broker)
        call_args = broker.place_order.call_args
        assert call_args.kwargs.get("action") == "BUY" or call_args.args[1] == "BUY"

    def test_sell_action_for_short(self):
        broker = _mock_broker()
        with (
            patch("bot.orders.executor._create_trade_record", return_value=1),
            patch("bot.orders.executor._update_trade"),
            patch("bot.utils.config.get", return_value=60),
        ):
            execute(_signal("short"), _decision(), trading_mode="paper", broker=broker)
        call_args = broker.place_order.call_args
        assert call_args.kwargs.get("action") == "SELL" or call_args.args[1] == "SELL"

    def test_no_broker_returns_failure(self):
        with patch("bot.orders.executor._create_trade_record", return_value=1):
            with patch("bot.orders.executor._update_trade"):
                result = execute(_signal(), _decision(), trading_mode="paper", broker=None)
        assert result.success is False


# ---------------------------------------------------------------------------
# execute() — timeout → market order fallback
# ---------------------------------------------------------------------------


class TestExecuteTimeout:
    def test_timeout_then_market_fill(self):
        broker = MagicMock(spec=IBKRBroker)
        broker.place_order.side_effect = [1001, 1002]
        # First call returns "Submitted" (never fills), second "Filled"
        broker.get_order_status.side_effect = [
            ("Submitted", None),  # limit order — instant timeout in test
            ("Filled", 150.20),   # market order
        ]
        with (
            patch("bot.orders.executor._create_trade_record", return_value=1),
            patch("bot.orders.executor._update_trade"),
            patch("bot.utils.config.get", return_value=2),  # 2s timeout
            patch("bot.orders.executor._POLL_INTERVAL", 0),  # no sleep in tests
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999, 999]),
        ):
            result = execute(_signal(), _decision(), trading_mode="paper", broker=broker)
        # Should have placed 2 orders (limit + market)
        assert broker.place_order.call_count == 2


class TestPollInterrupt:
    """Verify that poll_interrupt wakes up _wait_for_fill immediately."""

    def test_interrupt_returns_none(self):
        import threading

        import bot.orders.executor as executor_mod

        broker = MagicMock(spec=IBKRBroker)
        broker.get_order_status.return_value = ("Submitted", None)

        # Pre-set the interrupt so the very first wait() returns immediately.
        executor_mod.poll_interrupt.set()
        try:
            from bot.orders.executor import _wait_for_fill
            result = _wait_for_fill(broker, 9999, timeout=60)
        finally:
            executor_mod.poll_interrupt.clear()  # reset for other tests

        assert result is None

    def test_no_interrupt_polls_normally(self):
        """Without interrupt, a Filled status is returned correctly."""
        import bot.orders.executor as executor_mod

        broker = MagicMock(spec=IBKRBroker)
        broker.get_order_status.return_value = ("Filled", 123.45)

        executor_mod.poll_interrupt.clear()
        from bot.orders.executor import _wait_for_fill
        result = _wait_for_fill(broker, 9999, timeout=10)
        assert result == pytest.approx(123.45)


# ---------------------------------------------------------------------------
# close_all_positions()
# ---------------------------------------------------------------------------


class TestCloseAllPositions:
    def test_dryrun_returns_empty(self):
        result = close_all_positions(None, trading_mode="dryrun")
        assert result == []

    def test_no_broker_returns_empty(self):
        result = close_all_positions(None, trading_mode="paper")
        assert result == []

    def test_no_positions_returns_empty(self):
        broker = MagicMock(spec=IBKRBroker)
        broker.get_positions.return_value = []
        result = close_all_positions(broker, trading_mode="paper")
        assert result == []

    def test_long_position_places_sell(self):
        broker = MagicMock(spec=IBKRBroker)
        broker.get_positions.return_value = [
            {"symbol": "AAPL", "shares": 100, "action": "long"}
        ]
        broker.place_order.return_value = 2001
        broker.get_order_status.return_value = ("Filled", 151.0)
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        assert len(results) == 1
        assert results[0]["success"] is True
        call_args = broker.place_order.call_args
        close_action = call_args.kwargs.get("action") or call_args.args[1]
        assert close_action == "SELL"

    def test_short_position_places_buy(self):
        broker = MagicMock(spec=IBKRBroker)
        broker.get_positions.return_value = [
            {"symbol": "TSLA", "shares": 50, "action": "short"}
        ]
        broker.place_order.return_value = 2002
        broker.get_order_status.return_value = ("Filled", 200.0)
        with patch("bot.orders.eod_close._mark_trade_closed"):
            results = close_all_positions(broker, trading_mode="paper")
        call_args = broker.place_order.call_args
        close_action = call_args.kwargs.get("action") or call_args.args[1]
        assert close_action == "BUY"

    def test_fill_timeout_marks_failure(self):
        broker = MagicMock(spec=IBKRBroker)
        broker.get_positions.return_value = [
            {"symbol": "AAPL", "shares": 10, "action": "long"}
        ]
        broker.place_order.return_value = 2003
        broker.get_order_status.return_value = ("Submitted", None)
        with (
            patch("bot.orders.eod_close._mark_trade_closed"),
            patch("time.monotonic", side_effect=[0, 0, 999, 999, 999]),
            patch("bot.orders.executor._POLL_INTERVAL", 0),
        ):
            results = close_all_positions(broker, trading_mode="paper")
        assert results[0]["success"] is False


# ---------------------------------------------------------------------------
# execute() — trade record creation failure
# ---------------------------------------------------------------------------


class TestTradeRecordFailure:
    def test_db_failure_aborts_paper_order(self):
        with patch("bot.orders.executor._create_trade_record", return_value=None):
            result = execute(_signal(), _decision(), trading_mode="paper")
        assert result.success is False
        assert "Trade record creation failed" in result.reason

    def test_db_failure_aborts_dryrun_order(self):
        with patch("bot.orders.executor._create_trade_record", return_value=None):
            result = execute(_signal(), _decision(), trading_mode="dryrun")
        assert result.success is False
