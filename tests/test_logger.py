"""
Tests for bot/utils/logger.py.

Covers: get_logger(), CategoryLogger methods, shutdown(), unknown category
handling, and the disk write strategy.  The async DB handler is patched out
so no database connection is required.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

import bot.utils.logger as logger_module
from bot.utils.logger import CategoryLogger, get_logger, shutdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_logger(category: str, tmp_log_dir) -> CategoryLogger:
    """Return a CategoryLogger that writes to tmp_log_dir."""
    # Clear the singleton cache so a new logger is built with our patched LOG_DIR.
    with logger_module._lock:
        logger_module._loggers.pop(category, None)
        # Also remove the underlying stdlib logger to get fresh handlers.
        logging.getLogger(f"ibkr.{category}").handlers.clear()

    return get_logger(category)


# ---------------------------------------------------------------------------
# get_logger
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_returns_category_logger(self, tmp_log_dir):
        log = _fresh_logger("trading", tmp_log_dir)
        assert isinstance(log, CategoryLogger)

    def test_same_instance_returned_for_same_category(self, tmp_log_dir):
        log1 = _fresh_logger("trading", tmp_log_dir)
        log2 = get_logger("trading")
        assert log1 is log2

    def test_different_instance_for_different_category(self, tmp_log_dir):
        log1 = _fresh_logger("signals", tmp_log_dir)
        log2 = _fresh_logger("risk", tmp_log_dir)
        assert log1 is not log2

    def test_disk_log_file_created(self, tmp_log_dir):
        _fresh_logger("ml", tmp_log_dir)
        assert (tmp_log_dir / "ml.log").exists()

    def test_errors_log_file_created(self, tmp_log_dir):
        _fresh_logger("trading", tmp_log_dir)
        assert (tmp_log_dir / "errors.log").exists()

    def test_unknown_category_writes_to_fallback(self, tmp_log_dir):
        """An unknown category logs a warning to errors.log but does not raise."""
        _fresh_logger("unknown_xyz", tmp_log_dir)
        errors = (tmp_log_dir / "errors.log").read_text()
        assert "unknown_xyz" in errors


# ---------------------------------------------------------------------------
# CategoryLogger methods
# ---------------------------------------------------------------------------


class TestCategoryLogger:
    @pytest.fixture()
    def log(self, tmp_log_dir):
        return _fresh_logger("universe", tmp_log_dir)

    def test_has_all_log_level_methods(self, log):
        for method in ("debug", "info", "warning", "error", "critical"):
            assert callable(getattr(log, method))

    def test_info_writes_to_disk(self, log, tmp_log_dir):
        log.info("Test message")
        content = (tmp_log_dir / "universe.log").read_text()
        assert "Test message" in content

    def test_structured_kwargs_appear_in_disk_log(self, log, tmp_log_dir):
        log.info("Order placed", order_id=42, symbol="AAPL")
        content = (tmp_log_dir / "universe.log").read_text()
        assert "order_id=42" in content
        assert "symbol=AAPL" in content

    def test_error_written_to_category_and_errors_log(self, log, tmp_log_dir):
        log.error("Something broke", code=500)
        category_content = (tmp_log_dir / "universe.log").read_text()
        errors_content = (tmp_log_dir / "errors.log").read_text()
        assert "Something broke" in category_content
        assert "Something broke" in errors_content

    def test_debug_not_written_to_errors_log(self, log, tmp_log_dir):
        log.debug("Debug detail")
        errors_content = (tmp_log_dir / "errors.log").read_text()
        assert "Debug detail" not in errors_content

    def test_category_appears_in_log_line(self, log, tmp_log_dir):
        log.info("Check category")
        content = (tmp_log_dir / "universe.log").read_text()
        assert "universe" in content


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_does_not_raise(self, tmp_log_dir):
        _fresh_logger("ibkr", tmp_log_dir)
        shutdown()  # Should complete without exception.

    def test_shutdown_is_idempotent(self, tmp_log_dir):
        _fresh_logger("ibkr", tmp_log_dir)
        shutdown()
        shutdown()  # Second call must also not raise.
