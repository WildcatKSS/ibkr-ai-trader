"""
Tests for bot/utils/config.py.

All tests patch _load_from_db so no database connection is made.
The reset_config_cache fixture in conftest.py clears module-level
globals between tests automatically.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

import bot.utils.config as cfg
from bot.utils.config import ConfigError, all_settings, get, reload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FAKE_SETTINGS = {
    "TRADING_MODE": "dryrun",
    "EOD_CLOSE_MINUTES": "15",
    "POSITION_SIZE_PCT": "2.5",
    "ALERTS_EMAIL_ENABLED": "true",
    "CIRCUIT_BREAKER_DAILY_LOSS_PCT": "3.0",
    "UNIVERSE_MAX_SYMBOLS": "20",
    "FEATURE_DISABLED": "false",
}


def _patch_db(settings: dict | None = None):
    """Return a context manager that patches _load_from_db."""
    data = settings if settings is not None else FAKE_SETTINGS
    return patch("bot.utils.config._load_from_db", return_value=data)


# ---------------------------------------------------------------------------
# Basic retrieval
# ---------------------------------------------------------------------------


class TestGet:
    def test_returns_string_by_default(self):
        with _patch_db():
            assert get("TRADING_MODE") == "dryrun"

    def test_cast_to_int(self):
        with _patch_db():
            assert get("EOD_CLOSE_MINUTES", cast=int) == 15

    def test_cast_to_float(self):
        with _patch_db():
            assert get("POSITION_SIZE_PCT", cast=float) == 2.5

    def test_cast_to_bool_true(self):
        with _patch_db():
            assert get("ALERTS_EMAIL_ENABLED", cast=bool) is True

    def test_cast_to_bool_false(self):
        with _patch_db():
            assert get("FEATURE_DISABLED", cast=bool) is False

    @pytest.mark.parametrize("raw", ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"])
    def test_bool_truthy_values(self, raw):
        with _patch_db({"FLAG": raw}):
            assert get("FLAG", cast=bool) is True

    @pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off", "anything"])
    def test_bool_falsy_values(self, raw):
        with _patch_db({"FLAG": raw}):
            assert get("FLAG", cast=bool) is False


# ---------------------------------------------------------------------------
# Missing keys and defaults
# ---------------------------------------------------------------------------


class TestMissingKeys:
    def test_raises_config_error_for_missing_key(self):
        with _patch_db():
            with pytest.raises(ConfigError, match="MISSING_KEY"):
                get("MISSING_KEY")

    def test_returns_string_default(self):
        with _patch_db():
            assert get("MISSING_KEY", default="fallback") == "fallback"

    def test_returns_int_default_with_cast(self):
        with _patch_db():
            assert get("MISSING_KEY", default=0, cast=int) == 0

    def test_returns_float_default_with_cast(self):
        with _patch_db():
            assert get("MISSING_KEY", default=1.5, cast=float) == 1.5

    def test_returns_bool_default_with_cast(self):
        with _patch_db():
            assert get("MISSING_KEY", default=False, cast=bool) is False


# ---------------------------------------------------------------------------
# Cast errors
# ---------------------------------------------------------------------------


class TestCastErrors:
    def test_invalid_int_raises_config_error(self):
        with _patch_db({"BAD_INT": "not_a_number"}):
            with pytest.raises(ConfigError, match="int"):
                get("BAD_INT", cast=int)

    def test_invalid_float_raises_config_error(self):
        with _patch_db({"BAD_FLOAT": "abc"}):
            with pytest.raises(ConfigError, match="float"):
                get("BAD_FLOAT", cast=float)

    def test_unsupported_cast_type_raises_config_error(self):
        with _patch_db():
            with pytest.raises(ConfigError, match="Unsupported"):
                get("TRADING_MODE", cast=list)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class TestCache:
    def test_cache_is_populated_after_first_get(self):
        with _patch_db():
            get("TRADING_MODE")
            assert cfg._cache == FAKE_SETTINGS

    def test_db_called_only_once_within_ttl(self):
        with _patch_db() as mock_load:
            get("TRADING_MODE")
            get("EOD_CLOSE_MINUTES", cast=int)
            get("ALERTS_EMAIL_ENABLED", cast=bool)
            assert mock_load.call_count == 1

    def test_reload_forces_db_call(self):
        with _patch_db() as mock_load:
            get("TRADING_MODE")
            reload()
            assert mock_load.call_count == 2

    def test_stale_cache_refreshes_after_ttl(self, monkeypatch):
        monkeypatch.setattr(cfg, "_TTL", 0)  # Expire immediately.
        with _patch_db() as mock_load:
            get("TRADING_MODE")
            time.sleep(0.01)
            get("TRADING_MODE")
            assert mock_load.call_count == 2

    def test_stale_cache_kept_on_db_failure(self, monkeypatch):
        """DB failure during refresh keeps the old cache intact."""
        with _patch_db():
            get("TRADING_MODE")  # Warm the cache.

        # Force TTL expiry without resetting _loaded_at to 0 (which would
        # trigger the cold-start path).  Use TTL=0 so any subsequent call
        # sees the cache as stale.
        monkeypatch.setattr(cfg, "_TTL", 0)

        with patch("bot.utils.config._load_from_db", side_effect=RuntimeError("DB down")):
            # Should not raise — stale cache is served with a warning.
            value = get("TRADING_MODE")
            assert value == "dryrun"

    def test_cold_start_db_failure_raises_config_error(self):
        """Empty cache + DB failure raises ConfigError on cold start."""
        with patch("bot.utils.config._load_from_db", side_effect=RuntimeError("DB down")):
            with pytest.raises(ConfigError, match="Cannot load settings"):
                get("TRADING_MODE")


# ---------------------------------------------------------------------------
# all_settings
# ---------------------------------------------------------------------------


class TestAllSettings:
    def test_returns_all_settings_as_dict(self):
        with _patch_db():
            result = all_settings()
            assert result == FAKE_SETTINGS

    def test_returns_a_copy_not_the_cache(self):
        """Modifying the returned dict must not affect the cache."""
        with _patch_db():
            result = all_settings()
            result["TRADING_MODE"] = "live"
            assert cfg._cache["TRADING_MODE"] == "dryrun"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_get_does_not_raise(self):
        """
        100 threads calling get() simultaneously must all return the correct
        value without a lock error, data race, or exception.
        """
        import concurrent.futures

        with _patch_db():
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                futures = [pool.submit(get, "TRADING_MODE") for _ in range(100)]
                results = [f.result() for f in futures]

        assert all(r == "dryrun" for r in results)

    def test_concurrent_reload_does_not_corrupt_cache(self):
        """
        Interleaved reload() and get() calls must not produce a corrupted
        cache (e.g. empty dict or KeyError).
        """
        import concurrent.futures

        with _patch_db():
            def _reload_then_get():
                reload()
                return get("EOD_CLOSE_MINUTES")

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(_reload_then_get) for _ in range(50)]
                results = [f.result() for f in futures]

        assert all(r == "15" for r in results)
