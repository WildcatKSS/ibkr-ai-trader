"""
Backtest result data structures and serialisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BacktestTrade:
    """One simulated trade."""

    bar_index: int
    entry_time: str
    exit_time: str | None
    action: str              # "long" | "short"
    entry_price: float
    exit_price: float | None
    shares: int
    pnl: float | None
    exit_reason: str         # "target" | "stop" | "eod" | "open"
    ml_label: str
    ml_probability: float


@dataclass
class BacktestResult:
    """Output of ``BacktestEngine.run()``."""

    symbol: str
    initial_capital: float
    final_equity: float
    trades: list[BacktestTrade]
    equity_curve: list[float]    # equity after each bar
    metrics: dict[str, float]    # Sharpe, drawdown, win rate, etc.
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise for JSON response."""
        return {
            "symbol": self.symbol,
            "initial_capital": self.initial_capital,
            "final_equity": round(self.final_equity, 2),
            "trade_count": len(self.trades),
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "parameters": self.parameters,
            "trades": [
                {
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "action": t.action,
                    "entry_price": round(t.entry_price, 4),
                    "exit_price": round(t.exit_price, 4) if t.exit_price else None,
                    "shares": t.shares,
                    "pnl": round(t.pnl, 2) if t.pnl is not None else None,
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
            "equity_curve": [round(e, 2) for e in self.equity_curve],
        }
