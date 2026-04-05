"""
NYSE trading calendar for ibkr-ai-trader.

All calendar logic is centralised here.  No other module may import
exchange_calendars directly — always go through this module.

Usage:
    from bot.utils.calendar import (
        is_trading_day,
        is_market_open,
        market_open,
        market_close,
        minutes_until_close,
        next_trading_day,
    )
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

import exchange_calendars as xcals
import pandas as pd

# ---------------------------------------------------------------------------
# Calendar singleton
# ---------------------------------------------------------------------------

# exchange_calendars uses "XNYS" for the New York Stock Exchange.
_CALENDAR = xcals.get_calendar("XNYS")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_trading_day(dt: Optional[date] = None) -> bool:
    """
    Return True if *dt* is a NYSE trading day (not a weekend or holiday).

    Args:
        dt: The date to check.  Defaults to today (UTC).
    """
    target = _to_date(dt)
    session = pd.Timestamp(target)  # exchange_calendars requires timezone-naive
    return _CALENDAR.is_session(session)


def market_open(dt: Optional[date] = None) -> datetime:
    """
    Return the scheduled market open time for *dt* as a timezone-aware datetime.

    Args:
        dt: A NYSE trading day.  Defaults to today (UTC).

    Raises:
        ValueError: If *dt* is not a trading day.
    """
    target = _to_date(dt)
    session = pd.Timestamp(target)  # timezone-naive for session lookup
    _assert_session(session, target)
    open_ts: pd.Timestamp = _CALENDAR.session_open(session)
    return open_ts.to_pydatetime()


def market_close(dt: Optional[date] = None) -> datetime:
    """
    Return the scheduled market close time for *dt* as a timezone-aware datetime.

    Handles early-close days (e.g. day before Thanksgiving) automatically —
    exchange_calendars includes the correct early-close schedule.

    Args:
        dt: A NYSE trading day.  Defaults to today (UTC).

    Raises:
        ValueError: If *dt* is not a trading day.
    """
    target = _to_date(dt)
    session = pd.Timestamp(target)  # timezone-naive for session lookup
    _assert_session(session, target)
    close_ts: pd.Timestamp = _CALENDAR.session_close(session)
    return close_ts.to_pydatetime()


def is_market_open(dt: Optional[datetime] = None) -> bool:
    """
    Return True if the NYSE is currently open for trading.

    Args:
        dt: The moment to check.  Defaults to now (UTC).
    """
    now = dt if dt is not None else datetime.now(tz=timezone.utc)
    today = now.date()
    if not is_trading_day(today):
        return False
    open_time = market_open(today)
    close_time = market_close(today)
    # Normalise now to UTC for comparison with the UTC times from the calendar.
    now_utc = now.astimezone(timezone.utc)
    return open_time <= now_utc < close_time


def minutes_until_close(dt: Optional[datetime] = None) -> int:
    """
    Return the number of whole minutes until today's market close.

    Returns 0 if the market is already closed or today is not a trading day.

    Args:
        dt: The reference moment.  Defaults to now (UTC).
    """
    now = dt if dt is not None else datetime.now(tz=timezone.utc)
    today = now.date()
    if not is_trading_day(today):
        return 0
    close = market_close(today)
    now_utc = now.astimezone(timezone.utc)
    if now_utc >= close:
        return 0
    delta = close - now_utc
    return int(delta.total_seconds() // 60)


def next_trading_day(dt: Optional[date] = None) -> date:
    """
    Return the next NYSE trading day after *dt*.

    Args:
        dt: The reference date.  Defaults to today (UTC).
    """
    target = _to_date(dt)
    session = pd.Timestamp(target)  # timezone-naive
    if _CALENDAR.is_session(session):
        # next_session() requires the input to be a valid session.
        next_s: pd.Timestamp = _CALENDAR.next_session(session)
    else:
        # date_to_session(..., "next") returns the first session on or after
        # the given date — exactly what we want for non-trading-day inputs.
        next_s = _CALENDAR.date_to_session(session, direction="next")
    return next_s.date()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_date(dt: Optional[date | datetime]) -> date:
    """Normalise a date/datetime/None to a date."""
    if dt is None:
        return datetime.now(tz=timezone.utc).date()
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).date()
    return dt


def _assert_session(session: pd.Timestamp, target: date) -> None:
    if not _CALENDAR.is_session(session):
        raise ValueError(
            f"{target} is not a NYSE trading day. "
            "Check with is_trading_day() before calling market_open/close()."
        )
