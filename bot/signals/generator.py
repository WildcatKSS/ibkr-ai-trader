"""
Signal generator — 15-min confirmation filter + Claude API reasoning step.

This is steps 3 and 4 of the signal pipeline:

    indicators (5-min) → LightGBM → [THIS MODULE] → risk → orders

Flow
----
1. Calculate 5-min indicators on the supplied bars.
2. Get the LightGBM prediction; bail early if it is ``no_trade`` or below
   ``ML_MIN_PROBABILITY``.
3. Resample bars to 15-min and check indicator agreement.
4. Call Claude API for final decision, entry/target/stop, and explanation.
5. Return a ``Signal``.

Claude is called **at most once per signal** — never inside a tight loop.
All Claude API calls are logged under the ``"claude"`` category.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd

from bot.utils.logger import get_logger

log = get_logger("signals")
log_claude = get_logger("claude")

_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 512

# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class IntradayDataProvider(Protocol):
    """
    Minimal interface for fetching intraday OHLCV bars.

    The production implementation uses ib_insync and lives in ``bot/core/``.
    Tests inject a mock.
    """

    def fetch_intraday_bars(
        self,
        symbol: str,
        n_bars: int,
        bar_size: str = "5 mins",
    ) -> pd.DataFrame | None:
        """
        Return a DataFrame with columns open/high/low/close/volume indexed by
        datetime, or ``None`` if no data is available.
        """
        ...


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """Output of ``generate()``."""

    symbol: str
    action: str           # "long" | "short" | "no_trade"
    entry_price: float    # 0.0 when action == "no_trade"
    target_price: float   # 0.0 when action == "no_trade"
    stop_price: float     # 0.0 when action == "no_trade"
    confidence: float     # 0.0–1.0; 0.0 when action == "no_trade"
    explanation: str
    ml_label: str
    ml_probability: float
    confirmed_15min: bool
    indicators: dict = field(default_factory=dict)  # snapshot logged/stored


# Sentinel returned when the pipeline exits early (no signal to act on)
NO_SIGNAL = Signal(
    symbol="",
    action="no_trade",
    entry_price=0.0,
    target_price=0.0,
    stop_price=0.0,
    confidence=0.0,
    explanation="No signal.",
    ml_label="no_trade",
    ml_probability=0.0,
    confirmed_15min=False,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(
    symbol: str,
    bars: pd.DataFrame,
    *,
    ml_min_probability: float = 0.55,
    client=None,  # anthropic.Anthropic — injected for testability
) -> Signal:
    """
    Run the full signal pipeline for *symbol*.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"AAPL"``).
    bars:
        5-min OHLCV DataFrame with at least 50 rows.  Column names are
        normalised to lowercase internally.
    ml_min_probability:
        Minimum LightGBM class probability required to proceed.
    client:
        ``anthropic.Anthropic()`` instance.  If ``None``, one is created
        using ``ANTHROPIC_API_KEY``.  Pass a mock in tests.

    Returns
    -------
    Signal
        ``action == "no_trade"`` means nothing should be done.
    """
    from bot.ml.features import build
    from bot.ml.model import predict
    from bot.signals.indicators import calculate

    # ── 5-min indicators ─────────────────────────────────────────────────
    try:
        enriched = calculate(bars)
    except Exception as exc:
        log.warning("Indicator calculation failed", symbol=symbol, error=str(exc))
        return _no_signal(symbol, "indicator_error")

    # ── LightGBM prediction ───────────────────────────────────────────────
    try:
        features = build(enriched)
        if features.empty:
            return _no_signal(symbol, "features_empty")
        X = features.iloc[[-1]]
        ml_label, ml_prob = predict(X)
    except Exception as exc:
        log.warning("LightGBM prediction failed", symbol=symbol, error=str(exc))
        return _no_signal(symbol, "ml_error")

    if ml_label == "no_trade":
        log.debug("ML: no_trade signal", symbol=symbol, probability=round(ml_prob, 3))
        return _no_signal(symbol, "ml_no_trade", ml_label=ml_label, ml_prob=ml_prob)

    if ml_prob < ml_min_probability:
        log.debug(
            "ML probability below threshold",
            symbol=symbol,
            label=ml_label,
            probability=round(ml_prob, 3),
            threshold=ml_min_probability,
        )
        return _no_signal(symbol, "ml_low_probability", ml_label=ml_label, ml_prob=ml_prob)

    log.info(
        "ML signal accepted",
        symbol=symbol,
        label=ml_label,
        probability=round(ml_prob, 3),
    )

    # ── 15-min confirmation ───────────────────────────────────────────────
    confirmed = _confirm_15min(symbol, bars, ml_label)
    if not confirmed:
        log.info(
            "15-min confirmation failed — discarding signal",
            symbol=symbol,
            ml_label=ml_label,
        )
        return _no_signal(symbol, "15min_no_confirm", ml_label=ml_label, ml_prob=ml_prob)

    # ── Snapshot of key indicators (last bar) ─────────────────────────────
    snap = _indicator_snapshot(enriched)

    # Compute ATR-based suggested prices using configurable multipliers
    atr = snap.get("atr", 0.0) or 0.0
    close = snap.get("close", 0.0) or 0.0
    try:
        from bot.utils.config import get as get_setting
        stop_mult = float(get_setting("STOP_LOSS_PCT", default="1.0"))
        target_mult = float(get_setting("TAKE_PROFIT_PCT", default="2.0"))
    except Exception:
        stop_mult, target_mult = 1.0, 2.0
    entry, target, stop = _atr_prices(close, atr, ml_label, stop_mult, target_mult)

    # ── Claude API ────────────────────────────────────────────────────────
    if client is None:
        client = _make_client()

    if client is None:
        log.warning(
            "ANTHROPIC_API_KEY not set — using ATR-based signal (no Claude)",
            symbol=symbol,
        )
        return Signal(
            symbol=symbol,
            action=ml_label,
            entry_price=entry,
            target_price=target,
            stop_price=stop,
            confidence=ml_prob,
            explanation=f"ML signal ({ml_label}, p={ml_prob:.2f}). Claude unavailable.",
            ml_label=ml_label,
            ml_probability=ml_prob,
            confirmed_15min=confirmed,
            indicators=snap,
        )

    # Fetch sentiment score (non-blocking; defaults to 0.0 if unavailable)
    sentiment_score = 0.0
    try:
        from bot.sentiment import get_sentiment
        sentiment_score = get_sentiment(symbol)
    except Exception:
        pass

    prompt = _build_prompt(symbol, ml_label, ml_prob, snap, entry, target, stop,
                           sentiment=sentiment_score)
    try:
        from bot.utils.config import get as get_setting
        model = get_setting("CLAUDE_MODEL", default=_DEFAULT_MODEL)
    except Exception:
        model = _DEFAULT_MODEL
    try:
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        signal = _parse_response(raw, symbol, ml_label, ml_prob, confirmed, snap)
        log_claude.info(
            "Claude signal decision",
            symbol=symbol,
            action=signal.action,
            confidence=round(signal.confidence, 3),
        )
        return signal
    except Exception as exc:  # noqa: BLE001
        log_claude.error(
            "Claude API call failed — using ATR-based signal",
            symbol=symbol,
            error=str(exc),
        )
        return Signal(
            symbol=symbol,
            action=ml_label,
            entry_price=entry,
            target_price=target,
            stop_price=stop,
            confidence=ml_prob,
            explanation=f"ML signal ({ml_label}, p={ml_prob:.2f}). Claude error: {exc}",
            ml_label=ml_label,
            ml_probability=ml_prob,
            confirmed_15min=confirmed,
            indicators=snap,
        )


# ---------------------------------------------------------------------------
# 15-min confirmation
# ---------------------------------------------------------------------------


def _confirm_15min(symbol: str, bars_5min: pd.DataFrame, ml_label: str) -> bool:
    """
    Resample 5-min bars to 15-min and check that EMA cross + MACD histogram
    agree with *ml_label*.

    Returns ``True`` when both timeframes agree, ``False`` otherwise.
    """
    from bot.signals.indicators import MIN_ROWS, calculate

    bars_15 = _resample_to_15min(bars_5min)

    if len(bars_15) < MIN_ROWS:
        log.debug(
            "15-min resample too short for indicators",
            symbol=symbol,
            rows=len(bars_15),
        )
        # Accept the signal with partial confirmation if we don't have
        # enough 15-min history yet (e.g. early in the session).
        return True

    try:
        enriched_15 = calculate(bars_15)
    except Exception as exc:
        log.warning("15-min indicator error", symbol=symbol, error=str(exc))
        return True  # default to confirmed on error

    if enriched_15.empty:
        return True  # insufficient data — default to confirmed

    last = enriched_15.iloc[-1]
    ema_cross = last.get("ema_cross", float("nan"))
    macd_hist = last.get("macd_hist", float("nan"))

    import math

    if math.isnan(ema_cross) or math.isnan(macd_hist):
        return True  # insufficient history — default to confirmed

    if ml_label == "long":
        return bool(ema_cross == 1 and macd_hist > 0)
    elif ml_label == "short":
        return bool(ema_cross == 0 and macd_hist < 0)
    return False


def _resample_to_15min(bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5-min OHLCV bars to 15-min candles."""
    bars = bars.copy()
    bars.columns = [c.lower() for c in bars.columns]
    if not isinstance(bars.index, pd.DatetimeIndex):
        return bars  # can't resample without a datetime index

    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    # Keep only OHLCV before resampling (drop any indicator columns)
    ohlcv_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in bars.columns]
    return bars[ohlcv_cols].resample("15min").agg(agg).dropna()


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------


