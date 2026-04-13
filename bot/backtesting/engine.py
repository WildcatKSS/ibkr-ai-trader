"""
Backtesting engine — historical bar replay with simulated order execution.

Reuses the existing signal pipeline (indicators → ML → 15-min filter) to
generate signals on historical data, then simulates order execution with
ATR-based stop/target management.

Usage::

    from bot.backtesting import BacktestEngine
    import pandas as pd

    bars = pd.read_csv("historical.csv", parse_dates=["date"], index_col="date")
    engine = BacktestEngine(initial_capital=100_000)
    result = engine.run(bars, symbol="AAPL")

The engine does NOT call the Claude API to keep backtests fast and
reproducible.  Signal generation uses LightGBM + 15-min confirmation only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from bot.backtesting.metrics import compute_metrics
from bot.backtesting.results import BacktestResult, BacktestTrade
from bot.utils.logger import get_logger

log = get_logger("ml")

# Minimum bars needed before the signal pipeline can produce a valid signal.
# indicators.py needs 50 rows; we need extra for resampling and warmup.
_WARMUP_BARS = 60


@dataclass
class _OpenPosition:
    """Tracks a simulated open position."""

    bar_index: int
    entry_time: str
    action: str
    entry_price: float
    target_price: float
    stop_price: float
    shares: int
    ml_label: str
    ml_probability: float


class BacktestEngine:
    """
    Historical bar replay engine.

    Parameters
    ----------
    initial_capital:
        Starting portfolio value in USD.
    position_size_pct:
        Percentage of equity allocated per trade.
    stop_loss_atr:
        Stop distance as a multiple of ATR (default 1.0).
    take_profit_atr:
        Target distance as a multiple of ATR (default 2.0).
    ml_min_probability:
        Minimum LightGBM probability to act on a signal.
    max_trades_per_day:
        Maximum trades allowed per calendar day.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        position_size_pct: float = 2.0,
        stop_loss_atr: float = 1.0,
        take_profit_atr: float = 2.0,
        ml_min_probability: float = 0.55,
        max_trades_per_day: int = 3,
    ) -> None:
        self._initial_capital = initial_capital
        self._position_size_pct = position_size_pct
        self._stop_loss_atr = stop_loss_atr
        self._take_profit_atr = take_profit_atr
        self._ml_min_probability = ml_min_probability
        self._max_trades_per_day = max_trades_per_day

    def run(self, bars: pd.DataFrame, symbol: str = "UNKNOWN") -> BacktestResult:
        """
        Run a backtest on *bars* and return the result.

        Parameters
        ----------
        bars:
            OHLCV DataFrame with columns open/high/low/close/volume.
            Must be sorted ascending by time.  The index should be a
            DatetimeIndex for best results but is not strictly required.
        symbol:
            Ticker symbol for labelling.

        Returns
        -------
        BacktestResult
        """
        from bot.ml.features import build as build_features
        from bot.ml.model import predict
        from bot.signals.indicators import MIN_ROWS, calculate

        bars = bars.copy()
        bars.columns = [c.lower() for c in bars.columns]

        equity = self._initial_capital
        equity_curve: list[float] = []
        trades: list[BacktestTrade] = []
        position: _OpenPosition | None = None
        daily_trade_count: dict[str, int] = {}

        # Pre-compute indicators on the full dataset once.
        try:
            enriched = calculate(bars)
            features = build_features(enriched)
        except Exception as exc:
            log.warning("Backtest: indicator/feature computation failed",
                        symbol=symbol, error=str(exc))
            return BacktestResult(
                symbol=symbol,
                initial_capital=self._initial_capital,
                final_equity=self._initial_capital,
                trades=[],
                equity_curve=[self._initial_capital],
                metrics=compute_metrics([], [self._initial_capital], self._initial_capital),
                parameters=self._params_dict(),
            )

        n = len(bars)

        for i in range(n):
            bar_time = str(bars.index[i]) if hasattr(bars.index, '__iter__') else str(i)
            trade_date = str(bars.index[i].date()) if hasattr(bars.index[i], 'date') else bar_time[:10]

            close = float(bars["close"].iloc[i])
            high = float(bars["high"].iloc[i])
            low = float(bars["low"].iloc[i])

            # ── Check open position against stop/target ──────────────
            if position is not None:
                exit_price, exit_reason = self._check_exit(
                    position, high, low, close
                )
                if exit_price is not None:
                    pnl = self._calc_pnl(position, exit_price)
                    equity += pnl
                    trades.append(BacktestTrade(
                        bar_index=position.bar_index,
                        entry_time=position.entry_time,
                        exit_time=bar_time,
                        action=position.action,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        shares=position.shares,
                        pnl=pnl,
                        exit_reason=exit_reason,
                        ml_label=position.ml_label,
                        ml_probability=position.ml_probability,
                    ))
                    position = None

            # ── Try to open a new position ───────────────────────────
            if position is None and i >= _WARMUP_BARS:
                # Rate limit per day
                count = daily_trade_count.get(trade_date, 0)
                if count >= self._max_trades_per_day:
                    equity_curve.append(equity)
                    continue

                # Get ML prediction for this bar
                if i < len(features) and not features.iloc[i].isna().any():
                    try:
                        X = features.iloc[[i]]
                        ml_label, ml_prob = predict(X)
                    except Exception:
                        ml_label, ml_prob = "no_trade", 0.0
                else:
                    ml_label, ml_prob = "no_trade", 0.0

                if ml_label in ("long", "short") and ml_prob >= self._ml_min_probability:
                    # Check 15-min confirmation using recent bars
                    confirmed = self._check_15min_confirmation(
                        bars, i, ml_label, enriched
                    )

                    if confirmed:
                        atr = self._get_atr(enriched, i)
                        if atr > 0 and close > 0:
                            position = self._open_position(
                                i, bar_time, ml_label, ml_prob,
                                close, atr, equity,
                            )
                            daily_trade_count[trade_date] = count + 1

            equity_curve.append(equity)

        # Close any remaining position at the last bar price
        if position is not None:
            last_close = float(bars["close"].iloc[-1])
            last_time = str(bars.index[-1]) if hasattr(bars.index, '__iter__') else str(n - 1)
            pnl = self._calc_pnl(position, last_close)
            equity += pnl
            trades.append(BacktestTrade(
                bar_index=position.bar_index,
                entry_time=position.entry_time,
                exit_time=last_time,
                action=position.action,
                entry_price=position.entry_price,
                exit_price=last_close,
                shares=position.shares,
                pnl=pnl,
                exit_reason="eod",
                ml_label=position.ml_label,
                ml_probability=position.ml_probability,
            ))
            equity_curve[-1] = equity

        metrics = compute_metrics(trades, equity_curve, self._initial_capital)

        log.info(
            "Backtest complete",
            symbol=symbol,
            bars=n,
            trades=len(trades),
            total_return_pct=round(metrics.get("total_return_pct", 0), 2),
        )

        return BacktestResult(
            symbol=symbol,
            initial_capital=self._initial_capital,
            final_equity=equity,
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            parameters=self._params_dict(),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _open_position(
        self,
        bar_index: int,
        bar_time: str,
        action: str,
        ml_prob: float,
        close: float,
        atr: float,
        equity: float,
    ) -> _OpenPosition:
        """Create a new simulated position."""
        amount = equity * self._position_size_pct / 100.0
        shares = max(1, int(amount / close))

        if action == "long":
            target = close + self._take_profit_atr * atr
            stop = close - self._stop_loss_atr * atr
        else:
            target = close - self._take_profit_atr * atr
            stop = close + self._stop_loss_atr * atr

        return _OpenPosition(
            bar_index=bar_index,
            entry_time=bar_time,
            action=action,
            entry_price=close,
            target_price=target,
            stop_price=stop,
            shares=shares,
            ml_label=action,
            ml_probability=ml_prob,
        )

    @staticmethod
    def _check_exit(
        pos: _OpenPosition, high: float, low: float, close: float
    ) -> tuple[float | None, str]:
        """Check if the position hits stop or target within this bar."""
        if pos.action == "long":
            if low <= pos.stop_price:
                return pos.stop_price, "stop"
            if high >= pos.target_price:
                return pos.target_price, "target"
        else:  # short
            if high >= pos.stop_price:
                return pos.stop_price, "stop"
            if low <= pos.target_price:
                return pos.target_price, "target"
        return None, ""

    @staticmethod
    def _calc_pnl(pos: _OpenPosition, exit_price: float) -> float:
        if pos.action == "long":
            return (exit_price - pos.entry_price) * pos.shares
        return (pos.entry_price - exit_price) * pos.shares

    @staticmethod
    def _get_atr(enriched: pd.DataFrame, i: int) -> float:
        """Get ATR value at bar index *i*."""
        if "atr" not in enriched.columns or i >= len(enriched):
            return 0.0
        val = enriched["atr"].iloc[i]
        if math.isnan(val):
            return 0.0
        return float(val)

    @staticmethod
    def _check_15min_confirmation(
        bars: pd.DataFrame, i: int, ml_label: str, enriched: pd.DataFrame
    ) -> bool:
        """
        Simplified 15-min confirmation for backtesting.

        Checks EMA cross and MACD histogram direction from the enriched
        DataFrame (already computed on 5-min bars).  A full resample to
        15-min would require careful index alignment in replay mode, so
        we use the 5-min indicators as a proxy.
        """
        if i >= len(enriched):
            return False

        row = enriched.iloc[i]
        ema_cross = row.get("ema_cross", float("nan"))
        macd_hist = row.get("macd_hist", float("nan"))

        if math.isnan(ema_cross) or math.isnan(macd_hist):
            return True  # insufficient data — accept

        if ml_label == "long":
            return bool(ema_cross == 1 and macd_hist > 0)
        elif ml_label == "short":
            return bool(ema_cross == 0 and macd_hist < 0)
        return False

    def _params_dict(self) -> dict:
        return {
            "initial_capital": self._initial_capital,
            "position_size_pct": self._position_size_pct,
            "stop_loss_atr": self._stop_loss_atr,
            "take_profit_atr": self._take_profit_atr,
            "ml_min_probability": self._ml_min_probability,
            "max_trades_per_day": self._max_trades_per_day,
        }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run a backtest")
    parser.add_argument("--symbol", default="UNKNOWN", help="Ticker symbol")
    parser.add_argument("--data", required=True, help="Path to CSV file with OHLCV data")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital")
    parser.add_argument("--size-pct", type=float, default=2.0, help="Position size %%")
    parser.add_argument("--stop-atr", type=float, default=1.0, help="Stop loss ATR multiple")
    parser.add_argument("--target-atr", type=float, default=2.0, help="Take profit ATR multiple")
    parser.add_argument("--min-prob", type=float, default=0.55, help="Min ML probability")
    args = parser.parse_args()

    try:
        df = pd.read_csv(args.data, parse_dates=[0], index_col=0)
    except Exception as exc:
        sys.stderr.write(f"Error reading data: {exc}\n")
        sys.exit(1)

    engine = BacktestEngine(
        initial_capital=args.capital,
        position_size_pct=args.size_pct,
        stop_loss_atr=args.stop_atr,
        take_profit_atr=args.target_atr,
        ml_min_probability=args.min_prob,
    )
    result = engine.run(df, symbol=args.symbol)

    import json
    sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
