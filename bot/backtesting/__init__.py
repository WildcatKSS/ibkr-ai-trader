"""
Backtesting engine for strategy validation on historical data.

Usage::

    from bot.backtesting import BacktestEngine

    engine = BacktestEngine(
        initial_capital=100_000,
        stop_loss_atr=1.0,
        take_profit_atr=2.0,
    )
    result = engine.run(bars, symbol="AAPL")
    print(result.metrics)
"""

from bot.backtesting.engine import BacktestEngine
from bot.backtesting.metrics import compute_metrics
from bot.backtesting.results import BacktestResult

__all__ = ["BacktestEngine", "BacktestResult", "compute_metrics"]