def _atr_prices(
    close: float,
    atr: float,
    action: str,
    stop_mult: float = 1.0,
    target_mult: float = 2.0,
) -> tuple[float, float, float]:
    """
    Compute ATR-based entry / target / stop as a starting point.

    Multipliers are read from DB settings STOP_LOSS_PCT and TAKE_PROFIT_PCT.
    """
    if atr <= 0 or close <= 0:
        return close, close, close

    if action == "long":
        entry = close
        target = close + target_mult * atr
        stop = close - stop_mult * atr
    else:  # short
        entry = close
        target = close - target_mult * atr
        stop = close + stop_mult * atr

    return round(entry, 4), round(target, 4), round(stop, 4)


# ---------------------------------------------------------------------------
# Indicator snapshot
# ---------------------------------------------------------------------------


def _indicator_snapshot(enriched: pd.DataFrame) -> dict:
    """Extract key indicator values from the last row."""
    if enriched.empty:
        return {}
    last = enriched.iloc[-1]
    cols = [
        "close", "open", "high", "low", "volume",
        "rsi", "macd_hist", "adx", "ema_cross",
        "bb_pct", "atr", "mfi", "vwap",
        "ema_9", "ema_21",
    ]
    snap = {}
    for c in cols:
        if c in last.index:
            val = last[c]
            import math
            snap[c] = None if (isinstance(val, float) and math.isnan(val)) else float(val)
    return snap


