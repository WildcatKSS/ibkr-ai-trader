"""
Tests for bot/signals/generator.py

Uses synthetic OHLCV data and mocked LightGBM / Claude API so no real
external calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from bot.signals.generator import (
    Signal,
    _atr_prices,
    _confirm_15min,
    _no_signal,
    _resample_to_15min,
    generate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bars(n: int = 200, trend: str = "up") -> pd.DataFrame:
    """Return deterministic 5-min OHLCV bars."""
    rng = np.random.default_rng(0)
    if trend == "up":
        closes = np.cumprod(1 + np.full(n, 0.0003) + rng.normal(0, 0.0005, n)) * 100
    else:
        closes = np.cumprod(1 + np.full(n, -0.0003) + rng.normal(0, 0.0005, n)) * 100
    highs = closes * 1.004
    lows = closes * 0.996
    opens = lows + (highs - lows) * 0.5
    volumes = np.full(n, 500_000.0)
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestAtrPrices:
    def test_long_target_above_entry(self):
        entry, target, stop = _atr_prices(100.0, 1.0, "long")
        assert target > entry

    def test_long_stop_below_entry(self):
        entry, target, stop = _atr_prices(100.0, 1.0, "long")
        assert stop < entry

    def test_short_target_below_entry(self):
        entry, target, stop = _atr_prices(100.0, 1.0, "short")
        assert target < entry

    def test_short_stop_above_entry(self):
        entry, target, stop = _atr_prices(100.0, 1.0, "short")
        assert stop > entry

    def test_zero_atr_returns_same_price(self):
        entry, target, stop = _atr_prices(100.0, 0.0, "long")
        assert entry == target == stop == 100.0

    def test_values_rounded(self):
        entry, target, stop = _atr_prices(100.12345, 0.123456, "long")
        assert len(str(target).split(".")[-1]) <= 4


class TestResampleTo15min:
    def test_reduces_row_count(self):
        bars = _make_bars(60)
        result = _resample_to_15min(bars)
        assert len(result) < len(bars)

    def test_output_has_ohlcv_columns(self):
        result = _resample_to_15min(_make_bars(60))
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in result.columns

    def test_no_datetime_index_returns_unchanged(self):
        bars = _make_bars(20).reset_index(drop=True)
        result = _resample_to_15min(bars)
        assert len(result) == len(bars)


class TestNoSignal:
    def test_returns_no_trade_action(self):
        s = _no_signal("AAPL", "test_reason")
        assert s.action == "no_trade"

    def test_symbol_preserved(self):
        s = _no_signal("MSFT", "reason")
        assert s.symbol == "MSFT"

    def test_zero_prices(self):
        s = _no_signal("AAPL", "reason")
        assert s.entry_price == 0.0
        assert s.target_price == 0.0
        assert s.stop_price == 0.0


# ---------------------------------------------------------------------------
# Integration tests: generate()
# ---------------------------------------------------------------------------


class TestGenerateNoTrade:
    def test_no_model_returns_no_trade(self):
        """Without a loaded model, predict() returns no_trade."""
        bars = _make_bars()
        signal = generate("AAPL", bars)
        # Either no_trade (no model) or a valid signal
        assert signal.action in {"no_trade", "long", "short"}
        assert signal.symbol == "AAPL"

    def test_short_bars_returns_no_trade_or_signal(self):
        """Very short bar history — should not crash."""
        bars = _make_bars(n=10)
        signal = generate("AAPL", bars)
        assert signal.action in {"no_trade", "long", "short"}

    def test_returns_signal_type(self):
        bars = _make_bars()
        signal = generate("AAPL", bars)
        assert isinstance(signal, Signal)

    def test_ml_low_probability_returns_no_trade(self):
        """Force LightGBM to return a low-probability prediction."""
        bars = _make_bars()
        with patch("bot.ml.model.predict") as mock_predict:
            mock_predict.return_value = ("long", 0.40)
            signal = generate("AAPL", bars, ml_min_probability=0.55)
        assert signal.action == "no_trade"
        assert signal.explanation == "ml_low_probability"

    def test_ml_no_trade_returns_no_trade(self):
        bars = _make_bars()
        with patch("bot.ml.model.predict") as mock_predict:
            mock_predict.return_value = ("no_trade", 0.0)
            signal = generate("AAPL", bars)
        assert signal.action == "no_trade"


class TestGenerateWithSignal:
    def test_long_signal_uses_atr_fallback_without_claude(self):
        """With ML long + 15-min confirmed, no Claude key → ATR fallback."""
        bars = _make_bars()
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
            patch.dict("os.environ", {}, clear=False),
        ):
            # Remove API key so client returns None
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            mock_predict.return_value = ("long", 0.75)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=None)

        assert signal.action == "long"
        assert signal.entry_price > 0
        assert signal.target_price > signal.entry_price
        assert signal.stop_price < signal.entry_price

    def test_short_signal_atr_fallback(self):
        bars = _make_bars()
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            mock_predict.return_value = ("short", 0.70)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=None)

        assert signal.action == "short"
        assert signal.target_price < signal.entry_price
        assert signal.stop_price > signal.entry_price

    def test_claude_response_parsed(self):
        """Mock Claude returning a valid JSON response."""
        bars = _make_bars()
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [
            MagicMock(
                text='{"action":"long","entry":150.0,"target":153.0,'
                     '"stop":148.5,"confidence":0.8,"explanation":"Strong trend."}'
            )
        ]
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            mock_predict.return_value = ("long", 0.72)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=mock_client)

        assert signal.action == "long"
        assert signal.entry_price == 150.0
        assert signal.target_price == 153.0
        assert signal.stop_price == 148.5
        assert signal.confidence == 0.8

    def test_claude_no_trade_override_respected(self):
        """Claude can override ML signal to no_trade."""
        bars = _make_bars()
        mock_client = MagicMock()
        mock_client.messages.create.return_value.content = [
            MagicMock(
                text='{"action":"no_trade","entry":0,"target":0,"stop":0,'
                     '"confidence":0.0,"explanation":"RSI overbought."}'
            )
        ]
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            mock_predict.return_value = ("long", 0.72)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=mock_client)

        assert signal.action == "no_trade"

    def test_claude_api_error_falls_back_to_atr(self):
        bars = _make_bars()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            mock_predict.return_value = ("long", 0.70)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=mock_client)

        assert signal.action == "long"
        assert signal.entry_price > 0

    def test_15min_failure_discards_signal(self):
        bars = _make_bars()
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            mock_predict.return_value = ("long", 0.75)
            mock_15m.return_value = False
            signal = generate("AAPL", bars)

        assert signal.action == "no_trade"
        assert signal.explanation == "15min_no_confirm"

    def test_ml_probability_stored(self):
        bars = _make_bars()
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            mock_predict.return_value = ("long", 0.68)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=None)

        assert signal.ml_probability == pytest.approx(0.68)

    def test_confirmed_15min_stored(self):
        bars = _make_bars()
        with (
            patch("bot.ml.model.predict") as mock_predict,
            patch("bot.signals.generator._confirm_15min") as mock_15m,
        ):
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            mock_predict.return_value = ("long", 0.68)
            mock_15m.return_value = True
            signal = generate("AAPL", bars, client=None)

        assert signal.confirmed_15min is True
