"""
Universe selection criteria — pure scoring functions.

All functions are stateless and have no I/O.  They take a DataFrame of daily
OHLCV bars and return a ``CriteriaResult`` with a numeric score and individual
criterion flags.

Scoring weights
---------------
Core criteria (all seven must be True for ``passes_all=True``):

    price_above_ema9      10 pts
    price_above_sma50     10 pts
    price_above_sma200    10 pts
    ema9_rising           10 pts
    sma50_rising          10 pts
    higher_highs_lows     15 pts   (trend structure)
    volume_confirms       10 pts
    ─────────────────────────────
    core subtotal         75 pts

Bonus criteria (improve ranking):

    strong_candles         5 pts
    small_wicks            5 pts
    pullback_above_ema9    5 pts
    near_resistance       10 pts
    has_momentum          10 pts
    ─────────────────────────────
    bonus subtotal        35 pts

    max possible         110 pts  → normalised to 0–100
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import ta.trend

from bot.utils.logger import get_logger

log = get_logger("universe")

# Minimum daily bars needed (SMA200 requires 200, plus a safety margin).
MIN_BARS = 210

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class CriteriaConfig:
    """All configurable thresholds — sourced from DB settings at runtime."""

    ema9_period: int = 9
    sma50_period: int = 50
    sma200_period: int = 200
    volume_ma_period: int = 20       # bars for average-volume baseline
    hh_hl_lookback: int = 20         # bars for higher-highs / higher-lows check
    body_ratio_min: float = 0.60     # body / range ≥ this = "strong candle"
    wick_ratio_max: float = 0.30     # upper wick / range ≤ this = "small wick"
    strong_candle_min_ratio: float = 0.60  # fraction of recent candles that must be strong
    candle_lookback: int = 5         # bars assessed for candle quality
    near_resistance_pct: float = 2.0  # within X % below recent swing-high = near resistance
    momentum_gap_pct: float = 0.5    # open vs prev close gap ≥ X % = momentum
    momentum_5d_return_pct: float = 5.0  # 5-day return ≥ X % = momentum


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CriteriaResult:
    """Scoring output for one symbol."""

    symbol: str
    score: float          # 0–100, higher is better
    passes_all: bool      # True when all seven core criteria are satisfied

    # Core criteria (required for passes_all)
    price_above_ema9: bool
    price_above_sma50: bool
    price_above_sma200: bool
    ema9_rising: bool
    sma50_rising: bool
    higher_highs_lows: bool
    volume_confirms: bool

    # Bonus criteria (improve ranking)
    strong_candles: bool
    small_wicks: bool
    pullback_above_ema9: bool
    near_resistance: bool
    has_momentum: bool

    # Reference values (useful for logging and the Claude prompt)
    last_price: float
    avg_volume: float


# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------

_MAX_RAW_SCORE = 110.0  # sum of all weights defined in the module docstring


def score_candidate(
    df: pd.DataFrame,
    symbol: str,
    config: CriteriaConfig | None = None,
) -> CriteriaResult:
    """
    Score a daily OHLCV DataFrame against all universe-selection criteria.

    Parameters
    ----------
    df:
        Daily OHLCV DataFrame with columns ``open``, ``high``, ``low``,
        ``close``, ``volume`` (case-insensitive).  Must be sorted ascending.
    symbol:
        Ticker symbol — used only for labelling the result.
    config:
        Optional overrides; defaults to ``CriteriaConfig()``.

    Returns
    -------
    CriteriaResult
        If *df* has fewer than ``MIN_BARS`` rows or is missing required
        columns, a zero-score result with all criteria ``False`` is returned
        so that the caller can filter it out without crashing.
    """
    if config is None:
        config = CriteriaConfig()

    df = _normalise(df)
    if df is None or len(df) < MIN_BARS:
        return _empty_result(symbol)

    # --- Compute indicators --------------------------------------------------
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]

    ema9 = ta.trend.EMAIndicator(close=close, window=config.ema9_period).ema_indicator()
    sma50 = ta.trend.SMAIndicator(close=close, window=config.sma50_period).sma_indicator()
    sma200 = ta.trend.SMAIndicator(close=close, window=config.sma200_period).sma_indicator()
    vol_ma = volume.rolling(window=config.volume_ma_period).mean()

    last_close = float(close.iloc[-1])
    last_ema9 = float(ema9.iloc[-1])
    last_sma50 = float(sma50.iloc[-1])
    last_sma200 = float(sma200.iloc[-1])
    last_vol_ma = float(vol_ma.iloc[-1])

    if any(np.isnan(v) for v in (last_ema9, last_sma50, last_sma200, last_vol_ma)):
        return _empty_result(symbol)

    # --- Core criteria -------------------------------------------------------
    price_above_ema9 = last_close > last_ema9
    price_above_sma50 = last_close > last_sma50
    price_above_sma200 = last_close > last_sma200

    # EMA9 rising: current vs. 3 bars ago (avoids single-bar noise)
    ema9_rising = bool(float(ema9.iloc[-1]) > float(ema9.iloc[-4]))

    # SMA50 rising: current vs. 5 bars ago (weekly trend)
    sma50_rising = bool(float(sma50.iloc[-1]) > float(sma50.iloc[-6]))

    higher_highs_lows = _check_higher_highs_lows(high, low, config.hh_hl_lookback)
    volume_confirms = _check_volume_confirms(close, open_, volume, vol_ma)

    passes_all = all([
        price_above_ema9,
        price_above_sma50,
        price_above_sma200,
        ema9_rising,
        sma50_rising,
        higher_highs_lows,
        volume_confirms,
    ])

    # --- Bonus criteria ------------------------------------------------------
    strong_candles, small_wicks = _check_candle_quality(
        open_, high, low, close, config
    )
    pullback_above_ema9 = _check_pullback_above_ema9(close, ema9, config.candle_lookback)
    near_resistance = _check_near_resistance(close, high, config.near_resistance_pct)
    has_momentum = _check_momentum(
        open_, close, config.momentum_gap_pct, config.momentum_5d_return_pct
    )

    # --- Score ---------------------------------------------------------------
    raw = 0.0
    if price_above_ema9:    raw += 10
    if price_above_sma50:   raw += 10
    if price_above_sma200:  raw += 10
    if ema9_rising:         raw += 10
    if sma50_rising:        raw += 10
    if higher_highs_lows:   raw += 15
    if volume_confirms:     raw += 10
    if strong_candles:      raw += 5
    if small_wicks:         raw += 5
    if pullback_above_ema9: raw += 5
    if near_resistance:     raw += 10
    if has_momentum:        raw += 10

    score = round(raw / _MAX_RAW_SCORE * 100, 1)

    return CriteriaResult(
        symbol=symbol,
        score=score,
        passes_all=passes_all,
        price_above_ema9=price_above_ema9,
        price_above_sma50=price_above_sma50,
        price_above_sma200=price_above_sma200,
        ema9_rising=ema9_rising,
        sma50_rising=sma50_rising,
        higher_highs_lows=higher_highs_lows,
        volume_confirms=volume_confirms,
        strong_candles=strong_candles,
        small_wicks=small_wicks,
        pullback_above_ema9=pullback_above_ema9,
        near_resistance=near_resistance,
        has_momentum=has_momentum,
        last_price=last_close,
        avg_volume=last_vol_ma,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise(df: pd.DataFrame) -> pd.DataFrame | None:
    """Lowercase column names; return None if required columns are missing."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return None
    return df


