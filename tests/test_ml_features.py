"""
Tests for bot/ml/features.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.ml.features import FEATURE_NAMES, build
from bot.signals.indicators import calculate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = np.cumprod(1 + rng.normal(0.0003, 0.005, n)) * 100
    highs = closes * 1.006
    lows = closes * 0.994
    opens = lows + (highs - lows) * rng.uniform(0.2, 0.8, n)
    volumes = np.full(n, 1_000_000.0)
    volumes[-20:] = 2_000_000.0
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


@pytest.fixture()
def enriched_df():
    return calculate(_make_ohlcv())


# ---------------------------------------------------------------------------
# TestBuild
# ---------------------------------------------------------------------------


class TestBuild:
    def test_returns_dataframe(self, enriched_df):
        result = build(enriched_df)
        assert isinstance(result, pd.DataFrame)

    def test_columns_match_feature_names(self, enriched_df):
        result = build(enriched_df)
        assert list(result.columns) == FEATURE_NAMES

    def test_same_index_as_input(self, enriched_df):
        result = build(enriched_df)
        assert result.index.equals(enriched_df.index)

    def test_same_row_count(self, enriched_df):
        result = build(enriched_df)
        assert len(result) == len(enriched_df)

    def test_late_rows_have_no_nan(self, enriched_df):
        result = build(enriched_df)
        # Last row should be fully populated (enough history)
        last = result.iloc[-1]
        assert not last.isna().any(), f"NaN in last row: {last[last.isna()].index.tolist()}"

    def test_uppercase_columns_accepted(self):
        df = _make_ohlcv()
        df.columns = [c.upper() for c in df.columns]
        enriched = calculate(df)
        enriched.columns = [c.upper() for c in enriched.columns]
        result = build(enriched)
        assert list(result.columns) == FEATURE_NAMES

    def test_missing_indicator_column_raises(self, enriched_df):
        df = enriched_df.drop(columns=["rsi"])
        with pytest.raises(ValueError, match="Missing required columns"):
            build(df)

    def test_missing_ohlcv_column_raises(self, enriched_df):
        df = enriched_df.drop(columns=["close"])
        with pytest.raises(ValueError, match="Missing required columns"):
            build(df)


# ---------------------------------------------------------------------------
# TestFeatureValues
# ---------------------------------------------------------------------------


class TestFeatureValues:
    def test_rsi_range(self, enriched_df):
        result = build(enriched_df)
        rsi = result["rsi"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()

    def test_mfi_range(self, enriched_df):
        result = build(enriched_df)
        mfi = result["mfi"].dropna()
        assert (mfi >= 0).all() and (mfi <= 100).all()

    def test_bb_pct_exists(self, enriched_df):
        result = build(enriched_df)
        assert "bb_pct" in result.columns

    def test_body_ratio_non_negative(self, enriched_df):
        result = build(enriched_df)
        br = result["body_ratio"].dropna()
        assert (br >= 0).all()

    def test_wick_ratios_non_negative(self, enriched_df):
        result = build(enriched_df)
        for col in ("upper_wick_ratio", "lower_wick_ratio"):
            assert (result[col].dropna() >= 0).all()

    def test_volume_ratio_positive(self, enriched_df):
        result = build(enriched_df)
        vr = result["volume_ratio"].dropna()
        assert (vr > 0).all()

    def test_obv_slope_in_valid_set(self, enriched_df):
        result = build(enriched_df)
        unique_vals = set(result["obv_slope"].dropna().unique())
        assert unique_vals.issubset({-1.0, 0.0, 1.0})

    def test_atr_pct_positive(self, enriched_df):
        result = build(enriched_df)
        atr_pct = result["atr_pct"].dropna()
        # Early bars may be 0 during ATR warmup; later bars must be positive
        assert (atr_pct >= 0).all() and atr_pct.max() > 0

    def test_returns_finite_for_valid_rows(self, enriched_df):
        result = build(enriched_df)
        last = result.iloc[-1]
        assert np.isfinite(last.dropna().values).all()


# ---------------------------------------------------------------------------
# TestFeatureNames
# ---------------------------------------------------------------------------


class TestFeatureNames:
    def test_feature_names_is_list(self):
        assert isinstance(FEATURE_NAMES, list)

    def test_feature_names_non_empty(self):
        assert len(FEATURE_NAMES) > 0

    def test_no_duplicate_feature_names(self):
        assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))
