"""
Tests for bot/universe/criteria.py

All tests use synthetic OHLCV DataFrames — no external data, no API calls.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.universe.criteria import (
    MIN_BARS,
    CriteriaConfig,
    CriteriaResult,
    score_candidate,
    _check_higher_highs_lows,
    _check_momentum,
    _check_near_resistance,
    _check_pullback_above_ema9,
    _check_volume_confirms,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 250, trend: float = 0.001, seed: int = 42) -> pd.DataFrame:
    """
    Synthetic trending daily OHLCV DataFrame.

    trend > 0 → uptrend (price rises each bar on average)
    trend < 0 → downtrend

    Volume is designed so that recent bars have higher volume than older
    bars, ensuring volume_confirms=True in bullish setups.
    """
    rng = np.random.default_rng(seed)
    base = 100.0
    closes = [base]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + trend + rng.normal(0, 0.005)))

    closes = np.array(closes)
    highs = closes * (1 + rng.uniform(0.002, 0.012, n))
    lows = closes * (1 - rng.uniform(0.002, 0.012, n))
    opens = lows + (highs - lows) * rng.uniform(0.2, 0.8, n)

    # Stable base volume; last 20 bars are higher so recent avg > 20-bar avg.
    volumes = np.full(n, 800_000.0)
    volumes[-20:] = 2_000_000.0

    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _make_perfect_bullish(n: int = 250) -> pd.DataFrame:
    """
    Deterministic uptrend that satisfies EVERY criterion reliably.

    - Linear price rise → price > EMA9 > SMA50 > SMA200, both MAs rising, HH/HL
    - Opens near the low → all candles green, small upper wicks, large bodies
    - Volume trending up → recent avg > 20-bar avg, green candle vol above avg
    - 5-day return ≈ 5 % → has_momentum=True
    """
    closes = np.linspace(50.0, 100.0, n)
    highs = closes * 1.005
    lows = closes * 0.995
    opens = lows + (highs - lows) * 0.05   # near low → green candle
    volumes = np.linspace(800_000.0, 2_000_000.0, n)
    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _make_bearish(n: int = 250) -> pd.DataFrame:
    return _make_ohlcv(n=n, trend=-0.002)


@pytest.fixture()
def bullish_df() -> pd.DataFrame:
    return _make_ohlcv(n=250, trend=0.002)


@pytest.fixture()
def bearish_df() -> pd.DataFrame:
    return _make_bearish()


@pytest.fixture()
def config() -> CriteriaConfig:
    return CriteriaConfig()


# ---------------------------------------------------------------------------
# TestScoreCandidate — top-level function
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def test_returns_criteria_result(self, bullish_df, config):
        result = score_candidate(bullish_df, "AAPL", config)
        assert isinstance(result, CriteriaResult)
        assert result.symbol == "AAPL"

    def test_score_is_between_0_and_100(self, bullish_df, config):
        result = score_candidate(bullish_df, "AAPL", config)
        assert 0.0 <= result.score <= 100.0

    def test_bullish_trend_scores_high(self, bullish_df, config):
        result = score_candidate(bullish_df, "AAPL", config)
        # A clear uptrend should score well above 50
        assert result.score >= 50.0

    def test_bearish_trend_scores_low(self, bearish_df, config):
        result = score_candidate(bearish_df, "BEAR", config)
        assert result.score < 50.0

    def test_bullish_trend_passes_all(self, config):
        # Use deterministic data to guarantee all criteria pass reliably.
        df = _make_perfect_bullish()
        result = score_candidate(df, "AAPL", config)
        assert result.passes_all is True

    def test_bearish_trend_fails_all(self, bearish_df, config):
        result = score_candidate(bearish_df, "BEAR", config)
        assert result.passes_all is False

    def test_short_df_returns_zero_score(self, config):
        short_df = _make_ohlcv(n=MIN_BARS - 1)
        result = score_candidate(short_df, "X", config)
        assert result.score == 0.0
        assert result.passes_all is False

    def test_minimum_bars_accepted(self, config):
        df = _make_ohlcv(n=MIN_BARS, trend=0.002)
        result = score_candidate(df, "X", config)
        # Should not crash; score may be 0 if SMA200 warmup isn't complete,
        # but function must return a valid result
        assert isinstance(result, CriteriaResult)

    def test_missing_column_returns_zero_score(self, config):
        df = _make_ohlcv()
        df = df.drop(columns=["volume"])
        result = score_candidate(df, "X", config)
        assert result.score == 0.0

    def test_uppercase_columns_normalised(self, config):
        df = _make_ohlcv()
        df.columns = [c.upper() for c in df.columns]
        result = score_candidate(df, "X", config)
        assert isinstance(result, CriteriaResult)

    def test_last_price_populated(self, bullish_df, config):
        result = score_candidate(bullish_df, "AAPL", config)
        assert result.last_price == pytest.approx(float(bullish_df["close"].iloc[-1]))

    def test_avg_volume_populated(self, bullish_df, config):
        result = score_candidate(bullish_df, "AAPL", config)
        assert result.avg_volume > 0

    def test_default_config_used_when_none(self, bullish_df):
        result = score_candidate(bullish_df, "AAPL", None)
        assert isinstance(result, CriteriaResult)

    def test_score_higher_for_better_candidate(self, config):
        strong = score_candidate(_make_ohlcv(trend=0.003), "STR", config)
        weak = score_candidate(_make_ohlcv(trend=-0.001), "WEK", config)
        assert strong.score >= weak.score


# ---------------------------------------------------------------------------
# TestCoreCriteria
# ---------------------------------------------------------------------------


class TestCoreCriteria:
    def test_price_above_ema9_true_in_uptrend(self, bullish_df, config):
        result = score_candidate(bullish_df, "X", config)
        assert result.price_above_ema9 is True

    def test_price_above_sma50_true_in_uptrend(self, bullish_df, config):
        result = score_candidate(bullish_df, "X", config)
        assert result.price_above_sma50 is True

    def test_price_above_sma200_true_in_uptrend(self, bullish_df, config):
        result = score_candidate(bullish_df, "X", config)
        assert result.price_above_sma200 is True

    def test_ema9_rising_in_uptrend(self, bullish_df, config):
        result = score_candidate(bullish_df, "X", config)
        assert result.ema9_rising is True

    def test_sma50_rising_in_uptrend(self, bullish_df, config):
        result = score_candidate(bullish_df, "X", config)
        assert result.sma50_rising is True


# ---------------------------------------------------------------------------
# TestHigherHighsLows
# ---------------------------------------------------------------------------


class TestHigherHighsLows:
    def test_uptrend_has_hh_hl(self):
        n = 40
        highs = pd.Series(np.linspace(100, 120, n))
        lows = pd.Series(np.linspace(95, 115, n))
        assert _check_higher_highs_lows(highs, lows, lookback=20) is True

    def test_downtrend_lacks_hh_hl(self):
        n = 40
        highs = pd.Series(np.linspace(120, 100, n))
        lows = pd.Series(np.linspace(115, 95, n))
        assert _check_higher_highs_lows(highs, lows, lookback=20) is False

    def test_insufficient_bars_returns_false(self):
        highs = pd.Series([100.0, 101.0])
        lows = pd.Series([99.0, 100.0])
        assert _check_higher_highs_lows(highs, lows, lookback=20) is False


# ---------------------------------------------------------------------------
# TestVolumeConfirms
# ---------------------------------------------------------------------------


class TestVolumeConfirms:
    def test_high_volume_on_green_candles_passes(self):
        n = 30
        close = pd.Series(np.linspace(100, 110, n))
        open_ = close - 0.5          # all green candles
        # Volume on green candles well above 20-bar average
        volume = pd.Series([1_000_000.0] * n)
        vol_ma = volume.rolling(20).mean()
        assert _check_volume_confirms(close, open_, volume, vol_ma) is True

    def test_low_volume_fails(self):
        n = 30
        close = pd.Series(np.linspace(100, 110, n))
        open_ = close - 0.5
        # Recent volume half the average
        volume = pd.Series([1_000_000.0] * 25 + [400_000.0] * 5)
        vol_ma = volume.rolling(20).mean()
        assert _check_volume_confirms(close, open_, volume, vol_ma) is False


# ---------------------------------------------------------------------------
# TestNearResistance
# ---------------------------------------------------------------------------


class TestNearResistance:
    # _check_near_resistance uses iloc[-20:-3] so the swing high must fall
    # within that window.  Series length must be ≥ 23 for the guard to pass.
    # Layout (length 30): [100]*10 + [110]*5 + [100]*12 + [108]*3
    # iloc[-20:-3] = iloc[10:27] → max = 110.0  ✓

    def test_within_2pct_below_resistance(self):
        # Swing high 110, current price 108 (≈1.8% below) → near resistance
        highs = pd.Series([100.0] * 10 + [110.0] * 5 + [100.0] * 12 + [108.0] * 3)
        close = pd.Series([100.0] * 27 + [108.0] * 3)
        assert _check_near_resistance(close, highs, near_pct=2.0) is True

    def test_far_below_resistance_is_false(self):
        # Swing high 110, current 100 (≈9% below) → not near resistance
        highs = pd.Series([100.0] * 10 + [110.0] * 5 + [100.0] * 15)
        close = pd.Series([100.0] * 30)
        assert _check_near_resistance(close, highs, near_pct=2.0) is False

    def test_above_resistance_is_false(self):
        # Old resistance at 110; price broke out to 115 in the most recent 2 bars.
        # iloc[-20:-3] captures the old resistance zone (max=110).
        # 115 > 1.01 * 110 → not "near" resistance → False.
        highs = pd.Series([100.0] * 10 + [110.0] * 18 + [115.0] * 2)  # len=30
        close = pd.Series([100.0] * 28 + [115.0] * 2)
        assert _check_near_resistance(close, highs, near_pct=2.0) is False

    def test_insufficient_bars_returns_false(self):
        highs = pd.Series([110.0, 108.0])
        close = pd.Series([100.0, 108.0])
        assert _check_near_resistance(close, highs, near_pct=2.0) is False


# ---------------------------------------------------------------------------
# TestMomentum
# ---------------------------------------------------------------------------


class TestMomentum:
    def test_gap_up_triggers_momentum(self):
        close = pd.Series([100.0] * 8)
        open_ = pd.Series([100.0] * 7 + [101.0])  # 1% gap vs prev close 100
        assert _check_momentum(open_, close, gap_pct=0.5, five_day_return_pct=5.0) is True

    def test_strong_5d_return_triggers_momentum(self):
        # 5-day return ≈ 6%
        close = pd.Series([100.0, 100.0, 102.0, 103.0, 104.0, 105.0, 106.0])
        open_ = pd.Series([99.5] * 7)  # small opens, no gap
        assert _check_momentum(open_, close, gap_pct=0.5, five_day_return_pct=5.0) is True

    def test_no_momentum_returns_false(self):
        close = pd.Series([100.0] * 8)
        open_ = pd.Series([100.1] * 8)  # tiny opens, no gap
        assert _check_momentum(open_, close, gap_pct=0.5, five_day_return_pct=5.0) is False

    def test_insufficient_bars_returns_false(self):
        close = pd.Series([100.0, 101.0])
        open_ = pd.Series([99.0, 101.5])
        assert _check_momentum(open_, close, gap_pct=0.5, five_day_return_pct=5.0) is False


# ---------------------------------------------------------------------------
# TestPullbackAboveEMA9
# ---------------------------------------------------------------------------


class TestPullbackAboveEMA9:
    def test_all_closes_above_ema9(self):
        import ta.trend
        n = 50
        close = pd.Series(np.linspace(100, 115, n))
        ema9 = ta.trend.EMAIndicator(close=close, window=9).ema_indicator()
        assert _check_pullback_above_ema9(close, ema9, lookback=5) is True

    def test_close_below_ema9_returns_false(self):
        import ta.trend
        n = 50
        close_vals = list(np.linspace(100, 115, n - 1)) + [85.0]  # last bar crashes
        close = pd.Series(close_vals)
        ema9 = ta.trend.EMAIndicator(close=close, window=9).ema_indicator()
        assert _check_pullback_above_ema9(close, ema9, lookback=5) is False


# ---------------------------------------------------------------------------
# TestCustomConfig
# ---------------------------------------------------------------------------


class TestCustomConfig:
    def test_custom_periods_used(self, bullish_df):
        config = CriteriaConfig(ema9_period=5, sma50_period=20, sma200_period=50)
        result = score_candidate(bullish_df, "X", config)
        assert isinstance(result, CriteriaResult)

    def test_strict_body_ratio_reduces_bonus(self, bullish_df):
        loose = CriteriaConfig(body_ratio_min=0.10)
        strict = CriteriaConfig(body_ratio_min=0.99)
        r_loose = score_candidate(bullish_df, "X", loose)
        r_strict = score_candidate(bullish_df, "X", strict)
        assert r_loose.score >= r_strict.score
