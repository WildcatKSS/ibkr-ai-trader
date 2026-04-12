"""
Tests for bot/core/broker.py — IBKRConnection.

All ib_insync calls are mocked.  No real IBKR connection is made.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from bot.core.broker import IBKRConnection, _bar_size_seconds, _bars_to_duration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bar(date, o, h, l, c, v):
    """Create a mock BarData-like object."""
    return SimpleNamespace(date=date, open=o, high=h, low=l, close=c, volume=v)


@pytest.fixture()
def mock_ib():
    """Patch ib_insync.IB so no real connection is created."""
    with patch("bot.core.broker.IB") as MockIB:
        instance = MockIB.return_value
        instance.isConnected.return_value = True
        instance.sleep.return_value = None
        instance.qualifyContracts.return_value = [MagicMock()]
        yield instance


@pytest.fixture()
def conn(mock_ib):
    """Create an IBKRConnection backed by the mocked IB."""
    c = IBKRConnection(host="127.0.0.1", port=7497, client_id=1)
    return c


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


class TestBarSizeSeconds:
    def test_five_mins(self):
        assert _bar_size_seconds("5 mins") == 300

    def test_one_day(self):
        assert _bar_size_seconds("1 day") == 86400

    def test_unknown_defaults_to_300(self):
        assert _bar_size_seconds("unknown") == 300


class TestBarsToDuration:
    def test_daily_short(self):
        result = _bars_to_duration(100, "1 day")
        assert result == "110 D"

    def test_daily_long(self):
        result = _bars_to_duration(500, "1 day")
        assert result == "3 Y"

    def test_intraday_5min(self):
        result = _bars_to_duration(200, "5 mins")
        # 200 * 300 = 60000s / 23400s/day ≈ 2.56 → 4 D (+ 2 margin)
        assert result.endswith(" D")
        days = int(result.split()[0])
        assert days >= 1


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_success(self, conn, mock_ib):
        conn.connect()
        mock_ib.connect.assert_called_once_with(
            "127.0.0.1",
            7497,
            clientId=1,
            timeout=30,
            readonly=False,
        )

    def test_connect_failure_raises(self, conn, mock_ib):
        mock_ib.connect.side_effect = Exception("refused")
        with pytest.raises(ConnectionError, match="Cannot connect"):
            conn.connect()

    def test_disconnect(self, conn, mock_ib):
        conn.disconnect()
        mock_ib.disconnect.assert_called_once()

    def test_disconnect_when_not_connected(self, conn, mock_ib):
        mock_ib.isConnected.return_value = False
        conn.disconnect()
        mock_ib.disconnect.assert_not_called()

    def test_is_connected_property(self, conn, mock_ib):
        mock_ib.isConnected.return_value = True
        assert conn.is_connected is True
        mock_ib.isConnected.return_value = False
        assert conn.is_connected is False


# ---------------------------------------------------------------------------
# broker property
# ---------------------------------------------------------------------------


class TestBrokerProperty:
    def test_returns_self(self, conn):
        assert conn.broker is conn


# ---------------------------------------------------------------------------
# _ensure_connected
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    def test_pumps_events_when_connected(self, conn, mock_ib):
        mock_ib.isConnected.return_value = True
        conn._ensure_connected()
        mock_ib.sleep.assert_called_once_with(0)

    def test_reconnects_when_disconnected(self, conn, mock_ib):
        # First call: disconnected. After reconnect: connected.
        mock_ib.isConnected.side_effect = [False, True]
        mock_ib.connect.return_value = None
        conn._ensure_connected()
        assert mock_ib.connect.call_count == 1

    def test_raises_after_max_attempts(self, conn, mock_ib):
        mock_ib.isConnected.return_value = False
        mock_ib.connect.side_effect = Exception("refused")
        with patch("bot.core.broker.time.sleep"):  # skip actual waits
            with pytest.raises(ConnectionError, match="reconnection failed"):
                conn._ensure_connected()


# ---------------------------------------------------------------------------
# Contract caching
# ---------------------------------------------------------------------------


class TestContractCaching:
    def test_qualifies_once_and_caches(self, conn, mock_ib):
        mock_contract = MagicMock()
        mock_ib.qualifyContracts.return_value = [mock_contract]

        result1 = conn._get_contract("AAPL")
        result2 = conn._get_contract("AAPL")

        assert result1 is result2
        assert mock_ib.qualifyContracts.call_count == 1

    def test_raises_on_unqualifiable_symbol(self, conn, mock_ib):
        mock_ib.qualifyContracts.return_value = []
        with pytest.raises(ValueError, match="Cannot qualify"):
            conn._get_contract("FAKE")


# ---------------------------------------------------------------------------
# DataProvider — fetch_daily_bars
# ---------------------------------------------------------------------------


class TestFetchDailyBars:
    def _mock_util_df(self, bars):
        """Convert mock bars to a real DataFrame like ib_insync.util.df would."""
        data = [
            {"date": b.date, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ]
        return pd.DataFrame(data)

    def test_success(self, conn, mock_ib):
        bars = [
            _make_bar("2024-01-02", 100, 105, 99, 104, 1000),
            _make_bar("2024-01-03", 104, 110, 103, 109, 1200),
        ]
        mock_ib.reqHistoricalData.return_value = bars

        with (
            patch("bot.core.broker.time.sleep"),
            patch("bot.core.broker.util.df", side_effect=self._mock_util_df),
        ):
            df = conn.fetch_daily_bars("AAPL", n_bars=2)

        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

    def test_no_data_returns_none(self, conn, mock_ib):
        mock_ib.reqHistoricalData.return_value = []

        with patch("bot.core.broker.time.sleep"):
            df = conn.fetch_daily_bars("AAPL", n_bars=10)

        assert df is None

    def test_error_returns_none(self, conn, mock_ib):
        mock_ib.reqHistoricalData.side_effect = RuntimeError("timeout")

        with patch("bot.core.broker.time.sleep"):
            df = conn.fetch_daily_bars("AAPL", n_bars=10)

        assert df is None


# ---------------------------------------------------------------------------
# IntradayDataProvider — fetch_intraday_bars
# ---------------------------------------------------------------------------


class TestFetchIntradayBars:
    def _mock_util_df(self, bars):
        data = [
            {"date": b.date, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ]
        return pd.DataFrame(data)

    def test_success(self, conn, mock_ib):
        bars = [
            _make_bar("2024-01-02 09:35:00", 100, 101, 99.5, 100.5, 500),
            _make_bar("2024-01-02 09:40:00", 100.5, 102, 100, 101.5, 600),
        ]
        mock_ib.reqHistoricalData.return_value = bars

        with (
            patch("bot.core.broker.time.sleep"),
            patch("bot.core.broker.util.df", side_effect=self._mock_util_df),
        ):
            df = conn.fetch_intraday_bars("AAPL", n_bars=2)

        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

    def test_no_data_returns_none(self, conn, mock_ib):
        mock_ib.reqHistoricalData.return_value = []

        with patch("bot.core.broker.time.sleep"):
            df = conn.fetch_intraday_bars("AAPL", n_bars=200)

        assert df is None

    def test_custom_bar_size(self, conn, mock_ib):
        bars = [_make_bar("2024-01-02 09:45:00", 100, 101, 99, 100.5, 700)]
        mock_ib.reqHistoricalData.return_value = bars

        with (
            patch("bot.core.broker.time.sleep"),
            patch("bot.core.broker.util.df", side_effect=self._mock_util_df),
        ):
            df = conn.fetch_intraday_bars("AAPL", n_bars=1, bar_size="15 mins")

        assert df is not None
        call_kwargs = mock_ib.reqHistoricalData.call_args
        assert call_kwargs.kwargs.get("barSizeSetting") == "15 mins" or \
            call_kwargs[1].get("barSizeSetting") == "15 mins"


# ---------------------------------------------------------------------------
# IBKRBroker — place_order
# ---------------------------------------------------------------------------


class TestPlaceOrder:
    def _setup(self, mock_ib):
        mock_trade = MagicMock()
        mock_trade.order.orderId = 42
        mock_ib.placeOrder.return_value = mock_trade

    def test_limit_order(self, conn, mock_ib):
        self._setup(mock_ib)
        order_id = conn.place_order("AAPL", "BUY", 10, "LMT", 150.0)
        assert order_id == 42
        mock_ib.placeOrder.assert_called_once()

    def test_market_order(self, conn, mock_ib):
        self._setup(mock_ib)
        order_id = conn.place_order("AAPL", "SELL", 5, "MKT")
        assert order_id == 42
        mock_ib.placeOrder.assert_called_once()

    def test_limit_order_without_price_raises(self, conn, mock_ib):
        self._setup(mock_ib)
        with pytest.raises(ValueError, match="limit_price required"):
            conn.place_order("AAPL", "BUY", 10, "LMT", None)


# ---------------------------------------------------------------------------
# IBKRBroker — get_order_status
# ---------------------------------------------------------------------------


class TestGetOrderStatus:
    def test_filled(self, conn, mock_ib):
        trade = MagicMock()
        trade.order.orderId = 42
        trade.orderStatus.status = "Filled"
        trade.orderStatus.avgFillPrice = 150.25
        mock_ib.trades.return_value = [trade]

        status, price = conn.get_order_status(42)
        assert status == "Filled"
        assert price == 150.25

    def test_submitted(self, conn, mock_ib):
        trade = MagicMock()
        trade.order.orderId = 42
        trade.orderStatus.status = "Submitted"
        trade.orderStatus.avgFillPrice = 0.0
        mock_ib.trades.return_value = [trade]

        status, price = conn.get_order_status(42)
        assert status == "Submitted"
        assert price is None

    def test_not_found(self, conn, mock_ib):
        mock_ib.trades.return_value = []
        status, price = conn.get_order_status(999)
        assert status == "Inactive"
        assert price is None


# ---------------------------------------------------------------------------
# IBKRBroker — cancel_order
# ---------------------------------------------------------------------------


class TestCancelOrder:
    def test_cancel_found(self, conn, mock_ib):
        trade = MagicMock()
        trade.order.orderId = 42
        mock_ib.trades.return_value = [trade]

        conn.cancel_order(42)
        mock_ib.cancelOrder.assert_called_once_with(trade.order)

    def test_cancel_not_found(self, conn, mock_ib):
        mock_ib.trades.return_value = []
        conn.cancel_order(999)  # must not raise
        mock_ib.cancelOrder.assert_not_called()


# ---------------------------------------------------------------------------
# IBKRBroker — get_positions
# ---------------------------------------------------------------------------


class TestGetPositions:
    def test_long_position(self, conn, mock_ib):
        pos = MagicMock()
        pos.contract.symbol = "AAPL"
        pos.position = 100
        pos.avgCost = 150.0
        mock_ib.positions.return_value = [pos]

        result = conn.get_positions()
        assert len(result) == 1
        assert result[0] == {
            "symbol": "AAPL",
            "shares": 100,
            "avg_cost": 150.0,
            "action": "long",
        }

    def test_short_position(self, conn, mock_ib):
        pos = MagicMock()
        pos.contract.symbol = "TSLA"
        pos.position = -50
        pos.avgCost = 200.0
        mock_ib.positions.return_value = [pos]

        result = conn.get_positions()
        assert len(result) == 1
        assert result[0]["action"] == "short"
        assert result[0]["shares"] == 50

    def test_zero_position_skipped(self, conn, mock_ib):
        pos = MagicMock()
        pos.contract.symbol = "MSFT"
        pos.position = 0
        pos.avgCost = 0.0
        mock_ib.positions.return_value = [pos]

        result = conn.get_positions()
        assert len(result) == 0

    def test_empty_positions(self, conn, mock_ib):
        mock_ib.positions.return_value = []
        result = conn.get_positions()
        assert result == []


# ---------------------------------------------------------------------------
# get_portfolio_value
# ---------------------------------------------------------------------------


class TestGetPortfolioValue:
    def test_returns_net_liquidation(self, conn, mock_ib):
        item = MagicMock()
        item.tag = "NetLiquidation"
        item.value = "125000.50"
        mock_ib.accountSummary.return_value = [item]

        val = conn.get_portfolio_value()
        assert val == 125000.50

    def test_missing_tag_returns_default(self, conn, mock_ib):
        item = MagicMock()
        item.tag = "TotalCashValue"
        item.value = "50000"
        mock_ib.accountSummary.return_value = [item]

        val = conn.get_portfolio_value()
        assert val == 100_000.0