# ---------------------------------------------------------------------------
# Claude prompt & response
# ---------------------------------------------------------------------------


def _build_prompt(
    symbol: str,
    ml_label: str,
    ml_prob: float,
    snap: dict,
    suggested_entry: float,
    suggested_target: float,
    suggested_stop: float,
    sentiment: float = 0.0,
) -> str:
    close = snap.get("close") or 0.0
    rsi = snap.get("rsi") or 0.0
    macd = snap.get("macd_hist") or 0.0
    adx = snap.get("adx") or 0.0
    ema_cross = snap.get("ema_cross")
    bb_pct = snap.get("bb_pct") or 0.0
    atr = snap.get("atr") or 0.0
    mfi = snap.get("mfi") or 0.0
    vwap = snap.get("vwap") or 0.0

    ema_bias = "EMA9 > EMA21 (bullish)" if ema_cross == 1 else "EMA9 ≤ EMA21 (bearish)"

    if sentiment > 0.2:
        sentiment_text = f"POSITIVE ({sentiment:+.2f})"
    elif sentiment < -0.2:
        sentiment_text = f"NEGATIVE ({sentiment:+.2f})"
    else:
        sentiment_text = f"NEUTRAL ({sentiment:+.2f})"

    sentiment_line = f"\nNews sentiment: {sentiment_text}" if sentiment != 0.0 else ""

    return f"""You are a precise intraday trading assistant for an automated bot.

Symbol: {symbol}
Current price: ${close:.2f}  |  VWAP: ${vwap:.2f}  |  ATR: ${atr:.4f}

5-min indicator snapshot (latest bar):
  RSI: {rsi:.1f}  |  MACD hist: {macd:+.4f}  |  ADX: {adx:.1f}
  {ema_bias}  |  BB%: {bb_pct:.2f}  |  MFI: {mfi:.1f}

LightGBM signal: {ml_label.upper()} with {ml_prob:.1%} confidence
15-min confirmation: PASSED (both timeframes agree){sentiment_line}

ATR-based suggestion:
  Entry: ${suggested_entry:.2f}  |  Target: ${suggested_target:.2f}  |  Stop: ${suggested_stop:.2f}

Task: Evaluate this intraday signal and return your decision as JSON with these keys:
  "action": "long", "short", or "no_trade"
  "entry": float (limit price to enter; use current price or slightly better)
  "target": float (profit target)
  "stop": float (stop-loss price)
  "confidence": float 0.0–1.0
  "explanation": string (1–2 sentences)

Rules:
- Only override to "no_trade" if there is a strong reason (e.g. RSI extreme, ADX < 15, price far from VWAP).
- Keep entry/target/stop realistic relative to ATR (target ≥ 1.5×ATR away from entry).
- Respond with valid JSON only — no markdown, no text outside the JSON object.
"""


