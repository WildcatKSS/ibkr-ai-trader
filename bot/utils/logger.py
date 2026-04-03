"""
Central logging module for ibkr-ai-trader.

Design:
- Every log entry is written synchronously to a disk file first (fast, never blocks the
  trading loop).
- A background thread drains a queue and flushes entries to MariaDB asynchronously.
  If the DB is unavailable the queue is drained to disk and no exception propagates to
  the caller.

Usage:
    from bot.utils.logger import get_logger

    log = get_logger("trading")
    log.info("Order placed", order_id=4821, symbol="AAPL")

All keyword arguments beyond the message are stored as structured fields in MariaDB and
appended as key=value pairs to the disk log line.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset(
    {
        "universe",
        "signals",
        "ml",
        "risk",
        "trading",
        "ibkr",
        "web",
        "claude",
        "sentiment",
    }
)

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# How many records the async queue may hold before the producer blocks.
# At ~1 000 DEBUG lines/sec this gives ~10 s of buffer before back-pressure.
_DB_QUEUE_MAX = 10_000

# ---------------------------------------------------------------------------
# Internal async DB handler
# ---------------------------------------------------------------------------


class _AsyncDbHandler(logging.Handler):
    """
    Non-blocking logging.Handler that puts records on an in-process queue.

    A single daemon thread drains the queue and writes to MariaDB.  If the DB
    is unavailable the record is silently dropped from DB (it is already on
    disk) and a warning is written to logs/errors.log.
    """

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[logging.LogRecord | None] = queue.Queue(
            maxsize=_DB_QUEUE_MAX
        )
        self._worker = threading.Thread(
            target=self._drain, name="logger-db-flush", daemon=True
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # logging.Handler interface
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Queue is full — trading loop must not block.  The record is
            # already on disk; skip DB insert silently.
            pass

    def close(self) -> None:
        # Signal the worker to finish and wait up to 5 s for it to drain.
        self._queue.put(None)
        self._worker.join(timeout=5)
        super().close()

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                break
            self._write_to_db(record)

    def _write_to_db(self, record: logging.LogRecord) -> None:
        """Insert one log record into MariaDB.  Never raises."""
        try:
            # Import here to avoid a circular dependency at module load time
            # and to tolerate the DB not being set up yet during early boot.
            from db.models import LogEntry  # noqa: PLC0415
            from db.session import get_session  # noqa: PLC0415

            entry = LogEntry(
                timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
                level=record.levelname,
                category=getattr(record, "category", "unknown"),
                module=record.module,
                funcName=record.funcName,
                lineno=record.lineno,
                message=record.getMessage(),
                extra=getattr(record, "structured", {}),
            )
            with get_session() as session:
                session.add(entry)
                session.commit()
        except Exception:  # noqa: BLE001
            # DB write failed — record is already on disk, so this is safe to
            # swallow.  Log the failure itself to errors.log without recursion.
            _fallback_error(
                f"DB log flush failed for record: {record.getMessage()[:120]}"
            )


# ---------------------------------------------------------------------------
# Fallback: write directly to errors.log without going through the logger
# ---------------------------------------------------------------------------


def _fallback_error(message: str) -> None:
    errors_log = LOG_DIR / "errors.log"
    try:
        with errors_log.open("a") as fh:
            ts = datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")
            fh.write(f"{ts} | ERROR | logger | {message}\n")
    except OSError:
        pass  # Nothing left to do.


# ---------------------------------------------------------------------------
# Disk formatter
# ---------------------------------------------------------------------------

_DISK_FORMAT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(category)-10s | "
    "%(module)s.%(funcName)s:%(lineno)d | %(message)s%(structured_suffix)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _StructuredFormatter(logging.Formatter):
    """Appends structured key=value fields to the disk log line."""

    def format(self, record: logging.LogRecord) -> str:
        structured: dict[str, Any] = getattr(record, "structured", {})
        if structured:
            record.structured_suffix = " | " + " ".join(
                f"{k}={v}" for k, v in structured.items()
            )
        else:
            record.structured_suffix = ""

        if not hasattr(record, "category"):
            record.category = "unknown"

        return super().format(record)


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_async_db_handler: _AsyncDbHandler | None = None
_loggers: dict[str, "CategoryLogger"] = {}
# Two separate locks: sharing one lock between get_logger() and
# _get_async_db_handler() causes a deadlock because get_logger() holds
# _lock while calling _build_logger(), which calls _get_async_db_handler(),
# which then tries to re-acquire the same non-reentrant lock.
_lock = threading.Lock()           # guards _loggers
_db_handler_lock = threading.Lock()  # guards _async_db_handler


def _get_async_db_handler() -> _AsyncDbHandler:
    global _async_db_handler  # noqa: PLW0603
    if _async_db_handler is None:
        with _db_handler_lock:
            if _async_db_handler is None:
                _async_db_handler = _AsyncDbHandler()
    return _async_db_handler


# ---------------------------------------------------------------------------
# CategoryLogger — thin wrapper that injects structured fields
# ---------------------------------------------------------------------------


class CategoryLogger:
    """
    Logger bound to a single category (e.g. "trading", "signals").

    log.info("message", order_id=123, symbol="AAPL")
    """

    def __init__(self, category: str, _logger: logging.Logger) -> None:
        self._category = category
        self._logger = _logger

    def _log(self, level: int, msg: str, **kwargs: Any) -> None:
        extra = {"category": self._category, "structured": kwargs}
        self._logger.log(level, msg, extra=extra, stacklevel=3)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_logger(category: str) -> CategoryLogger:
    """
    Return a CategoryLogger for *category*.

    The first call for a given category creates and configures the underlying
    logging.Logger; subsequent calls return the cached instance.

    Args:
        category: One of the categories defined in VALID_CATEGORIES.  Passing
                  an unknown category is allowed (it will log to
                  logs/unknown.log) but emits a one-time warning.
    """
    if category not in _loggers:
        with _lock:
            if category not in _loggers:
                _loggers[category] = _build_logger(category)
    return _loggers[category]


def _build_logger(category: str) -> CategoryLogger:
    if category not in VALID_CATEGORIES:
        _fallback_error(
            f"Unknown log category '{category}' — check bot/utils/logger.py"
        )

    logger = logging.getLogger(f"ibkr.{category}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = _StructuredFormatter(fmt=_DISK_FORMAT, datefmt=_DATE_FORMAT)

    # ── 1. Rotating disk handler for this category ───────────────────────
    category_log = LOG_DIR / f"{category}.log"
    disk_handler = logging.handlers.RotatingFileHandler(
        category_log,
        maxBytes=50 * 1024 * 1024,  # 50 MB per file
        backupCount=10,
        encoding="utf-8",
    )
    disk_handler.setLevel(logging.DEBUG)
    disk_handler.setFormatter(formatter)
    logger.addHandler(disk_handler)

    # ── 2. Rotating disk handler for errors.log (ERROR+) ─────────────────
    errors_log = LOG_DIR / "errors.log"
    error_handler = logging.handlers.RotatingFileHandler(
        errors_log,
        maxBytes=50 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logger.addHandler(error_handler)

    # ── 3. Async DB handler ───────────────────────────────────────────────
    db_handler = _get_async_db_handler()
    db_handler.setLevel(logging.DEBUG)
    logger.addHandler(db_handler)

    return CategoryLogger(category, logger)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def shutdown() -> None:
    """
    Flush the async DB queue and close all handlers.

    Call this once during application shutdown (e.g. in the systemd stop
    handler or at the end of a test).  Safe to call multiple times.
    """
    global _async_db_handler  # noqa: PLW0603
    if _async_db_handler is not None:
        _async_db_handler.close()
        _async_db_handler = None
    logging.shutdown()
