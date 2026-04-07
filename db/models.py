"""
SQLAlchemy ORM models for ibkr-ai-trader.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class LogEntry(Base):
    """
    One log record written by bot/utils/logger.py.

    The logger writes to disk first (synchronous) and then enqueues records
    for insertion here by a background thread.  See logger.py for the full
    write strategy.
    """

    __tablename__ = "log_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    module: Mapped[str] = mapped_column(String(100), nullable=False)
    funcName: Mapped[str] = mapped_column(String(100), nullable=False)
    lineno: Mapped[int] = mapped_column(Integer, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured key=value fields passed as kwargs to log.info(...) etc.
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<LogEntry id={self.id} level={self.level} "
            f"category={self.category} ts={self.timestamp}>"
        )


class Setting(Base):
    """
    Operational settings managed via the web interface and stored in MariaDB.

    All runtime configuration (trading mode, risk parameters, position sizing,
    universe selection, etc.) lives here.  Never read from .env or a YAML file.
    Use bot/utils/config.py to load settings — it caches values and reloads
    them from this table on demand.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable description shown in the web interface.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Setting key={self.key} value={self.value!r}>"


class Trade(Base):
    """
    One intraday trade executed (or logged in dryrun) by the bot.

    Lifecycle:  pending → open → filled → closed
                                        ↘ cancelled / error
    In dryrun mode the status is set to "dryrun" after creation.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Identity ──────────────────────────────────────────────────────────
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # "long" | "short"
    trading_mode: Mapped[str] = mapped_column(String(10), nullable=False)  # paper/live/dryrun
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # ── Sizing & prices ───────────────────────────────────────────────────
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)   # intended entry
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # actual fill
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)   # close price
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── IBKR ──────────────────────────────────────────────────────────────
    ibkr_order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Signal provenance ─────────────────────────────────────────────────
    ml_label: Mapped[str] = mapped_column(String(20), nullable=False)
    ml_probability: Mapped[float] = mapped_column(Float, nullable=False)
    confirmed_15min: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Trade id={self.id} symbol={self.symbol} action={self.action} "
            f"status={self.status}>"
        )
