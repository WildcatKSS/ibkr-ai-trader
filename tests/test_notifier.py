"""
Tests for bot/alerts/notifier.py

SMTP and HTTP calls are mocked; no real network traffic is generated.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bot.alerts.notifier import _format_message, notify


# ---------------------------------------------------------------------------
# _format_message()
# ---------------------------------------------------------------------------


class TestFormatMessage:
    def test_trade_opened_subject(self):
        subject, _ = _format_message("trade_opened", {
            "symbol": "AAPL", "action": "long", "trading_mode": "paper"
        })
        assert "AAPL" in subject
        assert "long" in subject.lower() or "opened" in subject.lower()

    def test_trade_closed_pnl_in_subject(self):
        subject, _ = _format_message("trade_closed", {
            "symbol": "NVDA", "pnl": 125.50, "trading_mode": "paper"
        })
        assert "125.50" in subject

    def test_circuit_breaker_subject(self):
        subject, _ = _format_message("circuit_breaker", {"trading_mode": "paper"})
        assert "Circuit breaker" in subject or "circuit" in subject.lower()

    def test_daily_summary_pnl_in_subject(self):
        subject, _ = _format_message("daily_summary", {"total_pnl": -55.0})
        assert "55.00" in subject

    def test_error_event(self):
        subject, _ = _format_message("error", {"error": "Connection lost"})
        assert "Connection lost" in subject or "Error" in subject

    def test_unknown_event_falls_back_to_json(self):
        subject, body = _format_message("custom_event", {"key": "value"})
        assert "custom_event" in subject
        assert '"key"' in body

    def test_body_contains_symbol(self):
        _, body = _format_message("trade_opened", {
            "symbol": "MSFT", "action": "short", "shares": 10,
            "fill_price": 300.0, "target_price": 295.0,
            "stop_price": 302.5, "trading_mode": "paper", "explanation": ""
        })
        assert "MSFT" in body

    def test_positive_pnl_has_plus_sign(self):
        subject, body = _format_message("trade_closed", {
            "symbol": "AAPL", "pnl": 50.0, "trading_mode": "paper"
        })
        assert "+50.00" in subject


# ---------------------------------------------------------------------------
# notify() — disabled alerts
# ---------------------------------------------------------------------------


class TestNotifyDisabled:
    def test_no_email_when_disabled(self):
        def mock_get(key, *, default=None, cast=str):
            if key == "ALERTS_EMAIL_ENABLED":
                return False if cast is bool else "false"
            if key == "ALERTS_WEBHOOKS_ENABLED":
                return False if cast is bool else "false"
            return default

        with (
            patch("bot.alerts.notifier._send_email") as mock_email,
            patch("bot.utils.config.get", side_effect=mock_get),
        ):
            notify("trade_opened", {"symbol": "AAPL"})
        mock_email.assert_not_called()

    def test_no_webhook_when_disabled(self):
        def mock_get(key, *, default=None, cast=str):
            if key == "ALERTS_EMAIL_ENABLED":
                return False if cast is bool else "false"
            if key == "ALERTS_WEBHOOKS_ENABLED":
                return False if cast is bool else "false"
            return default

        with (
            patch("bot.alerts.notifier._send_webhook") as mock_wh,
            patch("bot.utils.config.get", side_effect=mock_get),
        ):
            notify("trade_opened", {"symbol": "AAPL"})
        mock_wh.assert_not_called()

    def test_config_error_does_not_raise(self):
        with patch("bot.utils.config.get", side_effect=Exception("DB down")):
            # Must not raise
            notify("trade_opened", {"symbol": "AAPL"})


# ---------------------------------------------------------------------------
# notify() — enabled email
# ---------------------------------------------------------------------------


class TestNotifyEmail:
    def test_email_called_when_enabled(self):
        def mock_get(key, *, default=None, cast=str):
            if key == "ALERTS_EMAIL_ENABLED":
                return True if cast is bool else "true"
            if key == "ALERTS_WEBHOOKS_ENABLED":
                return False if cast is bool else "false"
            return default

        with (
            patch("bot.alerts.notifier._send_email") as mock_email,
            patch("bot.utils.config.get", side_effect=mock_get),
        ):
            notify("trade_closed", {"symbol": "AAPL", "pnl": 10.0})
        mock_email.assert_called_once()

    def test_webhook_called_when_enabled(self):
        def mock_get(key, *, default=None, cast=str):
            if key == "ALERTS_EMAIL_ENABLED":
                return False if cast is bool else "false"
            if key == "ALERTS_WEBHOOKS_ENABLED":
                return True if cast is bool else "true"
            return default

        with (
            patch("bot.alerts.notifier._send_webhook") as mock_wh,
            patch("bot.utils.config.get", side_effect=mock_get),
        ):
            notify("circuit_breaker", {"reason": "Loss limit hit", "trading_mode": "paper"})
        mock_wh.assert_called_once()
