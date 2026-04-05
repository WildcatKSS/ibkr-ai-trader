"""
Seed default operational settings into MariaDB.

Run this once after the initial database migration:

    source /opt/ibkr-trader/venv/bin/activate
    python db/seed.py

The script is idempotent: existing settings are not overwritten, only
missing ones are inserted.  Re-run it after adding new settings to
ensure all deployments have the required defaults.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a standalone script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from db.models import Setting  # noqa: E402
from db.session import get_session  # noqa: E402
from bot.utils.logger import get_logger  # noqa: E402

log = get_logger("ibkr")

# ---------------------------------------------------------------------------
# Default settings
# ---------------------------------------------------------------------------
# Each entry: (key, value, description)
# Keep sorted by category for readability.

DEFAULTS: list[tuple[str, str, str]] = [
    # ── Trading mode ─────────────────────────────────────────────────────────
    (
        "TRADING_MODE",
        "dryrun",
        "Trading mode: 'dryrun' (log only), 'paper' (IBKR paper account), or 'live'.",
    ),
    # ── Market timing ────────────────────────────────────────────────────────
    (
        "EOD_CLOSE_MINUTES",
        "15",
        "Minutes before market close to start closing all open positions.",
    ),
    (
        "MARKET_OPEN_BUFFER_MINUTES",
        "5",
        "Minutes after market open to wait before entering new positions "
        "(avoids opening-bell volatility).",
    ),
    # ── Universe selection ───────────────────────────────────────────────────
    (
        "UNIVERSE_MAX_SYMBOLS",
        "20",
        "Maximum number of symbols in the daily trading universe.",
    ),
    (
        "UNIVERSE_APPROVAL_MODE",
        "autonomous",
        "Universe selection mode: 'autonomous' (Claude decides) or "
        "'approval' (human confirms before trading starts).",
    ),
    (
        "UNIVERSE_MIN_AVG_VOLUME",
        "500000",
        "Minimum 20-day average daily volume for a symbol to enter the universe.",
    ),
    (
        "UNIVERSE_MIN_PRICE",
        "5.0",
        "Minimum share price (USD) for universe inclusion.",
    ),
    (
        "UNIVERSE_MAX_PRICE",
        "500.0",
        "Maximum share price (USD) for universe inclusion.",
    ),
    # ── Position sizing ──────────────────────────────────────────────────────
    (
        "POSITION_SIZING_METHOD",
        "fixed_pct",
        "Position sizing method: 'fixed_pct', 'fixed_amount', or 'kelly'.",
    ),
    (
        "POSITION_SIZE_PCT",
        "2.0",
        "Capital allocated per trade as a percentage of total portfolio value. "
        "Used when POSITION_SIZING_METHOD is 'fixed_pct'.",
    ),
    (
        "POSITION_SIZE_AMOUNT",
        "5000.0",
        "Fixed dollar amount per trade. "
        "Used when POSITION_SIZING_METHOD is 'fixed_amount'.",
    ),
    (
        "POSITION_MAX_PCT",
        "5.0",
        "Hard cap: maximum capital in a single position as a percentage of "
        "portfolio value, regardless of sizing method.",
    ),
    # ── Risk management ──────────────────────────────────────────────────────
    (
        "CIRCUIT_BREAKER_DAILY_LOSS_PCT",
        "3.0",
        "Halt all trading for the day when daily P&L loss exceeds this "
        "percentage of portfolio value.",
    ),
    (
        "CIRCUIT_BREAKER_CONSECUTIVE_LOSSES",
        "5",
        "Halt all trading for the day after this many consecutive losing trades.",
    ),
    (
        "STOP_LOSS_PCT",
        "1.0",
        "Default stop-loss as a percentage of entry price.",
    ),
    (
        "TAKE_PROFIT_PCT",
        "2.0",
        "Default take-profit as a percentage of entry price.",
    ),
    # ── Gap filter ───────────────────────────────────────────────────────────
    (
        "GAP_FILTER_MAX_PCT",
        "3.0",
        "Exclude symbols with an expected opening gap larger than this "
        "percentage (earnings, news events, etc.).",
    ),
    # ── Order execution ──────────────────────────────────────────────────────
    (
        "ORDER_FILL_TIMEOUT_SECONDS",
        "60",
        "Cancel or convert to market order if a limit order is unfilled "
        "after this many seconds.",
    ),
    # ── Alerting ─────────────────────────────────────────────────────────────
    (
        "ALERTS_EMAIL_ENABLED",
        "false",
        "Send email alerts for circuit breaker trips, connection loss, and "
        "daily P&L summaries.",
    ),
    (
        "ALERTS_EMAIL_FROM",
        "",
        "Sender address for email alerts.",
    ),
    (
        "ALERTS_EMAIL_TO",
        "",
        "Recipient address for email alerts.",
    ),
    (
        "ALERTS_SMTP_HOST",
        "smtp.gmail.com",
        "SMTP server hostname.",
    ),
    (
        "ALERTS_SMTP_PORT",
        "587",
        "SMTP server port.",
    ),
    (
        "ALERTS_WEBHOOKS_ENABLED",
        "false",
        "Enable HTTP webhook dispatch for trade events.",
    ),
]

# ---------------------------------------------------------------------------


def seed() -> None:
    now = datetime.now(tz=timezone.utc)
    inserted = 0
    skipped = 0

    with get_session() as session:
        existing_keys: set[str] = {
            key for (key,) in session.query(Setting.key).all()
        }
        for key, value, description in DEFAULTS:
            if key in existing_keys:
                skipped += 1
                continue
            session.add(
                Setting(
                    key=key,
                    value=value,
                    description=description,
                    updated_at=now,
                )
            )
            inserted += 1

    log.info("Seed complete", inserted=inserted, skipped=skipped)


if __name__ == "__main__":
    seed()
