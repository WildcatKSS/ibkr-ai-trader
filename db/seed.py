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

from sqlalchemy import select as sa_select

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
        "10",
        "Watchlist size: number of symbols returned by the daily universe scan. "
        "In autonomous mode only the top-1 is traded; in approval mode the user "
        "picks from this list.",
    ),
    (
        "UNIVERSE_APPROVAL_MODE",
        "autonomous",
        "Universe selection mode: 'autonomous' (bot trades the highest-scored "
        "symbol automatically) or 'approval' (user picks from the watchlist via "
        "the web dashboard before trading starts).",
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
    (
        "UNIVERSE_POOL",
        "SPY,QQQ,IWM,DIA,XLF,XLK,XLE,XLV,GLD,"
        "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AMD,AVGO,QCOM,INTC,CRM,ADBE,ORCL,NFLX,"
        "JPM,BAC,GS,MS,V,MA,"
        "JNJ,UNH,LLY,PFE,TMO,"
        "HD,WMT,COST,NKE,SBUX,MCD,"
        "CVX,XOM,COP,"
        "CAT,BA,GE,HON",
        "Comma-separated list of candidate symbols scanned daily by the universe "
        "scanner. Edit via the web dashboard to customise the opportunity set.",
    ),
    (
        "UNIVERSE_BARS_HISTORY",
        "250",
        "Number of daily bars fetched per symbol. Must be ≥ 210 (SMA-200 needs "
        "200 bars plus a safety margin).",
    ),
    # ── Universe criteria — daily timeframe ──────────────────────────────────
    (
        "UNIVERSE_EMA9_PERIOD",
        "9",
        "Period for the short-term EMA (price momentum indicator).",
    ),
    (
        "UNIVERSE_SMA50_PERIOD",
        "50",
        "Period for the medium-term SMA (trend confirmation).",
    ),
    (
        "UNIVERSE_SMA200_PERIOD",
        "200",
        "Period for the long-term SMA (macro trend filter).",
    ),
    (
        "UNIVERSE_VOLUME_MA_PERIOD",
        "20",
        "Period for the volume moving average used as the 'average volume' baseline.",
    ),
    (
        "UNIVERSE_HH_HL_LOOKBACK",
        "20",
        "Number of daily bars used to detect higher-highs / higher-lows structure.",
    ),
    (
        "UNIVERSE_BODY_RATIO_MIN",
        "0.60",
        "Minimum candle body-to-range ratio to classify a candle as 'strong bullish'. "
        "Range 0–1; default 0.60 means body ≥ 60 % of the high-low range.",
    ),
    (
        "UNIVERSE_WICK_RATIO_MAX",
        "0.30",
        "Maximum upper-wick-to-range ratio to classify a candle as having 'small "
        "rejection'. Range 0–1; default 0.30 means upper wick ≤ 30 % of range.",
    ),
    (
        "UNIVERSE_NEAR_RESISTANCE_PCT",
        "2.0",
        "A symbol is 'near resistance' when its last close is within this many "
        "percent below the most recent swing-high (breakout candidate signal).",
    ),
    (
        "UNIVERSE_MOMENTUM_GAP_PCT",
        "0.5",
        "Minimum gap-up percentage (today open vs. prior close) that counts as "
        "a momentum / pre-market signal.",
    ),
    (
        "UNIVERSE_MOMENTUM_5D_RETURN_PCT",
        "5.0",
        "Minimum 5-day price return (%) that counts as strong momentum, "
        "in addition to the gap-up check.",
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
            key for (key,) in session.execute(sa_select(Setting.key)).all()
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
