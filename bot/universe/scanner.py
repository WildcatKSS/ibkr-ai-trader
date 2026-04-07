"""
Universe scanner — fetches daily OHLCV data and scores candidate symbols.

The scanner is designed for testability: all data fetching goes through the
``DataProvider`` protocol, so production code injects an IBKR provider while
tests inject a mock.

Typical usage (from TradingEngine)::

    from bot.universe.scanner import scan, load_scan_config

    config = load_scan_config()
    results = scan(symbols, data_provider=ibkr_provider, config=config)
    # results is sorted descending by score; apply UNIVERSE_MAX_SYMBOLS cap
    watchlist = [r.symbol for r in results[: config.n_results]]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from bot.universe.criteria import (
    CriteriaConfig,
    CriteriaResult,
    MIN_BARS,
    score_candidate,
)
from bot.utils.logger import get_logger

log = get_logger("universe")

# Default pool of symbols scanned when UNIVERSE_POOL is not configured.
# Mix of large-cap stocks and major ETFs across sectors.
DEFAULT_POOL = (
    "SPY,QQQ,IWM,DIA,XLF,XLK,XLE,XLV,GLD,"
    "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AMD,AVGO,QCOM,INTC,CRM,ADBE,ORCL,NFLX,"
    "JPM,BAC,GS,MS,V,MA,"
    "JNJ,UNH,LLY,PFE,TMO,"
    "HD,WMT,COST,NKE,SBUX,MCD,"
    "CVX,XOM,COP,"
    "CAT,BA,GE,HON"
)


# ---------------------------------------------------------------------------
# DataProvider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DataProvider(Protocol):
    """
    Minimal interface for fetching historical daily bars.

    Implementations must be injected into ``scan()``.  The IBKR implementation
    lives in ``bot/core/`` and is wired up by ``TradingEngine``.
    """

    def fetch_daily_bars(self, symbol: str, n_bars: int) -> pd.DataFrame | None:
        """
        Return up to *n_bars* of daily OHLCV data for *symbol*, sorted
        ascending by date, or ``None`` if the symbol is unavailable.

        The DataFrame must contain at least the columns
        ``open``, ``high``, ``low``, ``close``, ``volume``
        (case-insensitive).
        """
        ...


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ScanConfig:
    """Runtime configuration for the scanner, loaded from MariaDB settings."""

    min_price: float = 5.0
    max_price: float = 500.0
    min_avg_volume: float = 500_000.0
    n_results: int = 10          # UNIVERSE_MAX_SYMBOLS — watchlist size
    bars_history: int = 250      # bars fetched per symbol (≥ MIN_BARS)
    criteria: CriteriaConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.criteria is None:
            self.criteria = CriteriaConfig()
        # Safety: always fetch at least MIN_BARS
        if self.bars_history < MIN_BARS:
            self.bars_history = MIN_BARS


def load_scan_config() -> ScanConfig:
    """
    Read all ``UNIVERSE_*`` settings from MariaDB via ``bot.utils.config.get``
    and return a populated ``ScanConfig``.

    Falls back to sensible defaults if a setting is missing or unparseable.
    """
    from bot.utils.config import get

    def _float(key: str, default: float) -> float:
        try:
            return float(get(key, str(default)))
        except (ValueError, TypeError):
            log.warning("Invalid setting, using default", key=key, default=default)
            return default

    def _int(key: str, default: int) -> int:
        try:
            return int(get(key, str(default)))
        except (ValueError, TypeError):
            log.warning("Invalid setting, using default", key=key, default=default)
            return default

    criteria = CriteriaConfig(
        ema9_period=_int("UNIVERSE_EMA9_PERIOD", 9),
        sma50_period=_int("UNIVERSE_SMA50_PERIOD", 50),
        sma200_period=_int("UNIVERSE_SMA200_PERIOD", 200),
        volume_ma_period=_int("UNIVERSE_VOLUME_MA_PERIOD", 20),
        hh_hl_lookback=_int("UNIVERSE_HH_HL_LOOKBACK", 20),
        body_ratio_min=_float("UNIVERSE_BODY_RATIO_MIN", 0.60),
        wick_ratio_max=_float("UNIVERSE_WICK_RATIO_MAX", 0.30),
        near_resistance_pct=_float("UNIVERSE_NEAR_RESISTANCE_PCT", 2.0),
        momentum_gap_pct=_float("UNIVERSE_MOMENTUM_GAP_PCT", 0.5),
        momentum_5d_return_pct=_float("UNIVERSE_MOMENTUM_5D_RETURN_PCT", 5.0),
    )

    return ScanConfig(
        min_price=_float("UNIVERSE_MIN_PRICE", 5.0),
        max_price=_float("UNIVERSE_MAX_PRICE", 500.0),
        min_avg_volume=_float("UNIVERSE_MIN_AVG_VOLUME", 500_000.0),
        n_results=_int("UNIVERSE_MAX_SYMBOLS", 10),
        bars_history=_int("UNIVERSE_BARS_HISTORY", 250),
        criteria=criteria,
    )


def get_pool() -> list[str]:
    """
    Return the list of candidate symbols from ``UNIVERSE_POOL`` setting,
    falling back to ``DEFAULT_POOL`` if the setting is empty.
    """
    from bot.utils.config import get

    raw = get("UNIVERSE_POOL", DEFAULT_POOL).strip()
    symbols = [s.strip() for s in raw.split(",") if s.strip()]
    return symbols if symbols else DEFAULT_POOL.split(",")


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan(
    symbols: list[str],
    data_provider: DataProvider,
    config: ScanConfig | None = None,
) -> list[CriteriaResult]:
    """
    Scan *symbols*, score each against universe criteria, and return a list
    sorted by score (highest first).

    Symbols that fail the price/volume pre-filters or have insufficient data
    are silently excluded from the result.

    Parameters
    ----------
    symbols:
        Candidate ticker symbols to evaluate.
    data_provider:
        Source of daily OHLCV bars.  In production this is the IBKR provider;
        in tests it is a mock.
    config:
        Scanner configuration.  If ``None``, ``ScanConfig()`` defaults are used
        (does not read from DB — use ``load_scan_config()`` for that).

    Returns
    -------
    list[CriteriaResult]
        Scored candidates, sorted descending by score.  May be empty.
    """
    if config is None:
        config = ScanConfig()

    results: list[CriteriaResult] = []

    for symbol in symbols:
        try:
            result = _evaluate_symbol(symbol, data_provider, config)
        except Exception as exc:  # noqa: BLE001
            log.warning("Error evaluating symbol — skipping", symbol=symbol, error=str(exc))
            continue

        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.score, reverse=True)

    log.info(
        "Universe scan complete",
        total_scanned=len(symbols),
        passed_filters=len(results),
    )
    return results


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _evaluate_symbol(
    symbol: str,
    data_provider: DataProvider,
    config: ScanConfig,
) -> CriteriaResult | None:
    """
    Fetch data for *symbol*, apply pre-filters, score, and return the result.
    Returns ``None`` when the symbol should be excluded.
    """
    df = data_provider.fetch_daily_bars(symbol, config.bars_history)

    if df is None or df.empty:
        log.debug("No data for symbol — skipping", symbol=symbol)
        return None

    # Normalise column names so pre-filters work regardless of casing.
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        log.warning(
            "Symbol data missing required columns — skipping",
            symbol=symbol,
            columns=list(df.columns),
        )
        return None

    # --- Price filter --------------------------------------------------------
    last_price = float(df["close"].iloc[-1])
    if not (config.min_price <= last_price <= config.max_price):
        log.debug(
            "Symbol excluded by price filter",
            symbol=symbol,
            price=last_price,
            min_price=config.min_price,
            max_price=config.max_price,
        )
        return None

    # --- Volume filter -------------------------------------------------------
    avg_vol = float(df["volume"].tail(20).mean())
    if avg_vol < config.min_avg_volume:
        log.debug(
            "Symbol excluded by volume filter",
            symbol=symbol,
            avg_volume=avg_vol,
            min_avg_volume=config.min_avg_volume,
        )
        return None

    # --- Score ---------------------------------------------------------------
    return score_candidate(df, symbol, config.criteria)
