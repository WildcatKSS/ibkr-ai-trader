"""
FastAPI application entry point for ibkr-ai-trader web interface.

Run via systemd:
    uvicorn web.api.main:app --host 127.0.0.1 --port 8000 --workers 1

Routes:
    POST /api/auth/login    — obtain a JWT token (no auth required)
    GET  /health            — liveness probe   (no auth required)
    GET  /api/status        — bot runtime status
    GET  /api/settings      — list all settings
    PUT  /api/settings/{key}— update a setting
    GET  /api/logs          — recent log entries
    GET  /api/trades        — trade history with filters
    GET  /api/trades/open   — currently open positions
    GET  /api/trades/{id}   — single trade detail
    GET  /api/performance   — P&L and performance metrics
    GET  /api/portfolio     — portfolio summary
    POST /api/backtesting/run — run a backtest
    GET  /api/bot/service-status — ibkr-bot systemd state
    POST /api/bot/start     — start the ibkr-bot service
    POST /api/bot/stop      — stop the ibkr-bot service (confirm="STOP")
    POST /api/bot/restart   — restart the ibkr-bot service (confirm="RESTART")
    POST /api/logs/stream-token — issue a single-use SSE stream token
    GET  /api/logs/stream   — Server-Sent Events log stream (uses stream_token)
    GET  /api/ml/versions   — list all registered model versions
    GET  /api/ml/current    — current active model version
    POST /api/ml/retrain    — queue a retrain job (202 + job_id)
    POST /api/ml/rollback   — roll back to a previous model version
    GET  /api/ml/jobs/{id}  — inspect a single ML job
    GET  /api/ml/jobs       — list recent ML jobs
    GET  /api/universe/pending — most recent scan awaiting approval
    GET  /api/universe/history — recent scans
    POST /api/universe/approve — approve a candidate symbol
    POST /api/universe/reject  — reject today's scan
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Path, status
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from bot.utils.logger import get_logger, shutdown as logger_shutdown
from web.api.auth import require_auth, router as auth_router
from web.api.logs_stream import router as logs_stream_router
from web.api.ml_admin import router as ml_admin_router
from web.api.service import router as service_router
from web.api.universe import router as universe_router

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
    origins = [
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:5173",    # Vite dev server
        "http://127.0.0.1:5173",
    ]
    domain = os.getenv("DOMAIN", "").strip()
    if domain:
        origins.append(f"https://{domain}")
    return origins


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _warn_if_multi_worker() -> None:
    try:
        workers = int(os.getenv("WEB_CONCURRENCY", "1"))
        if workers > 1:
            log.warning(
                "Running with multiple workers — process-local state "
                "(rate limiter, config cache, SSE tokens, locks) will NOT be "
                "shared. Set --workers 1 for this single-user application.",
                workers=workers,
            )
    except (ValueError, TypeError):
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Web API starting up")
    _warn_if_multi_worker()
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
app.include_router(service_router)
app.include_router(logs_stream_router)
app.include_router(ml_admin_router)
app.include_router(universe_router)


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


_VALID_TRADING_MODES = frozenset({"paper", "live", "dryrun"})


class UpdateSettingBody(BaseModel):
    """Request body for PUT /api/settings/{key}."""

    value: str = Field(..., max_length=10_000)
    description: str | None = Field(default=None, max_length=500)


@app.put(
    "/api/settings/{key}",
    tags=["settings"],
    summary="Update a setting",
    dependencies=[Depends(require_auth)],
)
async def update_setting(
    key: str = Path(..., pattern=r"^[A-Z][A-Z0-9_]{0,99}$", max_length=100),
    body: UpdateSettingBody = ...,
) -> dict:
    """
    Persist a new value for *key* in MariaDB and invalidate the config cache.

    Key must match ``^[A-Z][A-Z0-9_]{0,99}$`` (uppercase, digits, underscores).
    Value is sent in the JSON request body (not as a URL query parameter) to
    prevent sensitive values from appearing in server logs or browser history.
    TRADING_MODE is validated against the allowed set (``paper``, ``live``,
    ``dryrun``).

    For existing keys the stored description is preserved unless *description*
    is explicitly supplied.  For new keys, *description* is optional.

    The value is stored as a string; type coercion happens at read time
    via ``bot.utils.config.get(..., cast=...)``.
    """
    if key == "TRADING_MODE" and body.value not in _VALID_TRADING_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"TRADING_MODE must be one of: {', '.join(sorted(_VALID_TRADING_MODES))}",
        )
    from db.models import Setting
    from db.session import get_session

    from bot.utils.config import reload

    now = datetime.now(tz=timezone.utc)

    with get_session() as session:
        obj = session.get(Setting, key)
        if obj is None:
            obj = Setting(
                key=key,
                value=body.value,
                description=body.description,
                updated_at=now,
            )
            session.add(obj)
        else:
            obj.value = body.value
            obj.updated_at = now
            if body.description is not None:  # only overwrite when caller supplied one
                obj.description = body.description

    reload()
    log.info("Setting updated", key=key)
    return {"key": key, "value": body.value}


# ---------------------------------------------------------------------------
# Logs  (protected)
# ---------------------------------------------------------------------------


_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


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
    - **category** — one of the categories defined in ``bot/utils/logger.py``
      (e.g. ``trading``, ``signals``, ``ibkr``)
    - **level**    — ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, or ``CRITICAL``
    """
    from bot.utils.logger import VALID_CATEGORIES
    from sqlalchemy import desc, select

    from db.models import LogEntry
    from db.session import get_session

    if category is not None and category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Valid values: {', '.join(sorted(VALID_CATEGORIES))}",
        )
    if level is not None and level.upper() not in _VALID_LOG_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid level. Valid values: {', '.join(sorted(_VALID_LOG_LEVELS))}",
        )

    limit = max(1, min(limit, 500))  # enforce 1 ≤ limit ≤ 500

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


