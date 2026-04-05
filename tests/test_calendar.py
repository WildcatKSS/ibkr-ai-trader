"""
Tests for bot/utils/calendar.py.

Uses fixed known dates so tests are deterministic regardless of when
they run.  No external API calls are made — exchange_calendars works
from a bundled schedule.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from bot.utils.calendar import (
    is_market_open,
    is_trading_day,
    market_close,
    market_open,
    minutes_until_close,
    next_trading_day,
)

# ---------------------------------------------------------------------------
# Known dates used across tests
# ---------------------------------------------------------------------------

# Regular trading day: Monday 2024-01-08
TRADING_DAY = date(2024, 1, 8)

# Weekend: Saturday 2024-01-06
WEEKEND = date(2024, 1, 6)

# NYSE holiday: New Year's Day observed 2024-01-01
HOLIDAY = date(2024, 1, 1)

# Early close day: Black Friday 2023-11-24 (day after Thanksgiving, closes at 13:00 ET)
EARLY_CLOSE_DAY = date(2023, 11, 24)

# Market open on 2024-01-08 is 14:30 UTC (09:30 ET)
OPEN_UTC = datetime(2024, 1, 8, 14, 30, tzinfo=timezone.utc)
# Market close on 2024-01-08 is 21:00 UTC (16:00 ET)
CLOSE_UTC = datetime(2024, 1, 8, 21, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


class TestIsTradingDay:
    def test_regular_trading_day(self):
        assert is_trading_day(TRADING_DAY) is True

    def test_saturday_is_not_trading_day(self):
        assert is_trading_day(WEEKEND) is False

    def test_holiday_is_not_trading_day(self):
        assert is_trading_day(HOLIDAY) is False

    def test_accepts_datetime(self):
        dt = datetime(2024, 1, 8, 15, 0, tzinfo=timezone.utc)
        assert is_trading_day(dt) is True

    def test_defaults_to_today(self):
        # Should not raise — just checks that the function runs with no arg.
        result = is_trading_day()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# market_open / market_close
# ---------------------------------------------------------------------------


class TestMarketTimes:
    def test_open_is_timezone_aware(self):
        open_time = market_open(TRADING_DAY)
        assert open_time.tzinfo is not None

    def test_close_is_timezone_aware(self):
        close_time = market_close(TRADING_DAY)
        assert close_time.tzinfo is not None

    def test_open_before_close(self):
        assert market_open(TRADING_DAY) < market_close(TRADING_DAY)

    def test_regular_open_time(self):
        # NYSE opens at 09:30 ET = 14:30 UTC (no DST in January).
        open_time = market_open(TRADING_DAY)
        assert open_time.hour == 14
        assert open_time.minute == 30

    def test_regular_close_time(self):
        # NYSE closes at 16:00 ET = 21:00 UTC (no DST in January).
        close_time = market_close(TRADING_DAY)
        assert close_time.hour == 21
        assert close_time.minute == 0

    def test_early_close_day_closes_before_1600_et(self):
        # Black Friday (day after Thanksgiving) closes at 13:00 ET = 18:00 UTC.
        close_time = market_close(EARLY_CLOSE_DAY)
        close_et_hour = (close_time.hour - 5) % 24  # ET = UTC-5 in November
        assert close_et_hour == 13

    def test_open_raises_on_weekend(self):
        with pytest.raises(ValueError, match="not a NYSE trading day"):
            market_open(WEEKEND)

    def test_close_raises_on_holiday(self):
        with pytest.raises(ValueError, match="not a NYSE trading day"):
            market_close(HOLIDAY)


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------


class TestIsMarketOpen:
    def test_open_during_trading_hours(self):
        # 15:00 UTC on a trading day = 10:00 ET, market is open.
        dt = datetime(2024, 1, 8, 15, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is True

    def test_closed_before_open(self):
        # 13:00 UTC = 08:00 ET, before the 09:30 open.
        dt = datetime(2024, 1, 8, 13, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False

    def test_closed_after_close(self):
        # 22:00 UTC = 17:00 ET, after the 16:00 close.
        dt = datetime(2024, 1, 8, 22, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False

    def test_closed_on_weekend(self):
        dt = datetime(2024, 1, 6, 15, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False

    def test_closed_on_holiday(self):
        dt = datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc)
        assert is_market_open(dt) is False

    def test_open_at_exact_open_time(self):
        assert is_market_open(OPEN_UTC) is True

    def test_closed_at_exact_close_time(self):
        # Close time is exclusive (>= close means closed).
        assert is_market_open(CLOSE_UTC) is False

    def test_defaults_to_now(self):
        result = is_market_open()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# minutes_until_close
# ---------------------------------------------------------------------------


class TestMinutesUntilClose:
    def test_during_trading_hours(self):
        # 90 minutes before close (21:00 UTC), so at 19:30 UTC.
        dt = datetime(2024, 1, 8, 19, 30, tzinfo=timezone.utc)
        assert minutes_until_close(dt) == 90

    def test_after_close_returns_zero(self):
        dt = datetime(2024, 1, 8, 22, 0, tzinfo=timezone.utc)
        assert minutes_until_close(dt) == 0

    def test_on_non_trading_day_returns_zero(self):
        dt = datetime(2024, 1, 6, 15, 0, tzinfo=timezone.utc)
        assert minutes_until_close(dt) == 0

    def test_returns_integer(self):
        dt = datetime(2024, 1, 8, 15, 0, tzinfo=timezone.utc)
        result = minutes_until_close(dt)
        assert isinstance(result, int)

    def test_defaults_to_now(self):
        result = minutes_until_close()
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# next_trading_day
# ---------------------------------------------------------------------------


class TestNextTradingDay:
    def test_monday_after_friday(self):
        friday = date(2024, 1, 5)
        assert next_trading_day(friday) == date(2024, 1, 8)

    def test_skips_weekend_from_friday(self):
        friday = date(2024, 1, 5)
        nxt = next_trading_day(friday)
        assert nxt.weekday() not in (5, 6)  # Not Saturday or Sunday.

    def test_skips_holiday(self):
        # New Year's Day 2024 is a holiday; next session is 2024-01-02.
        new_years_eve = date(2023, 12, 29)  # Last trading day of 2023.
        nxt = next_trading_day(new_years_eve)
        assert nxt == date(2024, 1, 2)

    def test_returns_date_object(self):
        nxt = next_trading_day(TRADING_DAY)
        assert isinstance(nxt, date)

    def test_defaults_to_today(self):
        nxt = next_trading_day()
        assert isinstance(nxt, date)
        assert nxt > datetime.now(tz=timezone.utc).date()
