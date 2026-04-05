"""
Configuration loader for ibkr-ai-trader.

All operational settings are stored in MariaDB (the `settings` table) and
managed via the web interface.  This module is the single access point for
those settings.  Never read from .env or a YAML file for operational config.

Usage:
    from bot.utils.config import get, reload

    mode   = get("TRADING_MODE")                      # str
    min    = get("EOD_CLOSE_MINUTES", cast=int)       # int
    pct    = get("POSITION_SIZE_PCT", cast=float)     # float
    active = get("ALERTS_EMAIL_ENABLED", cast=bool)   # bool

    # Force an immediate reload from the database (e.g. after the web
    # interface writes a new value):
    reload()

Cache behaviour:
- All settings are loaded in a single query and cached in memory.
- The cache is refreshed automatically after CONFIG_CACHE_TTL seconds
  (default: 60).  This bounds the lag between a web-UI change and the
  trading loop picking it up.
- If the database is unreachable during a refresh, the stale cache is
  kept and a warning is written to the log.  The trading loop is never
  blocked waiting for the database.
- If the cache has never been populated and the database is unreachable,
  a ConfigError is raised so the caller can decide how to handle cold-
  start failures.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, TypeVar, overload

from bot.utils.logger import get_logger

log = get_logger("ibkr")

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when a required setting cannot be retrieved."""


# ---------------------------------------------------------------------------
# Cache internals
# ---------------------------------------------------------------------------

# Seconds before the cache is considered stale and reloaded from the DB.
# Configurable via the CONFIG_CACHE_TTL environment variable (deployment
# concern, not a business setting).
def _parse_ttl() -> int:
    raw = os.getenv("CONFIG_CACHE_TTL", "60")
    try:
        return int(raw)
    except (ValueError, TypeError):
        log.warning("Invalid CONFIG_CACHE_TTL value, using default of 60s", value=raw)
        return 60


_TTL: int = _parse_ttl()

_cache: dict[str, str] = {}
_loaded_at: float = 0.0  # epoch seconds of last successful DB load
_lock = threading.Lock()


def _load_from_db() -> dict[str, str]:
    """Fetch all rows from the settings table and return as {key: value}."""
    # Late import to mirror logger.py's pattern: avoid circular imports at
    # module load time and tolerate the DB not being set up during early boot.
    from sqlalchemy import select  # noqa: PLC0415

    from db.models import Setting  # noqa: PLC0415
    from db.session import get_session  # noqa: PLC0415

    with get_session() as session:
        rows = session.execute(select(Setting.key, Setting.value)).all()
    return {key: value for key, value in rows}


def _refresh(force: bool = False) -> None:
    """
    Reload the cache from the database if it is stale or *force* is True.

    Must be called while *_lock* is held.
    """
    global _cache, _loaded_at  # noqa: PLW0603

    now = time.monotonic()
    if not force and _loaded_at > 0 and (now - _loaded_at) < _TTL:
        return  # Cache is still fresh.

    try:
        fresh = _load_from_db()
        _cache = fresh
        _loaded_at = now
        log.debug("Config cache refreshed", settings_count=len(fresh))
    except Exception as exc:  # noqa: BLE001
        if _loaded_at == 0:
            # Cold start — no cached data to fall back on.
            raise ConfigError(
                "Cannot load settings from database and no cached values "
                "are available.  Ensure MariaDB is running and the "
                "settings table has been seeded (python db/seed.py)."
            ) from exc
        # Warm cache available — log and continue with stale values.
        log.warning(
            "Config cache refresh failed — using stale values",
            error=str(exc),
            stale_seconds=int(now - _loaded_at),
        )


# ---------------------------------------------------------------------------
# Type-cast helpers
# ---------------------------------------------------------------------------

_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})


def _cast_value(raw: str, cast: type) -> Any:
    if cast is str:
        return raw
    if cast is int:
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"Cannot cast {raw!r} to int") from exc
    if cast is float:
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"Cannot cast {raw!r} to float") from exc
    if cast is bool:
        return raw.strip().lower() in _BOOL_TRUE
    raise ConfigError(f"Unsupported cast type: {cast!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


@overload
def get(key: str) -> str: ...


@overload
def get(key: str, *, cast: type[_T]) -> _T: ...


@overload
def get(key: str, *, default: str) -> str: ...


@overload
def get(key: str, *, default: _T, cast: type[_T]) -> _T: ...


_SENTINEL = object()


def get(key: str, *, default: Any = _SENTINEL, cast: type = str) -> Any:
    """
    Return the value of *key* from the settings table.

    Args:
        key:     Setting key as stored in the database (e.g. "TRADING_MODE").
        default: Value to return if *key* is not present in the database.
                 If omitted and the key is missing, ConfigError is raised.
        cast:    Python type to convert the raw string to.
                 Supported: str (default), int, float, bool.

    Returns:
        The setting value cast to *cast*.

    Raises:
        ConfigError: If the key is missing and no default is provided, or if
                     the cast fails, or if the DB is unreachable on cold start.
    """
    with _lock:
        _refresh()
        raw = _cache.get(key)

    if raw is None:
        if default is not _SENTINEL:
            return default if cast is str else _cast_value(str(default), cast)
        raise ConfigError(
            f"Setting {key!r} not found in database.  "
            f"Add it via the web interface or run python db/seed.py."
        )

    return _cast_value(raw, cast)


def reload() -> None:
    """
    Force an immediate reload of all settings from the database.

    Call this after the web interface writes a new value and you need the
    change to take effect before the next TTL expiry.
    """
    with _lock:
        _refresh(force=True)


def all_settings() -> dict[str, str]:
    """
    Return a snapshot of all cached settings as {key: value}.

    Triggers a cache refresh if the cache is stale.  Intended for use by
    the web interface to display the current configuration.
    """
    with _lock:
        _refresh()
        return dict(_cache)