def _empty_result(symbol: str) -> CriteriaResult:
    """Zero-score result used when data is insufficient or malformed."""
    return CriteriaResult(
        symbol=symbol,
        score=0.0,
        passes_all=False,
        price_above_ema9=False,
        price_above_sma50=False,
        price_above_sma200=False,
        ema9_rising=False,
        sma50_rising=False,
        higher_highs_lows=False,
        volume_confirms=False,
        strong_candles=False,
        small_wicks=False,
        pullback_above_ema9=False,
        near_resistance=False,
        has_momentum=False,
        last_price=0.0,
        avg_volume=0.0,
    )


def _check_higher_highs_lows(
    high: pd.Series, low: pd.Series, lookback: int
) -> bool:
    """
    True when the most recent ``lookback`` bars show a higher-highs /
    higher-lows structure.

    Split the lookback window into two halves.  The newer half must have a
    higher swing-high AND a higher swing-low than the older half.
    """
    if len(high) < lookback:
        return False
    window = lookback if lookback % 2 == 0 else lookback + 1
    recent = high.iloc[-window:]
    half = len(recent) // 2
    older_h, newer_h = recent.iloc[:half].max(), recent.iloc[half:].max()

    low_window = low.iloc[-window:]
    older_l, newer_l = low_window.iloc[:half].min(), low_window.iloc[half:].min()

    return bool((newer_h > older_h) and (newer_l > older_l))


