"""
Order executor — places orders via the IBKR broker and monitors fills.

This is step 6 of the signal pipeline:

    ... → risk → [THIS MODULE]

Design
------
- ``TRADING_MODE`` is checked before every order.  In ``dryrun`` mode the
  intended order is logged but nothing is sent to IBKR.
- An ``IBKRBroker`` protocol is injected so the executor can be unit-tested
  without a live IBKR connection.
- All trades (including dryrun) are persisted to the ``trades`` DB table.
- The executor polls for a fill up to ``ORDER_FILL_TIMEOUT_SECONDS``.  On
  timeout it cancels the limit order and tries a market order once.  If the
  market order also fails, the trade is marked ``error``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from bot.utils.logger import get_logger

if TYPE_CHECKING:
    from bot.risk.manager import RiskDecision
    from bot.signals.generator import Signal

log = get_logger("trading")

# Seconds between fill-status polls
_POLL_INTERVAL = 2


# ---------------------------------------------------------------------------
# Broker protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class IBKRBroker(Protocol):
    """
    Minimal interface for IBKR order operations.

    The production implementation wraps ``ib_insync.IB`` and lives in
    ``bot/core/``.  Tests inject a mock.
    """

    def place_order(
        self,
        symbol: str,
        action: str,           # "BUY" | "SELL"
        shares: int,
        order_type: str,       # "LMT" | "MKT"
        limit_price: float | None = None,
    ) -> int:
        """Place an order and return the broker order ID."""
        ...

    def get_order_status(self, order_id: int) -> tuple[str, float | None]:
        """
        Return ``(status, fill_price)``.

        *status* values: ``"Filled"``, ``"Submitted"``, ``"PreSubmitted"``,
        ``"Cancelled"``, ``"Inactive"``.
        """
        ...

    def cancel_order(self, order_id: int) -> None:
        """Cancel an open order."""
        ...

    def get_positions(self) -> list[dict]:
        """
        Return a list of open positions.

        Each dict: ``{symbol, shares, avg_cost, action}``
        where *action* is ``"long"`` or ``"short"``.
        """
        ...


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class OrderResult:
    """Output of ``execute()``."""

    success: bool
    trade_id: int | None    # DB primary key
    order_id: int | None    # IBKR order ID (None for dryrun)
    fill_price: float | None
    shares: int
    symbol: str
    action: str
    reason: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def execute(
    signal: "Signal",
    decision: "RiskDecision",
    *,
    trading_mode: str,
    broker: IBKRBroker | None = None,
) -> OrderResult:
    """
    Place an order and monitor the fill.

    Parameters
    ----------
    signal:
        Accepted signal (``action`` must be ``"long"`` or ``"short"``).
    decision:
        Approved risk decision (``approved`` must be ``True``).
    trading_mode:
        ``"paper"`` / ``"live"`` / ``"dryrun"``.
    broker:
        IBKR broker implementation.  Required for paper/live; ignored for
        dryrun.

    Returns
    -------
    OrderResult
    """
    if not decision.approved:
        return OrderResult(
            success=False,
            trade_id=None,
            order_id=None,
            fill_price=None,
            shares=0,
            symbol=signal.symbol,
            action=signal.action,
            reason="Risk decision not approved.",
        )

    # Persist the trade record immediately (status = "pending")
    trade_id = _create_trade_record(signal, decision, trading_mode)

    # ── Dryrun ────────────────────────────────────────────────────────────
    if trading_mode == "dryrun":
        log.info(
            "DRYRUN — order logged, not sent",
            symbol=signal.symbol,
            action=signal.action,
            shares=decision.shares,
            entry=signal.entry_price,
            target=signal.target_price,
            stop=signal.stop_price,
            confidence=round(signal.confidence, 3),
            explanation=signal.explanation[:120],
        )
        _update_trade(trade_id, status="dryrun", fill_price=signal.entry_price)
        return OrderResult(
            success=True,
            trade_id=trade_id,
            order_id=None,
            fill_price=signal.entry_price,
            shares=decision.shares,
            symbol=signal.symbol,
            action=signal.action,
            reason="Dryrun — order logged.",
        )

    # ── Paper / live ──────────────────────────────────────────────────────
    if broker is None:
        log.error(
            "No broker configured for paper/live mode",
            symbol=signal.symbol,
            trading_mode=trading_mode,
        )
        _update_trade(trade_id, status="error")
        return OrderResult(
            success=False,
            trade_id=trade_id,
            order_id=None,
            fill_price=None,
            shares=decision.shares,
            symbol=signal.symbol,
            action=signal.action,
            reason="No broker configured.",
        )

    ibkr_action = "BUY" if signal.action == "long" else "SELL"

    try:
        order_id = broker.place_order(
            symbol=signal.symbol,
            action=ibkr_action,
            shares=decision.shares,
            order_type="LMT",
            limit_price=signal.entry_price,
        )
    except Exception as exc:
        log.error("Failed to place order", symbol=signal.symbol, error=str(exc))
        _update_trade(trade_id, status="error")
        return OrderResult(
            success=False,
            trade_id=trade_id,
            order_id=None,
            fill_price=None,
            shares=decision.shares,
            symbol=signal.symbol,
            action=signal.action,
            reason=f"Place order failed: {exc}",
        )

    _update_trade(trade_id, status="open", ibkr_order_id=order_id)

    # ── Poll for fill ─────────────────────────────────────────────────────
    from bot.utils.config import get

    try:
        timeout = get("ORDER_FILL_TIMEOUT_SECONDS", cast=int, default=60)
    except Exception:
        timeout = 60

    fill_price = _wait_for_fill(broker, order_id, timeout)

    if fill_price is not None:
        _update_trade(trade_id, status="filled", fill_price=fill_price,
                      filled_at=datetime.now(tz=timezone.utc))
        log.info(
            "Order filled",
            symbol=signal.symbol,
            order_id=order_id,
            fill_price=fill_price,
            shares=decision.shares,
        )
        return OrderResult(
            success=True,
            trade_id=trade_id,
            order_id=order_id,
            fill_price=fill_price,
            shares=decision.shares,
            symbol=signal.symbol,
            action=signal.action,
            reason="Filled.",
        )

    # ── Timeout: cancel limit, try market ─────────────────────────────────
    log.warning(
        "Fill timeout — cancelling limit order",
        symbol=signal.symbol,
        order_id=order_id,
        timeout_sec=timeout,
    )
    try:
        broker.cancel_order(order_id)
    except Exception as exc:
        log.warning("Cancel order failed", order_id=order_id, error=str(exc))

    try:
        mkt_order_id = broker.place_order(
            symbol=signal.symbol,
            action=ibkr_action,
            shares=decision.shares,
            order_type="MKT",
        )
        fill_price = _wait_for_fill(broker, mkt_order_id, timeout=15)
    except Exception as exc:
        log.error("Market order fallback failed", symbol=signal.symbol, error=str(exc))
        fill_price = None
        mkt_order_id = None

    if fill_price is not None:
        _update_trade(trade_id, status="filled", fill_price=fill_price,
                      ibkr_order_id=mkt_order_id,
                      filled_at=datetime.now(tz=timezone.utc))
        log.info(
            "Market order filled after limit timeout",
            symbol=signal.symbol,
            fill_price=fill_price,
        )
        return OrderResult(
            success=True,
            trade_id=trade_id,
            order_id=mkt_order_id,
            fill_price=fill_price,
            shares=decision.shares,
            symbol=signal.symbol,
            action=signal.action,
            reason="Filled via market order after limit timeout.",
        )

    _update_trade(trade_id, status="error")
    log.error(
        "Order not filled — market order also failed",
        symbol=signal.symbol,
    )
    return OrderResult(
        success=False,
        trade_id=trade_id,
        order_id=None,
        fill_price=None,
        shares=decision.shares,
        symbol=signal.symbol,
        action=signal.action,
        reason="Order unfilled after limit timeout and market fallback.",
    )


# ---------------------------------------------------------------------------
# Fill monitoring
# ---------------------------------------------------------------------------


def _wait_for_fill(
    broker: IBKRBroker, order_id: int, timeout: int
) -> float | None:
    """
    Poll until the order is filled or *timeout* seconds elapses.

    Returns the fill price or ``None`` on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, fill_price = broker.get_order_status(order_id)
        except Exception as exc:
            log.warning("get_order_status error", order_id=order_id, error=str(exc))
            time.sleep(_POLL_INTERVAL)
            continue

        if status == "Filled" and fill_price is not None:
            return fill_price

        if status in {"Cancelled", "Inactive"}:
            return None

        time.sleep(_POLL_INTERVAL)

    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _create_trade_record(
    signal: "Signal",
    decision: "RiskDecision",
    trading_mode: str,
) -> int | None:
    """Insert a Trade row and return its ID."""
    try:
        from db.models import Trade
        from db.session import get_session

        now = datetime.now(tz=timezone.utc)
        trade = Trade(
            symbol=signal.symbol,
            action=signal.action,
            trading_mode=trading_mode,
            status="pending",
            shares=decision.shares,
            entry_price=signal.entry_price,
            target_price=signal.target_price,
            stop_price=signal.stop_price,
            ml_label=signal.ml_label,
            ml_probability=signal.ml_probability,
            confirmed_15min=signal.confirmed_15min,
            explanation=signal.explanation[:500] if signal.explanation else None,
            created_at=now,
        )
        with get_session() as session:
            session.add(trade)
            session.flush()
            trade_id = trade.id
        return trade_id
    except Exception as exc:
        log.warning("Failed to create trade record", error=str(exc))
        return None


def _update_trade(trade_id: int | None, **kwargs) -> None:
    """Update fields on an existing Trade row by primary key."""
    if trade_id is None:
        return
    try:
        from sqlalchemy import update

        from db.models import Trade
        from db.session import get_session

        with get_session() as session:
            session.execute(
                update(Trade).where(Trade.id == trade_id).values(**kwargs)
            )
    except Exception as exc:
        log.warning("Failed to update trade record", trade_id=trade_id, error=str(exc))
