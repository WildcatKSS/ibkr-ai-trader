"""
Universe approval API.

The engine writes a ``UniverseSelection`` row on each daily scan when
``UNIVERSE_APPROVAL_MODE = "approval"``.  The user then approves a single
symbol (or rejects the scan entirely) via the web UI; the engine reads the
approved row on its next tick and trades that symbol.

Endpoints
---------
``GET  /api/universe/pending``    — most recent row awaiting approval.
``GET  /api/universe/history``    — last N scans with decisions.
``POST /api/universe/approve``    — record the user's choice.
``POST /api/universe/reject``     — mark the day's scan as rejected.
``POST /api/universe/scan-now``   — (optional) trigger a rescan hint.

Security
--------
* All endpoints require a valid JWT.
* Approvals are idempotent at the row level: once a row's status transitions
  out of ``pending_approval`` it cannot be changed again (state machine).
* The chosen symbol must belong to the row's candidate list — you cannot
  approve an arbitrary ticker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from bot.utils.logger import get_logger
from db.models import UniverseSelection
from db.session import get_session
from web.api.auth import _get_client_ip, require_auth

log = get_logger("universe")

router = APIRouter(prefix="/api/universe", tags=["universe"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    selection_id: int = Field(..., ge=1)
    symbol: str = Field(..., min_length=1, max_length=20)


class RejectRequest(BaseModel):
    selection_id: int = Field(..., ge=1)
    reason: str = Field(default="", max_length=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selection_to_dict(s: UniverseSelection) -> dict[str, Any]:
    return {
        "id": s.id,
        "scan_date": s.scan_date.isoformat() if s.scan_date else None,
        "candidates": s.candidates or [],
        "selected_symbol": s.selected_symbol,
        "status": s.status,
        "reasoning": s.reasoning,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "decided_at": s.decided_at.isoformat() if s.decided_at else None,
        "decided_by": s.decided_by,
    }


def _candidate_symbols(row: UniverseSelection) -> set[str]:
    out: set[str] = set()
    for c in row.candidates or []:
        if isinstance(c, dict) and isinstance(c.get("symbol"), str):
            out.add(c["symbol"].upper())
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/pending",
    summary="Most recent scan awaiting approval",
    dependencies=[Depends(require_auth)],
)
async def get_pending() -> dict[str, Any] | None:
    with get_session() as session:
        row = session.scalars(
            select(UniverseSelection)
            .where(UniverseSelection.status == "pending_approval")
            .order_by(desc(UniverseSelection.scan_date))
            .limit(1)
        ).first()
        if row is None:
            return None
        return _selection_to_dict(row)


@router.get(
    "/history",
    summary="Recent universe selections (newest first)",
    dependencies=[Depends(require_auth)],
)
async def get_history(limit: int = 30) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with get_session() as session:
        rows = session.scalars(
            select(UniverseSelection)
            .order_by(desc(UniverseSelection.scan_date))
            .limit(limit)
        ).all()
    return [_selection_to_dict(r) for r in rows]


@router.post(
    "/approve",
    summary="Approve a scan with a chosen symbol",
    dependencies=[Depends(require_auth)],
)
async def approve_selection(body: ApproveRequest, request: Request) -> dict[str, Any]:
    ip = _get_client_ip(request)
    symbol = body.symbol.upper().strip()

    with get_session() as session:
        row = session.get(UniverseSelection, body.selection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Selection not found")
        if row.status != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Selection is already '{row.status}'.",
            )
        if symbol not in _candidate_symbols(row):
            raise HTTPException(
                status_code=422,
                detail=f"Symbol '{symbol}' is not in the candidate list.",
            )
        row.selected_symbol = symbol
        row.status = "approved"
        row.decided_at = datetime.now(tz=timezone.utc)
        row.decided_by = ip
        out = _selection_to_dict(row)

    log.info("Universe selection approved", id=body.selection_id, symbol=symbol, by=ip)
    return out


@router.post(
    "/reject",
    summary="Reject the scan (no trading today)",
    dependencies=[Depends(require_auth)],
)
async def reject_selection(body: RejectRequest, request: Request) -> dict[str, Any]:
    ip = _get_client_ip(request)

    with get_session() as session:
        row = session.get(UniverseSelection, body.selection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Selection not found")
        if row.status != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Selection is already '{row.status}'.",
            )
        row.status = "rejected"
        row.decided_at = datetime.now(tz=timezone.utc)
        row.decided_by = ip
        if body.reason:
            existing = row.reasoning or ""
            row.reasoning = (existing + "\n\nRejection reason: " + body.reason).strip()
        out = _selection_to_dict(row)

    log.info("Universe selection rejected", id=body.selection_id, by=ip)
    return out


@router.post(
    "/scan-now",
    summary="Hint the engine to rescan on the next tick",
    dependencies=[Depends(require_auth)],
)
async def scan_now() -> dict[str, Any]:
    """
    Touch the ``UNIVERSE_SCAN_REQUESTED`` flag so the engine's next tick
    treats today as un-scanned.  The engine checks this flag in
    ``_scan_universe``.
    """
    from bot.utils.config import reload
    from db.models import Setting

    now = datetime.now(tz=timezone.utc)
    with get_session() as session:
        obj = session.get(Setting, "UNIVERSE_SCAN_REQUESTED")
        if obj is None:
            obj = Setting(
                key="UNIVERSE_SCAN_REQUESTED",
                value=now.isoformat(),
                description="Internal flag — requests a universe rescan on the next tick.",
                updated_at=now,
            )
            session.add(obj)
        else:
            obj.value = now.isoformat()
            obj.updated_at = now
    reload()
    return {"ok": True, "requested_at": now.isoformat()}
