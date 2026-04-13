"""
Risk manager — circuit breaker, position sizing, and order approval.

Called once per signal, after ``bot.signals.generator.generate`` returns an
actionable signal and before ``bot.orders.executor.execute`` places the order.

Responsibilities
----------------
1. **Circuit breaker** — halt all trading for the day when:
   - Daily P&L loss exceeds ``CIRCUIT_BREAKER_DAILY_LOSS_PCT`` of portfolio.
   - Consecutive losing trades reach ``CIRCUIT_BREAKER_CONSECUTIVE_LOSSES``.
2. **Position sizing** — compute share count from:
   - ``fixed_pct``   — ``POSITION_SIZE_PCT`` % of portfolio value.
   - ``fixed_amount`` — ``POSITION_SIZE_AMOUNT`` USD per trade.
   - ``kelly``        — half-Kelly fraction based on ML probability.
3. **Hard cap** — shares are capped so the position never exceeds
   ``POSITION_MAX_PCT`` % of portfolio value regardless of sizing method.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from bot.utils.logger import get_logger

if TYPE_CHECKING:
    from bot.signals.generator import Signal

log = get_logger("risk")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class RiskDecision:
    """Output of ``check()``."""

    approved: bool
    shares: int         # 0 when not approved
    dollar_value: float # 0.0 when not approved
    stop_price: float
    target_price: float
    reason: str         # brief explanation (logged + stored)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check(
    signal: "Signal",
    portfolio_value: float,
    *,
    trading_mode: str = "paper",
) -> RiskDecision:
    """
    Evaluate a signal against risk rules and compute the position size.

    Parameters
    ----------
    signal:
        Accepted signal from the generator (``action`` must be ``"long"``
        or ``"short"``).
    portfolio_value:
        Current total portfolio value in USD (paper or live NAV).
    trading_mode:
        ``"paper"`` / ``"live"`` / ``"dryrun"``.  Dryrun skips DB queries
        but still applies sizing rules (for logging).

    Returns
    -------
    RiskDecision
        ``approved=False`` blocks the order from being sent.
    """
    from bot.utils.config import get

    if signal.action == "no_trade":
        return RiskDecision(
            approved=False,
            shares=0,
            dollar_value=0.0,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason="Signal action is no_trade.",
        )

    if portfolio_value <= 0:
        return RiskDecision(
            approved=False,
            shares=0,
            dollar_value=0.0,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason="Portfolio value is zero or negative.",
        )

    # ── Circuit breaker ───────────────────────────────────────────────────
    if trading_mode != "dryrun":
        cb_reason = _circuit_breaker_check(portfolio_value, get)
        if cb_reason:
            log.warning(
                "Circuit breaker tripped — no order",
                symbol=signal.symbol,
                reason=cb_reason,
            )
            try:
                from bot.alerts.notifier import notify
                notify("circuit_breaker", {
                    "reason": cb_reason,
                    "symbol": signal.symbol,
                    "trading_mode": trading_mode,
                })
            except BaseException:  # noqa: BLE001
                pass  # alert failure must never block the risk decision
            return RiskDecision(
                approved=False,
                shares=0,
                dollar_value=0.0,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                reason=cb_reason,
            )

    # ── Position sizing ───────────────────────────────────────────────────
    entry = signal.entry_price
    if entry <= 0:
        return RiskDecision(
            approved=False,
            shares=0,
            dollar_value=0.0,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason="Entry price is zero or negative.",
        )

    method = get("POSITION_SIZING_METHOD", default="fixed_pct")
    max_pct = get("POSITION_MAX_PCT", cast=float, default=5.0)

    if method == "fixed_amount":
        amount = get("POSITION_SIZE_AMOUNT", cast=float, default=5000.0)
    elif method == "kelly":
        amount = _kelly_amount(signal.ml_probability, portfolio_value)
    else:  # fixed_pct (default)
        pct = get("POSITION_SIZE_PCT", cast=float, default=2.0)
        amount = portfolio_value * pct / 100.0

    # Cap at POSITION_MAX_PCT
    max_amount = portfolio_value * max_pct / 100.0
    amount = min(amount, max_amount)

    shares = int(amount / entry)  # floor to whole shares

    if shares <= 0:
        return RiskDecision(
            approved=False,
            shares=0,
            dollar_value=0.0,
            stop_price=signal.stop_price,
            target_price=signal.target_price,
            reason=f"Position size rounds to 0 shares "
                   f"(amount={amount:.2f}, entry={entry:.2f}).",
        )

    dollar_value = shares * entry

    log.info(
        "Risk approved",
        symbol=signal.symbol,
        action=signal.action,
        shares=shares,
        dollar_value=round(dollar_value, 2),
        entry=entry,
        target=signal.target_price,
        stop=signal.stop_price,
        method=method,
    )

    return RiskDecision(
        approved=True,
        shares=shares,
        dollar_value=round(dollar_value, 2),
        stop_price=signal.stop_price,
        target_price=signal.target_price,
        reason="Approved.",
    )


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def _circuit_breaker_check(portfolio_value: float, get_setting) -> str | None:
    """
    Return a non-empty string (reason) if trading should be halted, else None.

    Queries today's closed trades from the DB to compute daily P&L and
    consecutive losses.
    """
    try:
        daily_loss_pct = get_setting("CIRCUIT_BREAKER_DAILY_LOSS_PCT", cast=float, default=3.0)
        max_consecutive = get_setting("CIRCUIT_BREAKER_CONSECUTIVE_LOSSES", cast=int, default=5)
    except Exception as exc:
        log.error("Cannot read circuit breaker settings — blocking trade (fail closed)", error=str(exc))
        return "Circuit breaker settings unreadable — halting trades as a safety measure."

    today = date.today()

    try:
        daily_pnl, consecutive_losses = _query_today_stats(today)
    except Exception as exc:
        log.error("Cannot query trade stats for circuit breaker — blocking trade (fail closed)", error=str(exc))
        return "Cannot verify daily P&L — halting trades as a safety measure."

    # Daily loss check
    if portfolio_value > 0:
        loss_pct = (-daily_pnl / portfolio_value * 100.0) if daily_pnl < 0 else 0.0
        if loss_pct >= daily_loss_pct:
            return (
                f"Daily loss {loss_pct:.2f}% exceeds limit {daily_loss_pct:.2f}%."
            )

    # Consecutive losses check
    if consecutive_losses >= max_consecutive:
        return (
            f"Consecutive losing trades ({consecutive_losses}) "
            f"reached limit ({max_consecutive})."
        )

    return None


def _query_today_stats(today: date) -> tuple[float, int]:
    """
    Return ``(daily_pnl, consecutive_losses)`` from closed trades.

    *daily_pnl* sums only trades closed today.
    *consecutive_losses* counts the unbroken losing streak at the tail of
    today's trade history — it resets to 0 when a winning trade or any
    trade from a previous day is encountered.  Yesterday's losses do NOT
    carry over into today's streak.
    """
    from sqlalchemy import select

    from db.models import Trade
    from db.session import get_session

    with get_session() as session:
        rows = session.execute(
            select(Trade.pnl, Trade.status, Trade.closed_at)
            .where(Trade.status == "closed")
            .order_by(Trade.closed_at.desc())
        ).all()

    today_pnl = 0.0
    consecutive = 0
    streak_active = True  # False once a win or a non-today trade is seen

    for pnl, _status, closed_at in rows:
        if pnl is None:
            continue

        is_today = closed_at is not None and closed_at.date() == today

        if is_today:
            today_pnl += pnl

        if streak_active:
            if not is_today:
                # Reached yesterday's trades — streak ends, but keep summing
                # today_pnl if there are any today rows still ahead (there
                # aren't, since we're ordered DESC).
                streak_active = False
            elif pnl < 0:
                consecutive += 1
            else:
                streak_active = False  # today win breaks the streak

    return today_pnl, consecutive


# ---------------------------------------------------------------------------
# Kelly sizing
# ---------------------------------------------------------------------------


def _kelly_amount(ml_probability: float, portfolio_value: float) -> float:
    """
    Compute a half-Kelly position size in USD.

    Kelly fraction = p - (1-p) / (reward/risk).
    We assume reward/risk ≈ 2 (target = 2× stop distance) and apply half-Kelly
    for conservatism.
    """
    p = max(0.0, min(1.0, ml_probability))
    q = 1.0 - p
    reward_risk = 2.0
    kelly = p - q / reward_risk
    half_kelly = max(0.0, kelly / 2.0)
    return portfolio_value * half_kelly
