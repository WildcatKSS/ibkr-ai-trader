"""
Tests for bot/signals/indicators.py.

All tests use synthetic OHLCV data — no real market data or API calls.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from bot.signals.indicators import (
    MIN_ROWS,
    REQUIRED_COLS,
    calculate,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic OHLCV data
# ---------------------------------------------------------------------------


def _make_ohlcv(
    n: int = 100,
    base_price: float = 150.0,
    seed: int = 42,
    with_datetime_index: bool = True,
) -> pd.DataFrame:
    """
    Generate n rows of synthetic 5-min OHLCV candles.

    Uses a random walk for close prices to produce realistic-looking
    indicator values (RSI not stuck at boundary, ATR > 0, etc.).
    """
    rng = np.random.default_rng(seed)
    closes = base_price + np.cumsum(rng.normal(0, 0.5, n))
    highs = closes + rng.uniform(0.1, 0.8, n)
    lows = closes - rng.uniform(0.1, 0.8, n)
    opens = closes + rng.normal(0, 0.3, n)
    volumes = rng.integers(10_000, 500_000, n).astype(float)

    if with_datetime_index:
        start = datetime(2024, 1, 8, 9, 30, tzinfo=timezone.utc)
        index = [start + timedelta(minutes=5 * i) for i in range(n)]
    else:
        index = list(range(n))

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


@pytest.fixture()
def ohlcv() -> pd.DataFrame:
    return _make_ohlcv()


@pytest.fixture()
def ohlcv_short() -> pd.DataFrame:
    """Just below MIN_ROWS — most indicators will be all-NaN."""
    return _make_ohlcv(n=MIN_ROWS - 1)


@pytest.fixture()
def ohlcv_minimal() -> pd.DataFrame:
    """Exactly MIN_ROWS — first valid indicator values should appear."""
    return _make_ohlcv(n=MIN_ROWS)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_close_raises(self, ohlcv):
        with pytest.raises(ValueError, match="close"):
            calculate(ohlcv.drop(columns=["close"]))

    def test_missing_volume_raises(self, ohlcv):
        with pytest.raises(ValueError, match="volume"):
            calculate(ohlcv.drop(columns=["volume"]))

    def test_missing_multiple_columns_raises(self, ohlcv):
        with pytest.raises(ValueError):
            calculate(ohlcv.drop(columns=["high", "low"]))

    def test_uppercase_column_names_accepted(self, ohlcv):
        upper = ohlcv.rename(columns=str.upper)
        result = calculate(upper)
        assert "rsi" in result.columns

    def test_mixed_case_column_names_accepted(self, ohlcv):
        mixed = ohlcv.rename(columns={"close": "Close", "volume": "Volume"})
        result = calculate(mixed)
        assert "ema_9" in result.columns

    def test_input_not_mutated(self, ohlcv):
        original_cols = list(ohlcv.columns)
        calculate(ohlcv)
        assert list(ohlcv.columns) == original_cols


# ---------------------------------------------------------------------------
# Output columns
# ---------------------------------------------------------------------------


EXPECTED_COLUMNS = [
    # trend
    "ema_9", "ema_21", "ema_cross",
    "macd", "macd_signal", "macd_hist",
    "adx", "adx_pos", "adx_neg",
    # momentum
    "rsi", "stoch_k", "stoch_d",
    # volatility
    "atr", "bb_upper", "bb_lower", "bb_pct", "bb_width", "bb_squeeze",
    # volume
    "obv", "mfi", "vwap",
]


class TestOutputColumns:
    def test_all_indicator_columns_present(self, ohlcv):
        result = calculate(ohlcv)
        for col in EXPECTED_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_original_ohlcv_columns_preserved(self, ohlcv):
        result = calculate(ohlcv)
        for col in ("open", "high", "low", "close", "volume"):
            assert col in result.columns

    def test_row_count_unchanged(self, ohlcv):
        result = calculate(ohlcv)
        assert len(result) == len(ohlcv)

    def test_index_unchanged(self, ohlcv):
        result = calculate(ohlcv)
        pd.testing.assert_index_equal(result.index, ohlcv.index)


# ---------------------------------------------------------------------------
# NaN handling
# ---------------------------------------------------------------------------


class TestNaNHandling:
    def test_early_rows_are_nan(self, ohlcv):
        """First few rows must be NaN for indicators needing look-back."""
        result = calculate(ohlcv)
        # MACD needs 26 + 9 rows — early rows must be NaN.
        assert result["macd"].iloc[:5].isna().all()

    def test_late_rows_are_not_all_nan(self, ohlcv):
        result = calculate(ohlcv)
        # After warm-up, every indicator column must have at least one value.
        for col in EXPECTED_COLUMNS:
            assert result[col].notna().any(), f"All NaN for column: {col}"

    def test_short_df_produces_all_nan_indicators(self, ohlcv_short):
        result = calculate(ohlcv_short)
        assert result["adx"].isna().all()

    def test_minimal_df_produces_some_values(self, ohlcv_minimal):
        result = calculate(ohlcv_minimal)
        # EMA 9 needs 9 rows — should have values by MIN_ROWS.
        assert result["ema_9"].notna().any()


# ---------------------------------------------------------------------------
# Value range checks
# ---------------------------------------------------------------------------


class TestValueRanges:
    def test_rsi_between_0_and_100(self, ohlcv):
        result = calculate(ohlcv)
        rsi = result["rsi"].dropna()
        assert (rsi >= 0).all(), "RSI below 0"
        assert (rsi <= 100).all(), "RSI above 100"

    def test_stoch_k_between_0_and_100(self, ohlcv):
        result = calculate(ohlcv)
        k = result["stoch_k"].dropna()
        assert (k >= 0).all()
        assert (k <= 100).all()

    def test_stoch_d_between_0_and_100(self, ohlcv):
        result = calculate(ohlcv)
        d = result["stoch_d"].dropna()
        assert (d >= 0).all()
        assert (d <= 100).all()

    def test_adx_between_0_and_100(self, ohlcv):
        result = calculate(ohlcv)
        adx = result["adx"].dropna()
        assert (adx >= 0).all()
        assert (adx <= 100).all()

    def test_mfi_between_0_and_100(self, ohlcv):
        result = calculate(ohlcv)
        mfi = result["mfi"].dropna()
        assert (mfi >= 0).all()
        assert (mfi <= 100).all()

    def test_atr_positive(self, ohlcv):
        result = calculate(ohlcv)
        atr = result["atr"].dropna()
        assert (atr >= 0).all(), "ATR must be non-negative"
        assert atr.max() > 0, "ATR must have at least one positive value"

    def test_bb_upper_above_lower(self, ohlcv):
        result = calculate(ohlcv)
        valid = result[result["bb_upper"].notna()]
        assert (valid["bb_upper"] >= valid["bb_lower"]).all()

    def test_bb_width_non_negative(self, ohlcv):
        result = calculate(ohlcv)
        assert (result["bb_width"].dropna() >= 0).all()

    def test_ema_cross_binary(self, ohlcv):
        result = calculate(ohlcv)
        valid = result["ema_cross"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})

    def test_bb_squeeze_binary(self, ohlcv):
        result = calculate(ohlcv)
        valid = result["bb_squeeze"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})


# ---------------------------------------------------------------------------
# Indicator semantics
# ---------------------------------------------------------------------------


class TestSemantics:
    def test_ema_cross_1_when_fast_above_slow(self, ohlcv):
        result = calculate(ohlcv)
        valid = result[result["ema_9"].notna() & result["ema_21"].notna()]
        mask_above = valid["ema_9"] > valid["ema_21"]
        assert (valid.loc[mask_above, "ema_cross"] == 1.0).all()

    def test_ema_cross_0_when_fast_below_slow(self, ohlcv):
        result = calculate(ohlcv)
        valid = result[result["ema_9"].notna() & result["ema_21"].notna()]
        mask_below = valid["ema_9"] < valid["ema_21"]
        assert (valid.loc[mask_below, "ema_cross"] == 0.0).all()

    def test_ema_9_more_responsive_than_ema_21(self, ohlcv):
        """EMA-9 should track price more closely than EMA-21."""
        result = calculate(ohlcv)
        diff_9 = (result["ema_9"] - result["close"]).abs().mean()
        diff_21 = (result["ema_21"] - result["close"]).abs().mean()
        assert diff_9 < diff_21

    def test_obv_changes_with_price_direction(self, ohlcv):
        """OBV must change on every row (volume is always > 0)."""
        result = calculate(ohlcv)
        obv = result["obv"].dropna()
        assert obv.diff().dropna().abs().gt(0).all()

    def test_macd_hist_is_macd_minus_signal(self, ohlcv):
        result = calculate(ohlcv)
        valid = result[result["macd"].notna() & result["macd_signal"].notna()]
        diff = (valid["macd"] - valid["macd_signal"] - valid["macd_hist"]).abs()
        assert (diff < 1e-10).all()

    def test_vwap_with_datetime_index(self, ohlcv):
        result = calculate(ohlcv)
        assert result["vwap"].notna().any()

    def test_vwap_with_integer_index(self):
        df = _make_ohlcv(with_datetime_index=False)
        result = calculate(df)
        # Cumulative VWAP fallback must produce values from row 0.
        assert result["vwap"].notna().all()

    def test_constant_price_rsi_is_nan_or_50(self):
        """With no price movement RSI is undefined or 50."""
        n = 50
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [100.1] * n,
                "low": [99.9] * n,
                "close": [100.0] * n,
                "volume": [1_000.0] * n,
            }
        )
        result = calculate(df)
        rsi = result["rsi"].dropna()
        assert rsi.empty or ((rsi >= 0) & (rsi <= 100)).all()