def _check_volume_confirms(
    close: pd.Series,
    open_: pd.Series,
    volume: pd.Series,
    vol_ma: pd.Series,
) -> bool:
    """
    True when recent average volume is above the long-term average AND
    volume on green candles (close > open) is above average.
    """
    # Recent 5-bar average above 20-bar average
    recent_avg_vol = float(volume.tail(5).mean())
    long_avg_vol = float(vol_ma.iloc[-1])
    if np.isnan(long_avg_vol) or long_avg_vol <= 0:
        return False
    above_avg = recent_avg_vol >= long_avg_vol

    # Volume on green candles in last 5 bars is above average
    last5_close = close.tail(5)
    last5_open = open_.tail(5)
    last5_vol = volume.tail(5)
    green_mask = last5_close.values > last5_open.values
    if green_mask.any():
        green_vol = float(last5_vol.values[green_mask].mean())
        vol_rising_on_green = green_vol >= long_avg_vol
    else:
        vol_rising_on_green = False

    return above_avg and vol_rising_on_green


def _check_candle_quality(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    config: CriteriaConfig,
) -> tuple[bool, bool]:
    """
    Returns ``(strong_candles, small_wicks)`` for the last ``candle_lookback``
    bars.

    strong_candles — fraction of bars with body/range ≥ ``body_ratio_min``
                     is at least ``strong_candle_min_ratio``.
    small_wicks    — same fraction with upper-wick/range ≤ ``wick_ratio_max``.
    """
    n = config.candle_lookback
    o = open_.tail(n).values
    h = high.tail(n).values
    lo = low.tail(n).values
    c = close.tail(n).values

    range_ = h - lo
    # Avoid division by zero for doji candles
    nonzero = range_ > 0

    body = np.abs(c - o)
    body_ratio = np.where(nonzero, body / range_, 0.0)
    strong_frac = float((body_ratio >= config.body_ratio_min).mean())

    upper_wick = h - np.maximum(o, c)
    wick_ratio = np.where(nonzero, upper_wick / range_, 0.0)
    small_frac = float((wick_ratio <= config.wick_ratio_max).mean())

    return (
        strong_frac >= config.strong_candle_min_ratio,
        small_frac >= config.strong_candle_min_ratio,
    )


def _check_pullback_above_ema9(
    close: pd.Series, ema9: pd.Series, lookback: int
) -> bool:
    """True when none of the last ``lookback`` closes dropped below EMA9."""
    c = close.tail(lookback).values
    e = ema9.tail(lookback).values
    valid = ~np.isnan(e)
    if not valid.any():
        return False
    return bool(np.all(c[valid] >= e[valid]))


def _check_near_resistance(
    close: pd.Series, high: pd.Series, near_pct: float
) -> bool:
    """
    True when the last close is within ``near_pct`` % below the most recent
    swing-high (highest high of the last 20 bars, excluding the last 3).

    This signals that a breakout attempt is likely imminent.
    """
    if len(high) < 23:
        return False
    swing_high = float(high.iloc[-20:-3].max())
    last_close = float(close.iloc[-1])
    lower_bound = swing_high * (1.0 - near_pct / 100.0)
    return bool(lower_bound <= last_close <= swing_high * 1.01)


def _check_momentum(
    open_: pd.Series,
    close: pd.Series,
    gap_pct: float,
    five_day_return_pct: float,
) -> bool:
    """
    True when there is a recent gap-up OR strong 5-day return.

    gap_up  — most recent open vs. prior close ≥ ``gap_pct`` %.
    5d_ret  — return from 5 bars ago to latest close ≥ ``five_day_return_pct`` %.
    """
    if len(close) < 7:
        return False

    prev_close = float(close.iloc[-2])
    last_open = float(open_.iloc[-1])
    gap = (last_open - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0

    base = float(close.iloc[-6])
    last = float(close.iloc[-1])
    five_day_ret = (last - base) / base * 100.0 if base > 0 else 0.0

    return bool((gap >= gap_pct) or (five_day_ret >= five_day_return_pct))