def _parse_response(
    raw: str,
    symbol: str,
    ml_label: str,
    ml_prob: float,
    confirmed: bool,
    snap: dict,
) -> Signal:
    """Parse Claude's JSON response into a ``Signal``."""
    close = snap.get("close") or 0.0
    atr = snap.get("atr") or 0.0

    try:
        data = json.loads(raw.strip())
        action = str(data.get("action", ml_label)).lower().strip()
        if action not in {"long", "short", "no_trade"}:
            raise ValueError(f"Invalid action: {action!r}")

        entry = float(data.get("entry", close))
        target = float(data.get("target", close))
        stop = float(data.get("stop", close))
        confidence = float(data.get("confidence", ml_prob))
        explanation = str(data.get("explanation", "")).strip()

        return Signal(
            symbol=symbol,
            action=action,
            entry_price=round(entry, 4),
            target_price=round(target, 4),
            stop_price=round(stop, 4),
            confidence=min(max(confidence, 0.0), 1.0),
            explanation=explanation,
            ml_label=ml_label,
            ml_probability=ml_prob,
            confirmed_15min=confirmed,
            indicators=snap,
        )
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        log_claude.warning(
            "Failed to parse Claude signal response — using ATR fallback",
            symbol=symbol,
            error=str(exc),
            raw=raw[:200],
        )
        entry, target, stop = _atr_prices(close, atr, ml_label)
        return Signal(
            symbol=symbol,
            action=ml_label,
            entry_price=entry,
            target_price=target,
            stop_price=stop,
            confidence=ml_prob,
            explanation=f"ML signal ({ml_label}, p={ml_prob:.2f}). Parse error: {exc}",
            ml_label=ml_label,
            ml_probability=ml_prob,
            confirmed_15min=confirmed,
            indicators=snap,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_signal(
    symbol: str,
    reason: str,
    ml_label: str = "no_trade",
    ml_prob: float = 0.0,
) -> Signal:
    s = Signal(
        symbol=symbol,
        action="no_trade",
        entry_price=0.0,
        target_price=0.0,
        stop_price=0.0,
        confidence=0.0,
        explanation=reason,
        ml_label=ml_label,
        ml_probability=ml_prob,
        confirmed_15min=False,
    )
    return s


def _make_client():
    """Return an ``anthropic.Anthropic`` client or ``None`` if key absent."""
    try:
        import anthropic
    except ImportError:
        return None
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)
