"""
IBKR connection — unified data provider and order broker.

Wraps ``ib_insync.IB`` and satisfies three protocol surfaces:

- ``bot.universe.scanner.DataProvider``        (daily bars)
- ``bot.signals.generator.IntradayDataProvider`` (intraday bars)
- ``bot.orders.executor.IBKRBroker``            (order execution)

The engine accesses the broker through
``getattr(data_provider, "broker", None)``; the ``broker`` property
on this class returns ``self`` to fulfil that contract.

Usage (from ``bot/core/__main__.py``)::

    conn = IBKRConnection(port=7497)
    conn.connect()
    engine = TradingEngine(trading_mode="paper", data_provider=conn)
    engine.run()
    conn.disconnect()
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
from ib_insync import IB, LimitOrder, MarketOrder, Stock, util

from bot.utils.logger import get_logger

log = get_logger("ibkr")

# Seconds to pause between historical-data requests to stay within the IBKR
# pacing limit (~60 requests per 10 minutes).
_HIST_PACE_SECONDS = 0.5

# Maximum reconnection attempts before giving up.
_MAX_RECONNECT_ATTEMPTS = 3


def _bar_size_seconds(bar_size: str) -> int:
    """Convert an IBKR bar-size string to an approximate duration in seconds."""
    mapping = {
        "1 min": 60,
        "2 mins": 120,
        "3 mins": 180,
        "5 mins": 300,
        "10 mins": 600,
        "15 mins": 900,
        "20 mins": 1200,
        "30 mins": 1800,
        "1 hour": 3600,
        "1 day": 86400,
    }
    return mapping.get(bar_size, 300)


def _bars_to_duration(n_bars: int, bar_size: str) -> str:
    """Compute an IBKR durationStr that covers *n_bars* of *bar_size*.

    IBKR requires specific duration formats: ``"N S"`` (seconds),
    ``"N D"`` (days), ``"N W"`` (weeks), ``"N M"`` (months), ``"N Y"`` (years).
    """
    if bar_size == "1 day":
        # Daily bars — request enough trading days (with margin).
        if n_bars <= 365:
            return f"{max(n_bars + 10, 30)} D"
        years = n_bars // 250 + 1
        return f"{years} Y"

    total_seconds = n_bars * _bar_size_seconds(bar_size)
    # Convert to trading days (6.5 hours = 23 400 seconds per day).
    trading_days = total_seconds / 23_400
    days = int(trading_days) + 2  # margin for weekends/holidays
    return f"{max(days, 1)} D"


class IBKRConnection:
    """
    Unified IBKR connection for data retrieval and order execution.

    Implements ``DataProvider``, ``IntradayDataProvider``, and ``IBKRBroker``
    protocols.  A single ``ib_insync.IB`` instance is shared across all
    operations.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        timeout: int = 30,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout = timeout

        self._ib = IB()
        self._contracts: dict[str, Any] = {}  # symbol → qualified Contract

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to TWS / IB Gateway.  Raises on failure."""
        log.info(
            "Connecting to IBKR",
            host=self._host,
            port=self._port,
            client_id=self._client_id,
        )
        try:
            self._ib.connect(
                self._host,
                self._port,
                clientId=self._client_id,
                timeout=self._timeout,
                readonly=False,
            )
        except Exception as exc:
            log.error("IBKR connection failed", error=str(exc), port=self._port)
            raise ConnectionError(
                f"Cannot connect to IBKR on {self._host}:{self._port}"
            ) from exc
        log.info("IBKR connected", port=self._port)

    def disconnect(self) -> None:
        """Disconnect gracefully."""
        if self._ib.isConnected():
            self._ib.disconnect()
            log.info("IBKR disconnected")

    @property
    def is_connected(self) -> bool:
        """Return ``True`` if the connection is alive."""
        return self._ib.isConnected()

    @property
    def broker(self) -> "IBKRConnection":
        """Return self so ``getattr(data_provider, 'broker', None)`` works."""
        return self

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        """Check connection and attempt one reconnect if needed.

        Also pumps the ib_insync event loop so pending callbacks are
        processed (required by ib_insync's architecture).
        """
        if self._ib.isConnected():
            self._ib.sleep(0)  # pump events
            return

        log.warning("IBKR disconnected — attempting reconnect")
        for attempt in range(1, _MAX_RECONNECT_ATTEMPTS + 1):
            try:
                self._ib.connect(
                    self._host,
                    self._port,
                    clientId=self._client_id,
                    timeout=self._timeout,
                    readonly=False,
                )
                log.info("IBKR reconnected", attempt=attempt)
                return
            except Exception as exc:
                log.warning(
                    "Reconnect attempt failed",
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < _MAX_RECONNECT_ATTEMPTS:
                    time.sleep(2 ** attempt)  # 2, 4 seconds

        raise ConnectionError("IBKR reconnection failed after max attempts")

    def _get_contract(self, symbol: str) -> Any:
        """Return a qualified ``Stock`` contract, using a cache."""
        if symbol in self._contracts:
            return self._contracts[symbol]

        contract = Stock(symbol, "SMART", "USD")
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise ValueError(f"Cannot qualify contract for {symbol}")
        self._contracts[symbol] = qualified[0]
        return qualified[0]

    def _hist_bars_to_df(self, bars: list) -> pd.DataFrame | None:
        """Convert ib_insync ``BarData`` list to a DataFrame."""
        if not bars:
            return None

        df = util.df(bars)
        # Normalise column names — ib_insync uses 'date' for the datetime.
        if "date" in df.columns:
            df = df.rename(columns={"date": "datetime"})
        if "datetime" in df.columns:
            df = df.set_index("datetime")
        return df[["open", "high", "low", "close", "volume"]]

    # ------------------------------------------------------------------
    # DataProvider protocol
    # ------------------------------------------------------------------

    def fetch_daily_bars(self, symbol: str, n_bars: int) -> pd.DataFrame | None:
        """Fetch up to *n_bars* of daily OHLCV data for *symbol*."""
        try:
            self._ensure_connected()
            contract = self._get_contract(symbol)
            duration = _bars_to_duration(n_bars, "1 day")
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            time.sleep(_HIST_PACE_SECONDS)
            df = self._hist_bars_to_df(bars)
            if df is not None:
                df = df.tail(n_bars)
            return df
        except ConnectionError:
            raise
        except Exception as exc:
            log.warning("fetch_daily_bars failed", symbol=symbol, error=str(exc))
            return None

    # ------------------------------------------------------------------
    # IntradayDataProvider protocol
    # ------------------------------------------------------------------

    def fetch_intraday_bars(
        self,
        symbol: str,
        n_bars: int,
        bar_size: str = "5 mins",
    ) -> pd.DataFrame | None:
        """Fetch up to *n_bars* of intraday OHLCV data for *symbol*."""
        try:
            self._ensure_connected()
            contract = self._get_contract(symbol)
            duration = _bars_to_duration(n_bars, bar_size)
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            time.sleep(_HIST_PACE_SECONDS)
            df = self._hist_bars_to_df(bars)
            if df is not None:
                df = df.tail(n_bars)
            return df
        except ConnectionError:
            raise
        except Exception as exc:
            log.warning(
                "fetch_intraday_bars failed",
                symbol=symbol,
                bar_size=bar_size,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # IBKRBroker protocol
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        action: str,
        shares: int,
        order_type: str,
        limit_price: float | None = None,
    ) -> int:
        """Place an order and return the broker order ID."""
        self._ensure_connected()
        contract = self._get_contract(symbol)

        if order_type == "LMT":
            if limit_price is None:
                raise ValueError("limit_price required for LMT orders")
            order = LimitOrder(action, shares, limit_price)
        else:
            order = MarketOrder(action, shares)

        trade = self._ib.placeOrder(contract, order)
        log.info(
            "Order placed",
            symbol=symbol,
            action=action,
            shares=shares,
            order_type=order_type,
            limit_price=limit_price,
            order_id=trade.order.orderId,
        )
        return trade.order.orderId

    def get_order_status(self, order_id: int) -> tuple[str, float | None]:
        """Return ``(status, fill_price)`` for the given order ID."""
        self._ensure_connected()

        for trade in self._ib.trades():
            if trade.order.orderId == order_id:
                status = trade.orderStatus.status
                fill_price = (
                    trade.orderStatus.avgFillPrice
                    if status == "Filled"
                    else None
                )
                return (status, fill_price)

        return ("Inactive", None)

    def cancel_order(self, order_id: int) -> None:
        """Cancel an open order."""
        self._ensure_connected()

        for trade in self._ib.trades():
            if trade.order.orderId == order_id:
                self._ib.cancelOrder(trade.order)
                log.info("Order cancelled", order_id=order_id)
                return

        log.warning("Order not found for cancellation", order_id=order_id)

    def get_positions(self) -> list[dict]:
        """Return open positions as a list of dicts."""
        self._ensure_connected()

        positions = []
        for pos in self._ib.positions():
            shares = int(pos.position)
            if shares == 0:
                continue
            positions.append({
                "symbol": pos.contract.symbol,
                "shares": abs(shares),
                "avg_cost": float(pos.avgCost),
                "action": "long" if shares > 0 else "short",
            })
        return positions

    def get_portfolio_value(self) -> float:
        """Return net liquidation value from the account summary."""
        self._ensure_connected()

        for item in self._ib.accountSummary():
            if item.tag == "NetLiquidation":
                return float(item.value)

        log.warning("NetLiquidation not found in account summary")
        return 100_000.0