# ---------------------------------------------------------------------------
# Trades  (protected)
# ---------------------------------------------------------------------------


def _trade_to_dict(t) -> dict:
    """Convert a Trade ORM instance to a JSON-safe dict."""
    return {
        "id": t.id,
        "symbol": t.symbol,
        "action": t.action,
        "trading_mode": t.trading_mode,
        "status": t.status,
        "shares": t.shares,
        "entry_price": t.entry_price,
        "target_price": t.target_price,
        "stop_price": t.stop_price,
        "fill_price": t.fill_price,
        "exit_price": t.exit_price,
        "pnl": t.pnl,
        "ibkr_order_id": t.ibkr_order_id,
        "ml_label": t.ml_label,
        "ml_probability": t.ml_probability,
        "confirmed_15min": t.confirmed_15min,
        "explanation": t.explanation,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "filled_at": t.filled_at.isoformat() if t.filled_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


@app.get("/api/trades", tags=["trades"], summary="Trade history",
         dependencies=[Depends(require_auth)])
async def list_trades(
    symbol: str | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Return trades with optional filters.

    - **symbol** — filter by ticker
    - **status_filter** — ``closed``, ``open``, ``filled``, ``dryrun``, ``error``
    - **limit** / **offset** — pagination (max 500)
    """
    from sqlalchemy import desc, func, select

    from db.models import Trade
    from db.session import get_session

    limit = max(1, min(limit, 500))
    offset = max(0, offset)

    with get_session() as session:
        q = select(Trade).order_by(desc(Trade.created_at))
        count_q = select(func.count(Trade.id))

        if symbol:
            q = q.where(Trade.symbol == symbol.upper())
            count_q = count_q.where(Trade.symbol == symbol.upper())
        if status_filter:
            q = q.where(Trade.status == status_filter)
            count_q = count_q.where(Trade.status == status_filter)

        total = session.execute(count_q).scalar() or 0
        rows = session.scalars(q.offset(offset).limit(limit)).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "trades": [_trade_to_dict(r) for r in rows],
    }


@app.get("/api/trades/open", tags=["trades"], summary="Open positions",
         dependencies=[Depends(require_auth)])
async def open_trades() -> list[dict]:
    """Return all trades with status pending, open, or filled (active positions)."""
    from sqlalchemy import desc, select

    from db.models import Trade
    from db.session import get_session

    with get_session() as session:
        rows = session.scalars(
            select(Trade)
            .where(Trade.status.in_(["pending", "open", "filled"]))
            .order_by(desc(Trade.created_at))
        ).all()

    return [_trade_to_dict(r) for r in rows]


@app.get("/api/trades/{trade_id}", tags=["trades"], summary="Trade detail",
         dependencies=[Depends(require_auth)])
async def get_trade(trade_id: int) -> dict:
    """Return a single trade by ID."""
    from db.models import Trade
    from db.session import get_session

    with get_session() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        return _trade_to_dict(trade)


# ---------------------------------------------------------------------------
# Performance  (protected)
# ---------------------------------------------------------------------------


@app.get("/api/performance", tags=["performance"],
         summary="P&L and performance metrics",
         dependencies=[Depends(require_auth)])
async def performance(
    period: str = "all",
) -> dict:
    """
    Compute performance metrics from closed trades.

    **period**: ``1d``, ``7d``, ``30d``, or ``all``.
    """
    from datetime import timedelta

    from sqlalchemy import select

    from db.models import Trade
    from db.session import get_session

    now = datetime.now(tz=timezone.utc)

    period_map = {"1d": 1, "7d": 7, "30d": 30}
    days = period_map.get(period)

    with get_session() as session:
        q = select(Trade).where(Trade.status == "closed")
        if days is not None:
            cutoff = now - timedelta(days=days)
            q = q.where(Trade.closed_at >= cutoff)
        q = q.order_by(Trade.closed_at)
        rows = session.scalars(q).all()

    if not rows:
        return {
            "period": period,
            "trade_count": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "profit_factor": 0.0,
            "timestamp": now.isoformat(),
        }

    pnls = [r.pnl for r in rows if r.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    return {
        "period": period,
        "trade_count": len(pnls),
        "total_pnl": round(sum(pnls), 2),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "largest_win": round(max(wins), 2) if wins else 0.0,
        "largest_loss": round(min(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "timestamp": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Portfolio  (protected)
# ---------------------------------------------------------------------------


@app.get("/api/portfolio", tags=["portfolio"], summary="Portfolio summary",
         dependencies=[Depends(require_auth)])
async def portfolio() -> dict:
    """Return portfolio summary: open positions, daily P&L, total value."""
    from datetime import date

    from sqlalchemy import func, select

    from db.models import Trade
    from db.session import get_session

    today = date.today()
    day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

    with get_session() as session:
        # Open positions
        open_rows = session.scalars(
            select(Trade)
            .where(Trade.status.in_(["pending", "open", "filled"]))
        ).all()

        # Today's closed P&L
        today_pnl = session.execute(
            select(func.coalesce(func.sum(Trade.pnl), 0.0))
            .where(Trade.status == "closed", Trade.closed_at >= day_start)
        ).scalar() or 0.0

        # Today's trade count
        today_count = session.execute(
            select(func.count(Trade.id))
            .where(Trade.created_at >= day_start)
        ).scalar() or 0

    positions = [
        {
            "symbol": t.symbol,
            "action": t.action,
            "shares": t.shares,
            "entry_price": t.entry_price,
            "fill_price": t.fill_price,
            "target_price": t.target_price,
            "stop_price": t.stop_price,
            "status": t.status,
        }
        for t in open_rows
    ]

    return {
        "open_positions": positions,
        "position_count": len(positions),
        "daily_pnl": round(float(today_pnl), 2),
        "daily_trades": today_count,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Backtesting  (protected)
# ---------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    """Request body for POST /api/backtesting/run."""

    symbol: str = Field(..., max_length=20)
    initial_capital: float = Field(default=100_000, gt=0)
    position_size_pct: float = Field(default=2.0, gt=0, le=100)
    stop_loss_atr: float = Field(default=1.0, gt=0)
    take_profit_atr: float = Field(default=2.0, gt=0)
    ml_min_probability: float = Field(default=0.55, ge=0, le=1)


@app.post("/api/backtesting/run", tags=["backtesting"],
          summary="Run a backtest",
          dependencies=[Depends(require_auth)])
async def run_backtest(body: BacktestRequest) -> dict:
    """
    Run a backtest using the current LightGBM model on historical data
    fetched from IBKR (if connected) or return an error.

    This endpoint runs synchronously and may take several seconds.
    """
    from bot.backtesting.engine import BacktestEngine

    # Try to fetch historical data from the broker
    bars = _fetch_backtest_data(body.symbol)
    if bars is None:
        raise HTTPException(
            status_code=422,
            detail="Cannot fetch historical data. Ensure IBKR is connected "
                   "or provide data via the CLI backtest command.",
        )

    engine = BacktestEngine(
        initial_capital=body.initial_capital,
        position_size_pct=body.position_size_pct,
        stop_loss_atr=body.stop_loss_atr,
        take_profit_atr=body.take_profit_atr,
        ml_min_probability=body.ml_min_probability,
    )
    result = engine.run(bars, symbol=body.symbol.upper())
    return result.to_dict()


def _fetch_backtest_data(symbol: str):
    """Attempt to fetch historical bars via the broker (if available)."""
    try:
        from bot.core.broker import IBKRConnection
        import os

        port_str = os.getenv("IBKR_PORT", "")
        if not port_str:
            return None

        conn = IBKRConnection(port=int(port_str))
        conn.connect()
        try:
            bars = conn.fetch_intraday_bars(symbol, n_bars=1000, bar_size="5 mins")
            return bars
        finally:
            conn.disconnect()
    except Exception:
        return None
