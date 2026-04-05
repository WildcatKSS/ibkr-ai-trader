"""
FastAPI application entry point for ibkr-ai-trader web interface.

Run via systemd:
    uvicorn web.api.main:app --host 127.0.0.1 --port 8000 --workers 2

Routes:
    POST /api/auth/login    — obtain a JWT token (no auth required)
    GET  /health            — liveness probe   (no auth required)
    GET  /api/status        — bot runtime status
    GET  /api/settings      — list all settings
    PUT  /api/settings/{key}— update a setting
    GET  /api/logs          — recent log entries
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bot.utils.logger import get_logger, shutdown as logger_shutdown
from web.api.auth import require_auth, router as auth_router

log = get_logger("web")


# ---------------------------------------------------------------------------
# CORS helpers
# ---------------------------------------------------------------------------


def _cors_origins() -> list[str]:
    """
    Build the allowed-origin list from the environment.

    Always permits localhost for local development.  If DOMAIN is set,
    the production HTTPS origin is added automatically.
    """
    origins = ["http://localhost", "http://127.0.0.1"]
    domain = os.getenv("DOMAIN", "").strip()
    if domain:
        origins.append(f"https://{domain}")
    return origins


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Web API starting up")
    yield
    log.info("Web API shutting down")
    logger_shutdown()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="IBKR AI Trader",
    description="Management dashboard API for the IBKR AI intraday trading bot.",
    version="0.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router)


# ---------------------------------------------------------------------------
# Health  (public — required by Nginx upstream health checks)
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"], summary="Liveness probe")
async def health() -> dict:
    """Returns HTTP 200 as long as the process is running."""
    return {
        "status": "ok",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Status  (protected)
# ---------------------------------------------------------------------------


@app.get("/api/status", tags=["status"], summary="Bot runtime status",
         dependencies=[Depends(require_auth)])
async def status() -> dict:
    """Returns the current trading mode and calendar status."""
    from bot.utils.calendar import is_market_open, is_trading_day
    from bot.utils.config import ConfigError, get

    try:
        trading_mode = get("TRADING_MODE")
    except ConfigError:
        trading_mode = "unknown"

    return {
        "trading_mode": trading_mode,
        "market_open": is_market_open(),
        "trading_day": is_trading_day(),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Settings  (protected)
# ---------------------------------------------------------------------------


@app.get("/api/settings", tags=["settings"], summary="List all settings",
         dependencies=[Depends(require_auth)])
async def list_settings() -> dict:
    """Return all operational settings as a flat key→value dict."""
    from bot.utils.config import all_settings

    return all_settings()


@app.put(
    "/api/settings/{key}",
    tags=["settings"],
    summary="Update a setting",
    dependencies=[Depends(require_auth)],
)
async def update_setting(key: str, value: str) -> dict:
    """
    Persist a new value for *key* in MariaDB and invalidate the config cache.

    The value is always stored as a string; type coercion happens at read time
    via ``bot.utils.config.get(..., cast=...)``.
    """
    from db.models import Setting
    from db.session import get_session

    from bot.utils.config import reload

    now = datetime.now(tz=timezone.utc)

    with get_session() as session:
        obj = session.get(Setting, key)
        if obj is None:
            obj = Setting(key=key, value=value, updated_at=now)
            session.add(obj)
        else:
            obj.value = value
            obj.updated_at = now

    reload()
    log.info("Setting updated", key=key)
    return {"key": key, "value": value}


# ---------------------------------------------------------------------------
# Logs  (protected)
# ---------------------------------------------------------------------------


@app.get("/api/logs", tags=["logs"], summary="Recent log entries",
         dependencies=[Depends(require_auth)])
async def recent_logs(
    category: str | None = None,
    level: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """
    Return the most recent *limit* log entries from MariaDB.

    Optional filters:
    - **category** — e.g. ``trading``, ``signals``, ``ibkr``
    - **level**    — e.g. ``INFO``, ``WARNING``, ``ERROR``
    """
    from sqlalchemy import desc, select

    from db.models import LogEntry
    from db.session import get_session

    limit = min(limit, 500)  # cap to prevent large result sets

    with get_session() as session:
        q = select(LogEntry).order_by(desc(LogEntry.timestamp)).limit(limit)
        if category:
            q = q.where(LogEntry.category == category)
        if level:
            q = q.where(LogEntry.level == level.upper())
        rows = session.scalars(q).all()

    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "level": r.level,
            "category": r.category,
            "module": r.module,
            "message": r.message,
            "extra": r.extra,
        }
        for r in rows
    ]
