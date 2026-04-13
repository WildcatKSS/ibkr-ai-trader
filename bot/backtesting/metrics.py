"""
Performance metrics for backtesting results.

All functions are pure — no I/O, no side effects.
"""

from __future__ import annotations

import math

from bot.backtesting.results import BacktestTrade


def compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[float],
    initial_capital: float,
) -> dict[str, float]:
    """
    Compute standard performance metrics from a completed backtest.

    Returns a dict with keys:
        total_return_pct, total_pnl, trade_count, win_count, loss_count,
        win_rate, avg_win, avg_loss, profit_factor, max_drawdown_pct,
        sharpe_ratio, avg_trade_pnl, largest_win, largest_loss
    """
    closed = [t for t in trades if t.pnl is not None]

    if not closed:
        return _empty_metrics()

    pnls = [t.pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    final_equity = equity_curve[-1] if equity_curve else initial_capital

    return {
        "total_return_pct": (final_equity - initial_capital) / initial_capital * 100.0,
        "total_pnl": total_pnl,
        "trade_count": len(closed),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(closed) * 100.0 if closed else 0.0,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown_pct": _max_drawdown(equity_curve),
        "sharpe_ratio": _sharpe_ratio(equity_curve),
        "avg_trade_pnl": total_pnl / len(closed) if closed else 0.0,
        "largest_win": max(wins) if wins else 0.0,
        "largest_loss": min(losses) if losses else 0.0,
    }


def _max_drawdown(equity_curve: list[float]) -> float:
    """Peak-to-trough drawdown as a positive percentage."""
    if len(equity_curve) < 2:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0

    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _sharpe_ratio(equity_curve: list[float], trading_days_per_year: int = 252) -> float:
    """
    Annualised Sharpe ratio from the equity curve.

    Uses daily returns derived from the equity curve.  Risk-free rate is
    assumed to be 0 for simplicity (standard for intraday strategies).
    """
    if len(equity_curve) < 3:
        return 0.0

    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            returns.append((equity_curve[i] - prev) / prev)

    if not returns:
        return 0.0

    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return 0.0

    return (mean_ret / std_ret) * math.sqrt(trading_days_per_year)


def _empty_metrics() -> dict[str, float]:
    return {
        "total_return_pct": 0.0,
        "total_pnl": 0.0,
        "trade_count": 0,
        "win_count": 0,
        "loss_count": 0,
        "win_rate": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "avg_trade_pnl": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
    }
