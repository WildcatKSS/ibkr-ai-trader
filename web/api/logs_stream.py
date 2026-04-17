"""
Server-Sent Events (SSE) log stream.

Why a separate stream-token flow
--------------------------------
The W3C ``EventSource`` API cannot attach an ``Authorization`` header — the
browser only sends cookies, basic auth, or URL query parameters.  Sending the
long-lived JWT as a query parameter is unsafe: Nginx access logs, browser
history, and Referer headers would all capture the token.

We therefore issue a short-lived, single-use **stream token** over the regular
Bearer-authenticated POST endpoint.  The browser exchanges its JWT for the
stream token, then opens the SSE connection with the stream token in the
query string:

    1. POST /api/logs/stream-token   (Authorization: Bearer <JWT>)   → stream_token
    2. GET  /api/logs/stream?stream_token=<stream_token>              → SSE stream

Properties
----------
* ``stream_token`` is a cryptographically random 32-byte URL-safe string.
* Each token is bound to a single source IP and expires after 60 seconds.
* Tokens are consumed on first use: opening the stream removes the token
  from the in-memory store; a second GET with the same token fails with 401.
* Tokens are stored in process-local memory only — never persisted.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select

from bot.utils.logger import VALID_CATEGORIES, get_logger
from db.models import LogEntry
from db.session import get_session
from web.api.auth import _get_client_ip, require_auth

log = get_logger("web")

router = APIRouter(prefix="/api/logs", tags=["logs"])

_VALID_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

_TOKEN_TTL_SECONDS = 60
_TOKEN_MAX_ENTRIES = 500  # cap memory; older tokens are evicted first


@dataclass
class _Token:
    token: str
    ip: str
    created_at: float


_tokens_lock = threading.Lock()
_tokens: dict[str, _Token] = {}


def _purge_expired(now: float) -> None:
    """Remove expired tokens (caller must hold _tokens_lock)."""
    expired = [k for k, t in _tokens.items() if now - t.created_at > _TOKEN_TTL_SECONDS]
    for k in expired:
        _tokens.pop(k, None)


def _issue_token(ip: str) -> str:
    """Generate a new single-use stream token bound to *ip*."""
    now = time.monotonic()
    with _tokens_lock:
        _purge_expired(now)
        # Cap memory use — evict the oldest token if we're at the limit.
        if len(_tokens) >= _TOKEN_MAX_ENTRIES:
            oldest = min(_tokens.values(), key=lambda t: t.created_at)
            _tokens.pop(oldest.token, None)
        token = secrets.token_urlsafe(32)
        _tokens[token] = _Token(token=token, ip=ip, created_at=now)
    return token


def _consume_token(token: str, ip: str) -> bool:
    """Validate and remove *token*.  Returns True when the token is valid."""
    now = time.monotonic()
    with _tokens_lock:
        _purge_expired(now)
        entry = _tokens.pop(token, None)
    if entry is None:
        return False
    if entry.ip != ip:
        # IP mismatch — do not reuse a different entry; treat as invalid.
        return False
    if now - entry.created_at > _TOKEN_TTL_SECONDS:
        return False
    return True


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/stream-token",
    summary="Issue a single-use SSE stream token",
    dependencies=[Depends(require_auth)],
)
async def stream_token_endpoint(request: Request) -> dict:
    ip = _get_client_ip(request)
    token = _issue_token(ip)
    return {"stream_token": token, "expires_in": _TOKEN_TTL_SECONDS}


async def _log_event_generator(
    category: str | None,
    level: str | None,
    since_id: int,
    disconnect_check,
):
    """Yield SSE-formatted events for new log entries.

    Polls the database once per second and emits any rows with id > last_id.
    Sends a heartbeat comment every 15 seconds to keep idle connections alive
    across any proxies that close silent sockets.
    """
    last_id = since_id
    last_heartbeat = time.monotonic()

    # Initial sync: grab the most recent N rows so the client sees context
    # (otherwise an idle bot means an empty viewer).
    try:
        with get_session() as session:
            q = select(LogEntry).order_by(desc(LogEntry.id)).limit(50)
            if category:
                q = q.where(LogEntry.category == category)
            if level:
                q = q.where(LogEntry.level == level.upper())
            rows = list(reversed(session.scalars(q).all()))
        for r in rows:
            last_id = max(last_id, r.id)
            yield _format_event(r)
    except Exception as exc:  # noqa: BLE001
        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    while True:
        if await disconnect_check():
            break

        try:
            with get_session() as session:
                q = (
                    select(LogEntry)
                    .where(LogEntry.id > last_id)
                    .order_by(LogEntry.id)
                    .limit(200)
                )
                if category:
                    q = q.where(LogEntry.category == category)
                if level:
                    q = q.where(LogEntry.level == level.upper())
                rows = session.scalars(q).all()

            for r in rows:
                last_id = r.id
                yield _format_event(r)
        except Exception as exc:  # noqa: BLE001
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

        now = time.monotonic()
        if now - last_heartbeat > 15:
            yield ": heartbeat\n\n"
            last_heartbeat = now

        await asyncio.sleep(1.0)


def _format_event(r: LogEntry) -> str:
    payload = {
        "id": r.id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "level": r.level,
        "category": r.category,
        "module": r.module,
        "message": r.message,
        "extra": r.extra,
    }
    return f"id: {r.id}\ndata: {json.dumps(payload)}\n\n"


@router.get(
    "/stream",
    summary="Server-Sent Events log stream",
    # Authentication is handled by the stream_token query parameter, NOT the
    # standard Bearer dependency — EventSource cannot send headers.  Do not
    # add Depends(require_auth) here or the endpoint becomes unreachable from
    # the browser.
)
async def stream_endpoint(
    request: Request,
    stream_token: str = Query(..., min_length=16, max_length=256),
    category: str | None = Query(default=None, max_length=50),
    level: str | None = Query(default=None, max_length=20),
    since_id: int = Query(default=0, ge=0),
):
    ip = _get_client_ip(request)
    if not _consume_token(stream_token, ip):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired stream token.",
        )
    if category is not None and category not in VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid category. Valid: {', '.join(sorted(VALID_CATEGORIES))}",
        )
    if level is not None and level.upper() not in _VALID_LEVELS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid level. Valid: {', '.join(sorted(_VALID_LEVELS))}",
        )

    async def disconnect_check() -> bool:
        return await request.is_disconnected()

    generator = _log_event_generator(category, level, since_id, disconnect_check)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            # Disable proxy-level buffering on Nginx for this endpoint.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
        },
    )
