"""
Technical indicator calculation for ibkr-ai-trader.

All indicators are computed on 5-minute OHLCV candles using the ``ta``
library.  This is the **only** place in the codebase that imports ``ta``.

Usage::

    import pandas as pd
    from bot.signals.indicators import calculate

    # df must have columns: open, high, low, close, volume
    # index must be datetime (timezone-aware or naive)
    enriched = calculate(df)

Output columns (appended to a copy of the input DataFrame):

Trend
-----
ema_9, ema_21          Exponential moving averages (fast / slow)
ema_cross              1 when ema_9 > ema_21, else 0  (directional bias)
macd, macd_signal,     MACD line, signal line, histogram
macd_hist
adx, adx_pos,          Average Directional Index + directional indicators
adx_neg

Momentum
--------
rsi                    Relative Strength Index (14)
stoch_k, stoch_d       Stochastic oscillator %K and %D (14, 3, 3)

Volatility
----------
atr                    Average True Range (14) — used by risk module for
                       stop placement and position sizing
bb_upper, bb_lower,    Bollinger Bands (20, 2σ): bands + %B + width
bb_pct, bb_width
bb_squeeze             1 when bb_width < its 20-period rolling mean (squeeze)

Volume
------
obv                    On-Balance Volume
mfi                    Money Flow Index (14)
vwap                   Volume-Weighted Average Price (cumulative intraday)

All indicators are computed with ``fillna=False`` (default) so the caller
can clearly see which rows have insufficient history.  Early rows will
contain ``NaN`` values; downstream consumers (LightGBM, Claude) must
handle or drop them.
"""

from __future__ import annotations

import pandas as pd
import ta.momentum
import ta.trend
import ta.volatility
import ta.volume

from bot.utils.logger import get_logger

log = get_logger("signals")

# ---------------------------------------------------------------------------
# Required input columns
# ---------------------------------------------------------------------------

REQUIRED_COLS = frozenset({"open", "high", "low", "close", "volume"})

# Minimum number of rows recommended before all indicators produce non-NaN
# values.  ADX with window=14 requires roughly 2*window rows internally;
# using 50 gives a comfortable margin and covers all other lookbacks.
MIN_ROWS = 50

# All indicator column names produced by calculate().  Kept here so the
# short-history guard can pre-fill them with NaN without running ta.
_INDICATOR_COLS = (
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
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def calculate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators on *df* and return an enriched copy.

    Parameters
    ----------
    df:
        DataFrame with columns ``open``, ``high``, ``low``, ``close``,
        ``volume``.  Column names are case-insensitive and normalised
        internally.  The index should be a DatetimeIndex but this is not
        strictly required.

    Returns
    -------
    pd.DataFrame
        A copy of *df* with all indicator columns appended.  The input
        is never mutated.

    Raises
    ------
    ValueError
        If *df* is missing any required OHLCV column.
    """
    df = _validate_and_normalise(df)

    out = df.copy()

    if len(out) < MIN_ROWS:
        # Not enough history for reliable indicator computation.
        # Fill all indicator columns with NaN so callers can detect the gap.
        for col in _INDICATOR_COLS:
            out[col] = float("nan")
        log.debug("Indicators skipped — insufficient history", rows=len(out), min_rows=MIN_ROWS)
        return out

    _add_trend(out)
    _add_momentum(out)
    _add_volatility(out)
    _add_volume(out)

    log.debug(
        "Indicators calculated",
        rows=len(out),
        columns=len(out.columns) - len(df.columns),
    )
    return out


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_and_normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case column names and verify OHLCV columns are present."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"DataFrame is missing required OHLCV columns: {sorted(missing)}"
        )
    return df


# ---------------------------------------------------------------------------
# Indicator groups
# ---------------------------------------------------------------------------


def _add_trend(df: pd.DataFrame) -> None:
    """EMA crosses, MACD, ADX — written in-place."""

    # ── EMA 9 / 21 ────────────────────────────────────────────────────────
    df["ema_9"] = ta.trend.EMAIndicator(
        close=df["close"], window=9
    ).ema_indicator()

    df["ema_21"] = ta.trend.EMAIndicator(
        close=df["close"], window=21
    ).ema_indicator()

    # Binary directional-bias feature: 1 = fast above slow
    df["ema_cross"] = (df["ema_9"] > df["ema_21"]).astype(float)
    # Propagate NaN where either EMA is NaN
    df.loc[df["ema_9"].isna() | df["ema_21"].isna(), "ema_cross"] = float("nan")

    # ── MACD (12, 26, 9) ──────────────────────────────────────────────────
    macd = ta.trend.MACD(
        close=df["close"], window_slow=26, window_fast=12, window_sign=9
    )
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # ── ADX (14) ──────────────────────────────────────────────────────────
    adx = ta.trend.ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"], window=14
    )
    df["adx"] = adx.adx()
    df["adx_pos"] = adx.adx_pos()
    df["adx_neg"] = adx.adx_neg()


def _add_momentum(df: pd.DataFrame) -> None:
    """RSI, Stochastic — written in-place."""

    # ── RSI (14) ──────────────────────────────────────────────────────────
    df["rsi"] = ta.momentum.RSIIndicator(
        close=df["close"], window=14
    ).rsi()

    # ── Stochastic %K / %D (14, 3, 3) ────────────────────────────────────
    stoch = ta.momentum.StochasticOscillator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14,
        smooth_window=3,
    )
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()


def _add_volatility(df: pd.DataFrame) -> None:
    """ATR, Bollinger Bands — written in-place."""

    # ── ATR (14) ──────────────────────────────────────────────────────────
    df["atr"] = ta.volatility.AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=14
    ).average_true_range()

    # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────
    bb = ta.volatility.BollingerBands(
        close=df["close"], window=20, window_dev=2
    )
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_pct"] = bb.bollinger_pband()    # (price - lower) / (upper - lower)
    df["bb_width"] = bb.bollinger_wband()  # (upper - lower) / middle

    # Squeeze: band width below its own 20-period rolling mean
    rolling_mean_width = df["bb_width"].rolling(window=20).mean()
    df["bb_squeeze"] = (df["bb_width"] < rolling_mean_width).astype(float)
    df.loc[df["bb_width"].isna() | rolling_mean_width.isna(), "bb_squeeze"] = float("nan")


def _add_volume(df: pd.DataFrame) -> None:
    """OBV, MFI, VWAP — written in-place."""

    # ── OBV ───────────────────────────────────────────────────────────────
    df["obv"] = ta.volume.OnBalanceVolumeIndicator(
        close=df["close"], volume=df["volume"]
    ).on_balance_volume()

    # ── MFI (14) ──────────────────────────────────────────────────────────
    df["mfi"] = ta.volume.MFIIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        volume=df["volume"],
        window=14,
    ).money_flow_index()

    # ── VWAP (cumulative within session) ─────────────────────────────────
    # ta's VWAP resets per day when the index is a DatetimeIndex.
    # Fall back to a simple cumulative VWAP if the index is not datetime.
    if isinstance(df.index, pd.DatetimeIndex):
        df["vwap"] = ta.volume.VolumeWeightedAveragePrice(
            high=df["high"],
            low=df["low"],
            close=df["close"],
            volume=df["volume"],
            window=14,
        ).volume_weighted_average_price()
    else:
        # Cumulative VWAP fallback (no daily reset)
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        cumulative_tp_vol = (typical_price * df["volume"]).cumsum()
        cumulative_vol = df["volume"].cumsum()
        df["vwap"] = cumulative_tp_vol / cumulative_vol
