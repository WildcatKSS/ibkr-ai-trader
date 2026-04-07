"""
Tests for bot/universe/scanner.py

Uses a MockDataProvider — no IBKR connection, no external data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.universe.criteria import CriteriaResult
from bot.universe.scanner import (
    DEFAULT_POOL,
    ScanConfig,
    get_pool,
    scan,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 250, trend: float = 0.002, price: float = 100.0,
                volume: float = 1_000_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = [price]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + trend + rng.normal(0, 0.003)))
    closes = np.array(closes)
    highs = closes * 1.008
    lows = closes * 0.992
    opens = (highs + lows) / 2
    volumes = np.full(n, volume)
    idx = pd.date_range("2023-01-03", periods=n, freq="B")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


class MockDataProvider:
    """Returns pre-configured DataFrames keyed by symbol."""

    def __init__(self, data: dict[str, pd.DataFrame | None]):
        self._data = data

    def fetch_daily_bars(self, symbol: str, n_bars: int) -> pd.DataFrame | None:
        return self._data.get(symbol)


@pytest.fixture()
def bullish_provider():
    return MockDataProvider(
        {
            "AAPL": _make_ohlcv(trend=0.003),
            "MSFT": _make_ohlcv(trend=0.002),
            "NVDA": _make_ohlcv(trend=0.004),
        }
    )


@pytest.fixture()
def default_config():
    return ScanConfig()


# ---------------------------------------------------------------------------
# TestScan
# ---------------------------------------------------------------------------


class TestScan:
    def test_returns_list_of_criteria_results(self, bullish_provider, default_config):
        results = scan(["AAPL", "MSFT"], bullish_provider, default_config)
        assert all(isinstance(r, CriteriaResult) for r in results)

    def test_sorted_by_score_descending(self, bullish_provider, default_config):
        results = scan(["AAPL", "MSFT", "NVDA"], bullish_provider, default_config)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_symbols_returns_empty(self, bullish_provider, default_config):
        results = scan([], bullish_provider, default_config)
        assert results == []

    def test_unavailable_symbol_skipped(self, default_config):
        provider = MockDataProvider({"AAPL": _make_ohlcv(), "MISSING": None})
        results = scan(["AAPL", "MISSING"], provider, default_config)
        symbols = [r.symbol for r in results]
        assert "MISSING" not in symbols
        assert "AAPL" in symbols

    def test_symbol_below_min_price_excluded(self, default_config):
        config = ScanConfig(min_price=50.0)
        provider = MockDataProvider({"CHEAP": _make_ohlcv(price=10.0)})
        results = scan(["CHEAP"], provider, config)
        assert results == []

    def test_symbol_above_max_price_excluded(self, default_config):
        config = ScanConfig(max_price=200.0)
        provider = MockDataProvider({"PRICEY": _make_ohlcv(price=500.0)})
        results = scan(["PRICEY"], provider, config)
        assert results == []

    def test_symbol_below_min_volume_excluded(self):
        config = ScanConfig(min_avg_volume=500_000.0)
        provider = MockDataProvider({"THIN": _make_ohlcv(volume=100_000.0)})
        results = scan(["THIN"], provider, config)
        assert results == []

    def test_symbol_above_min_volume_included(self):
        config = ScanConfig(min_avg_volume=500_000.0)
        provider = MockDataProvider({"LIQUID": _make_ohlcv(volume=2_000_000.0)})
        results = scan(["LIQUID"], provider, config)
        assert len(results) == 1

    def test_none_config_uses_defaults(self, bullish_provider):
        results = scan(["AAPL"], bullish_provider, None)
        assert len(results) >= 0  # must not crash

    def test_exception_in_provider_skips_symbol(self, default_config):
        class BrokenProvider:
            def fetch_daily_bars(self, symbol, n_bars):
                raise RuntimeError("IBKR timeout")

        results = scan(["AAPL"], BrokenProvider(), default_config)
        assert results == []

    def test_missing_column_skips_symbol(self, default_config):
        df = _make_ohlcv().drop(columns=["volume"])
        provider = MockDataProvider({"X": df})
        results = scan(["X"], provider, default_config)
        assert results == []

    def test_multiple_symbols_all_scored(self, bullish_provider, default_config):
        results = scan(["AAPL", "MSFT", "NVDA"], bullish_provider, default_config)
        symbols = {r.symbol for r in results}
        assert symbols == {"AAPL", "MSFT", "NVDA"}


# ---------------------------------------------------------------------------
# TestScanConfig
# ---------------------------------------------------------------------------


class TestScanConfig:
    def test_default_values(self):
        c = ScanConfig()
        assert c.min_price == 5.0
        assert c.max_price == 500.0
        assert c.min_avg_volume == 500_000.0
        assert c.n_results == 10
        assert c.bars_history >= 210  # at least MIN_BARS

    def test_bars_history_clamped_to_min_bars(self):
        from bot.universe.criteria import MIN_BARS
        c = ScanConfig(bars_history=10)
        assert c.bars_history == MIN_BARS

    def test_criteria_defaulted(self):
        c = ScanConfig()
        assert c.criteria is not None


# ---------------------------------------------------------------------------
# TestGetPool
# ---------------------------------------------------------------------------


class TestGetPool:
    def test_returns_list_of_strings(self, monkeypatch):
        monkeypatch.setattr("bot.utils.config.get",
                            lambda key, default="": "AAPL,MSFT,NVDA")
        result = get_pool()
        assert result == ["AAPL", "MSFT", "NVDA"]

    def test_falls_back_to_default_pool_when_empty(self, monkeypatch):
        monkeypatch.setattr("bot.utils.config.get", lambda key, default="": "")
        result = get_pool()
        assert len(result) > 0
        assert "AAPL" in result or "SPY" in result

    def test_default_pool_has_expected_symbols(self):
        for sym in ("SPY", "QQQ", "AAPL", "MSFT", "NVDA"):
            assert sym in DEFAULT_POOL
