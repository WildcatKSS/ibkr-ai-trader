"""
Tests for bot/risk/manager.py

All DB calls are mocked so no real MariaDB connection is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bot.risk.manager import RiskDecision, _kelly_amount, check
from bot.signals.generator import Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signal(
    action: str = "long",
    entry: float = 100.0,
    target: float = 102.0,
    stop: float = 99.0,
    ml_prob: float = 0.70,
) -> Signal:
    return Signal(
        symbol="AAPL",
        action=action,
        entry_price=entry,
        target_price=target,
        stop_price=stop,
        confidence=ml_prob,
        explanation="Test signal",
        ml_label=action,
        ml_probability=ml_prob,
        confirmed_15min=True,
    )


def _mock_get(key, *, default=None, cast=str):
    settings = {
        "POSITION_SIZING_METHOD": "fixed_pct",
        "POSITION_SIZE_PCT": "2.0",
        "POSITION_SIZE_AMOUNT": "5000.0",
        "POSITION_MAX_PCT": "5.0",
        "CIRCUIT_BREAKER_DAILY_LOSS_PCT": "3.0",
        "CIRCUIT_BREAKER_CONSECUTIVE_LOSSES": "5",
    }
    raw = settings.get(key, str(default) if default is not None else "")
    if cast is float:
        return float(raw) if raw else 0.0
    if cast is int:
        return int(raw) if raw else 0
    if cast is bool:
        return raw.lower() in {"true", "1", "yes"}
    return raw


# ---------------------------------------------------------------------------
# No-trade signal
# ---------------------------------------------------------------------------


class TestNoTradeSignal:
    def test_no_trade_action_rejected(self):
        sig = _signal(action="no_trade")
        decision = check(sig, 100_000.0, trading_mode="dryrun")
        assert not decision.approved

    def test_no_trade_reason(self):
        sig = _signal(action="no_trade")
        decision = check(sig, 100_000.0, trading_mode="dryrun")
        assert "no_trade" in decision.reason


# ---------------------------------------------------------------------------
# Portfolio value edge cases
# ---------------------------------------------------------------------------


class TestPortfolioEdgeCases:
    def test_zero_portfolio_rejected(self):
        with patch("bot.risk.manager._circuit_breaker_check", return_value=None):
            decision = check(_signal(), 0.0, trading_mode="dryrun")
        assert not decision.approved

    def test_negative_portfolio_rejected(self):
        with patch("bot.risk.manager._circuit_breaker_check", return_value=None):
            decision = check(_signal(), -1000.0, trading_mode="dryrun")
        assert not decision.approved

    def test_zero_entry_price_rejected(self):
        sig = _signal(entry=0.0)
        with patch("bot.risk.manager._circuit_breaker_check", return_value=None):
            decision = check(sig, 100_000.0, trading_mode="dryrun")
        assert not decision.approved


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_circuit_breaker_blocks_trade(self):
        with patch(
            "bot.risk.manager._circuit_breaker_check",
            return_value="Daily loss 4.0% exceeds limit 3.0%.",
        ):
            decision = check(_signal(), 100_000.0, trading_mode="paper")
        assert not decision.approved
        assert "Daily loss" in decision.reason

    def test_dryrun_skips_circuit_breaker(self):
        """Dryrun never calls the circuit breaker."""
        with (
            patch("bot.risk.manager._circuit_breaker_check") as mock_cb,
            patch("bot.risk.manager.check.__wrapped__", None, create=True),
        ):
            with patch("bot.utils.config.get", side_effect=_mock_get):
                decision = check(_signal(), 100_000.0, trading_mode="dryrun")
        # Circuit breaker should not have been called
        mock_cb.assert_not_called()


# ---------------------------------------------------------------------------
# Position sizing — fixed_pct
# ---------------------------------------------------------------------------


class TestPositionSizingFixedPct:
    def test_approved_returns_correct_shares(self):
        # 2% of 100k = 2000; at 100.0 entry = 20 shares
        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=_mock_get),
        ):
            decision = check(_signal(entry=100.0), 100_000.0, trading_mode="paper")
        assert decision.approved
        assert decision.shares == 20

    def test_approved_dollar_value(self):
        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=_mock_get),
        ):
            decision = check(_signal(entry=100.0), 100_000.0, trading_mode="paper")
        assert decision.dollar_value == pytest.approx(2000.0)

    def test_max_pct_cap_applied(self):
        # 10% PCT but max is 5% → capped at 5000 → 50 shares at $100
        def get_with_high_pct(key, *, default=None, cast=str):
            if key == "POSITION_SIZE_PCT":
                return 10.0 if cast is float else "10.0"
            return _mock_get(key, default=default, cast=cast)

        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=get_with_high_pct),
        ):
            decision = check(_signal(entry=100.0), 100_000.0, trading_mode="paper")
        assert decision.approved
        assert decision.shares <= 50  # capped at 5%


# ---------------------------------------------------------------------------
# Position sizing — fixed_amount
# ---------------------------------------------------------------------------


class TestPositionSizingFixedAmount:
    def test_fixed_amount_sizing(self):
        def get_fixed(key, *, default=None, cast=str):
            if key == "POSITION_SIZING_METHOD":
                return "fixed_amount"
            return _mock_get(key, default=default, cast=cast)

        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=get_fixed),
        ):
            decision = check(_signal(entry=100.0), 100_000.0, trading_mode="paper")
        assert decision.approved
        assert decision.shares == 50  # 5000 / 100


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------


class TestKellySizing:
    def test_kelly_amount_high_prob(self):
        amount = _kelly_amount(0.70, 100_000.0)
        assert amount > 0
        assert amount <= 100_000.0

    def test_kelly_amount_low_prob(self):
        amount = _kelly_amount(0.40, 100_000.0)
        assert amount >= 0

    def test_kelly_amount_zero_prob(self):
        amount = _kelly_amount(0.0, 100_000.0)
        assert amount == 0.0

    def test_kelly_sizing_method(self):
        def get_kelly(key, *, default=None, cast=str):
            if key == "POSITION_SIZING_METHOD":
                return "kelly"
            return _mock_get(key, default=default, cast=cast)

        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=get_kelly),
        ):
            decision = check(_signal(entry=100.0, ml_prob=0.70), 100_000.0, trading_mode="paper")
        assert decision.approved
        assert decision.shares > 0


# ---------------------------------------------------------------------------
# Result fields
# ---------------------------------------------------------------------------


class TestDecisionFields:
    def test_stop_price_preserved(self):
        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=_mock_get),
        ):
            decision = check(_signal(stop=98.5), 100_000.0, trading_mode="paper")
        assert decision.stop_price == 98.5

    def test_target_price_preserved(self):
        with (
            patch("bot.risk.manager._circuit_breaker_check", return_value=None),
            patch("bot.utils.config.get", side_effect=_mock_get),
        ):
            decision = check(_signal(target=103.0), 100_000.0, trading_mode="paper")
        assert decision.target_price == 103.0
