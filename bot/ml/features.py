"""
Feature engineering for the LightGBM signal model.

``build(df)`` takes the DataFrame produced by ``bot.signals.indicators.calculate``
(OHLCV + 21 indicator columns) and returns a new DataFrame with exactly
``FEATURE_NAMES`` columns, ready to be passed to LightGBM.

For inference, pass the full enriched DataFrame and slice the last row::

    features_df = build(enriched_df)
    X = features_df.iloc[[-1]]          # single-row DataFrame
    label, prob = model.predict(X)

For training, use the full feature DataFrame (NaN rows are dropped inside
``trainer.train``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.utils.logger import get_logger

log = get_logger("ml")

# ---------------------------------------------------------------------------
# Feature catalogue
# ---------------------------------------------------------------------------
# Any change here must be matched by retraining the model.
# The order defines the column order LightGBM expects at inference time.

FEATURE_NAMES: list[str] = [
    # ── Momentum ─────────────────────────────────────────────────────────────
    "rsi",              # 0–100 RSI
    "stoch_k",          # Stochastic %K
    "stoch_d",          # Stochastic %D (signal)
    "macd_hist",        # MACD histogram (momentum direction)
    # ── Trend ────────────────────────────────────────────────────────────────
    "adx",              # ADX trend strength
    "adx_pos",          # +DI directional movement
    "adx_neg",          # −DI directional movement
    "ema_cross",        # 1 when EMA9 > EMA21, else 0
    # ── Volatility ───────────────────────────────────────────────────────────
    "bb_pct",           # Bollinger %B (0=lower band, 1=upper band)
    "bb_width",         # Band width (volatility measure)
    "bb_squeeze",       # 1 when bands are contracting (breakout pending)
    "atr_pct",          # ATR as % of close (normalised volatility)
    # ── Volume ───────────────────────────────────────────────────────────────
    "mfi",              # Money Flow Index (0–100)
    "obv_slope",        # OBV 5-bar slope direction (+1 / 0 / −1)
    "volume_ratio",     # Current volume / 20-bar average
    # ── Price-relative ───────────────────────────────────────────────────────
    "close_vs_ema9",    # (close − EMA9) / close  [%]
    "close_vs_ema21",   # (close − EMA21) / close [%]
    "close_vs_vwap",    # (close − VWAP) / close  [%]
    # ── Candle structure ─────────────────────────────────────────────────────
    "body_ratio",       # |close − open| / (high − low)
    "upper_wick_ratio", # (high − max(open,close)) / (high − low)
    "lower_wick_ratio", # (min(open,close) − low) / (high − low)
    # ── Short-term returns ───────────────────────────────────────────────────
    "return_1bar",      # 1-bar log return (5 min)
    "return_3bar",      # 3-bar log return (15 min)
    "return_6bar",      # 6-bar log return (30 min)
]

# Columns the DataFrame must supply (beyond OHLCV).
_REQUIRED_INDICATOR_COLS = {
    "rsi", "stoch_k", "stoch_d", "macd_hist",
    "adx", "adx_pos", "adx_neg", "ema_cross",
    "bb_pct", "bb_width", "bb_squeeze", "atr",
    "mfi", "obv", "vwap",
    "ema_9", "ema_21",
}
_REQUIRED_OHLCV_COLS = {"open", "high", "low", "close", "volume"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the feature matrix from an enriched OHLCV DataFrame.

    Parameters
    ----------
    df:
        Output of ``bot.signals.indicators.calculate`` — must contain all
        OHLCV columns plus the 21 indicator columns.  Column names are
        normalised to lowercase internally.

    Returns
    -------
    pd.DataFrame
        Same index as *df*, columns exactly ``FEATURE_NAMES``.
        Rows where any required input is NaN will contain NaN in the
        corresponding feature columns (caller must drop them before training).

    Raises
    ------
    ValueError
        If required columns are missing.
    """
    df = _normalise(df)

    features = pd.DataFrame(index=df.index)

    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ── Momentum ─────────────────────────────────────────────────────────────
    features["rsi"] = df["rsi"]
    features["stoch_k"] = df["stoch_k"]
    features["stoch_d"] = df["stoch_d"]
    features["macd_hist"] = df["macd_hist"]

    # ── Trend ────────────────────────────────────────────────────────────────
    features["adx"] = df["adx"]
    features["adx_pos"] = df["adx_pos"]
    features["adx_neg"] = df["adx_neg"]
    features["ema_cross"] = df["ema_cross"]

    # ── Volatility ───────────────────────────────────────────────────────────
    features["bb_pct"] = df["bb_pct"]
    features["bb_width"] = df["bb_width"]
    features["bb_squeeze"] = df["bb_squeeze"]
    features["atr_pct"] = df["atr"] / close.replace(0, np.nan) * 100.0

    # ── Volume ───────────────────────────────────────────────────────────────
    features["mfi"] = df["mfi"]
    obv = df["obv"]
    features["obv_slope"] = np.sign(obv - obv.shift(5)).fillna(0)
    vol_ma = volume.rolling(20).mean()
    volume_ratio = volume / vol_ma.replace(0, np.nan)
    # Clamp extreme values to prevent inf from polluting the model.
    features["volume_ratio"] = volume_ratio.clip(upper=50.0)

    # ── Price-relative ───────────────────────────────────────────────────────
    features["close_vs_ema9"] = (close - df["ema_9"]) / close.replace(0, np.nan) * 100.0
    features["close_vs_ema21"] = (close - df["ema_21"]) / close.replace(0, np.nan) * 100.0
    features["close_vs_vwap"] = (close - df["vwap"]) / close.replace(0, np.nan) * 100.0

    # ── Candle structure ─────────────────────────────────────────────────────
    range_ = high - low
    safe_range = range_.replace(0, np.nan)  # doji candles → NaN (no meaningful ratio)
    features["body_ratio"] = (close - open_).abs() / safe_range
    max_oc = pd.concat([open_, close], axis=1).max(axis=1)
    min_oc = pd.concat([open_, close], axis=1).min(axis=1)
    features["upper_wick_ratio"] = (high - max_oc) / safe_range
    features["lower_wick_ratio"] = (min_oc - low) / safe_range

    # ── Short-term returns ───────────────────────────────────────────────────
    log_close = np.log(close.replace(0, np.nan))
    features["return_1bar"] = log_close - log_close.shift(1)
    features["return_3bar"] = log_close - log_close.shift(3)
    features["return_6bar"] = log_close - log_close.shift(6)

    return features[FEATURE_NAMES]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase column names and validate required columns."""
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    missing = (_REQUIRED_INDICATOR_COLS | _REQUIRED_OHLCV_COLS) - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    return df
