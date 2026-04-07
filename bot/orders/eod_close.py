"""
End-of-day position close routine.

Called by ``TradingEngine._eod_close()`` when ``minutes_until_close <=
EOD_CLOSE_MINUTES``.  Closes all open positions with market orders so no
overnight exposure is held — this is a core architecture rule.

In ``dryrun`` mode the routine logs intent but sends no orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.utils.logger import get_logger

if TYPE_CHECKING:
    from bot.orders.executor import IBKRBroker

log = get_logger("trading")


def close_all_positions(
    broker: "IBKRBroker | None",
    *,
    trading_mode: str,
) -> list[dict]:
    """
    Close every open position with a market order.

    Parameters
    ----------
    broker:
        IBKR broker implementation.  Required for paper/live; pass ``None``
        for dryrun.
    trading_mode:
        ``"paper"`` / ``"live"`` / ``"dryrun"``.

    Returns
    -------
    list[dict]
        One result dict per position:
        ``{symbol, action, shares, success, fill_price, reason}``.
    """
    results: list[dict] = []

    if trading_mode == "dryrun":
        log.info("DRYRUN — EOD close skipped, no orders sent")
        return results

    if broker is None:
        log.error("EOD close: no broker configured, cannot close positions")
        return results

    try:
        positions = broker.get_positions()
    except Exception as exc:
        log.error("EOD close: failed to fetch positions", error=str(exc))
        return results

    if not positions:
        log.info("EOD close: no open positions")
        return results

    log.info("EOD close: closing all positions", count=len(positions))

    for pos in positions:
        symbol = pos.get("symbol", "UNKNOWN")
        shares = abs(int(pos.get("shares", 0)))
        pos_action = pos.get("action", "long")

        if shares <= 0:
            continue

        # To close: long → SELL, short → BUY
        close_side = "SELL" if pos_action == "long" else "BUY"

        try:
            order_id = broker.place_order(
                symbol=symbol,
                action=close_side,
                shares=shares,
                order_type="MKT",
            )
        except Exception as exc:
            log.error("EOD close: failed to place order", symbol=symbol, error=str(exc))
            results.append({
                "symbol": symbol,
                "action": close_side,
                "shares": shares,
                "success": False,
                "fill_price": None,
                "reason": str(exc),
            })
            continue

        # Wait for fill (generous timeout at EOD)
        from bot.orders.executor import _wait_for_fill

        fill_price = _wait_for_fill(broker, order_id, timeout=30)

        # Update the open trade record if it exists
        _mark_trade_closed(symbol, fill_price)

        if fill_price is not None:
            log.info(
                "EOD close: position closed",
                symbol=symbol,
                side=close_side,
                shares=shares,
                fill_price=fill_price,
            )
            results.append({
                "symbol": symbol,
                "action": close_side,
                "shares": shares,
                "success": True,
                "fill_price": fill_price,
                "reason": "Closed at EOD.",
            })
        else:
            log.error(
                "EOD close: fill timeout",
                symbol=symbol,
                side=close_side,
                shares=shares,
            )
            results.append({
                "symbol": symbol,
                "action": close_side,
                "shares": shares,
                "success": False,
                "fill_price": None,
                "reason": "Fill timeout at EOD.",
            })

    return results


def _mark_trade_closed(symbol: str, exit_price: float | None) -> None:
    """Update the most recent open/filled trade for *symbol* as closed."""
    try:
        from sqlalchemy import select, update

        from db.models import Trade
        from db.session import get_session

        now = datetime.now(tz=timezone.utc)

        with get_session() as session:
            row = session.execute(
                select(Trade)
                .where(Trade.symbol == symbol, Trade.status.in_(["open", "filled"]))
                .order_by(Trade.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if row is None:
                return

            pnl = None
            if exit_price and row.fill_price:
                if row.action == "long":
                    pnl = (exit_price - row.fill_price) * row.shares
                else:
                    pnl = (row.fill_price - exit_price) * row.shares

            session.execute(
                update(Trade)
                .where(Trade.id == row.id)
                .values(
                    status="closed",
                    exit_price=exit_price,
                    pnl=pnl,
                    closed_at=now,
                )
            )
    except Exception as exc:
        log.warning("Failed to mark trade closed", symbol=symbol, error=str(exc))
