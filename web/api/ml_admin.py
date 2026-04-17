"""
ML admin API — retrain, rollback, and inspect the LightGBM model from the GUI.

Security model
--------------
* All endpoints require a valid Bearer JWT (``require_auth``).
* A module-level lock guarantees that at most one training job runs at a
  time.  A second POST /retrain while one is in progress returns 409.
* Every admin action creates an ``MlJob`` row so the UI can poll it and
  we have an audit trail of who-did-what-and-when.
* Rollback runs synchronously (it just rewrites a JSON manifest) but still
  creates a job row so the history is complete.
* Retrain runs on a background thread so the request returns immediately
  with a job_id.  The UI polls ``GET /api/ml/jobs/{id}`` for progress.

Data sourcing
-------------
Training requires a large amount of 5-min OHLCV data.  We fetch it from
IBKR using the same ``IBKRConnection`` the engine uses.  When IBKR is not
reachable the job is marked ``failed`` with an explanatory error — we
deliberately do not fall back to synthetic or cached data because a
silently-trained bad model is worse than no retraining at all.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from bot.ml.versioning import get_current_version, list_versions, rollback
from bot.utils.logger import get_logger
from db.models import MlJob
from db.session import get_session
from web.api.auth import _get_client_ip, require_auth

log = get_logger("ml")

router = APIRouter(prefix="/api/ml", tags=["ml-admin"])

# Only one retrain can run at a time — the trainer pins CPU cores and the
# model manifest is a single JSON file that cannot be written concurrently.
_train_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RetrainRequest(BaseModel):
    """
    POST /api/ml/retrain body.

    ``symbol`` and ``n_bars`` select the historical 5-min data fetched from
    IBKR.  The three threshold fields match ``bot.ml.trainer.train``'s
    signature and are preserved in the job record for reproducibility.
    """

    symbol: str = Field(..., min_length=1, max_length=20)
    n_bars: int = Field(default=5000, ge=500, le=20_000)
    forward_bars: int = Field(default=6, ge=1, le=50)
    long_threshold_pct: float = Field(default=0.3, gt=0, le=10)
    short_threshold_pct: float = Field(default=0.3, gt=0, le=10)


class RollbackRequest(BaseModel):
    """POST /api/ml/rollback body."""

    version: str = Field(..., min_length=1, max_length=40)


class JobCreated(BaseModel):
    job_id: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job_to_dict(j: MlJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "status": j.status,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
        "version": j.version,
        "metrics": j.metrics,
        "error": j.error,
        "params": j.params,
        "requested_by": j.requested_by,
    }


def _create_job(
    job_type: str,
    params: dict[str, Any],
    requested_by: str,
) -> int:
    """Insert a new ``MlJob`` row in ``pending`` state and return its id."""
    now = datetime.now(tz=timezone.utc)
    with get_session() as session:
        job = MlJob(
            job_type=job_type,
            status="pending",
            started_at=now,
            params=params,
            requested_by=requested_by,
        )
        session.add(job)
        session.flush()  # assign j.id
        job_id = job.id
    return job_id


def _finish_job(
    job_id: int,
    *,
    status_value: str,
    version: str | None = None,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Update the job row to a terminal state."""
    with get_session() as session:
        job = session.get(MlJob, job_id)
        if job is None:  # pragma: no cover — shouldn't happen
            return
        job.status = status_value
        job.finished_at = datetime.now(tz=timezone.utc)
        if version is not None:
            job.version = version
        if metrics is not None:
            job.metrics = metrics
        if error is not None:
            job.error = error[:2000]  # truncate to fit TEXT column sanely


def _fetch_training_bars(symbol: str, n_bars: int):
    """Fetch 5-min OHLCV from IBKR; return None when unavailable."""
    import os

    try:
        from bot.core.broker import IBKRConnection
    except Exception:  # noqa: BLE001
        return None

    port_str = os.getenv("IBKR_PORT", "")
    if not port_str:
        return None

    try:
        conn = IBKRConnection(port=int(port_str))
        conn.connect()
        try:
            return conn.fetch_intraday_bars(symbol, n_bars=n_bars, bar_size="5 mins")
        finally:
            conn.disconnect()
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to fetch training bars", symbol=symbol, error=str(exc))
        return None


def _mark_running(job_id: int) -> None:
    with get_session() as session:
        job = session.get(MlJob, job_id)
        if job is not None:
            job.status = "running"


def _run_retrain_job(job_id: int, req: RetrainRequest) -> None:
    """Thread target — executes the retrain and records the result."""
    # Serialise trainings across the whole process.  If another retrain is
    # already running, mark this one failed immediately so the user sees a
    # clear message.  In practice the POST handler already rejects concurrent
    # requests with 409, so this is defence-in-depth.
    acquired = _train_lock.acquire(blocking=False)
    if not acquired:
        _finish_job(
            job_id,
            status_value="failed",
            error="Another retrain is already running.",
        )
        return
    try:
        _mark_running(job_id)
        log.info("Retrain job started", job_id=job_id, symbol=req.symbol)

        bars = _fetch_training_bars(req.symbol, req.n_bars)
        if bars is None or len(bars) < 500:
            msg = (
                f"Could not fetch enough training data for {req.symbol}. "
                "Ensure IBKR is connected and the symbol has history."
            )
            _finish_job(job_id, status_value="failed", error=msg)
            log.warning("Retrain aborted", job_id=job_id, reason=msg)
            return

        from bot.ml import trainer  # local import keeps FastAPI start-up fast

        try:
            version = trainer.train(
                bars,
                forward_bars=req.forward_bars,
                long_threshold_pct=req.long_threshold_pct,
                short_threshold_pct=req.short_threshold_pct,
            )
        except Exception as exc:  # noqa: BLE001
            _finish_job(job_id, status_value="failed", error=str(exc))
            log.error("Retrain failed", job_id=job_id, error=str(exc))
            return

        # Pull the just-registered metrics back from the version manifest.
        metrics: dict[str, Any] = {}
        for v in list_versions():
            if v.get("version") == version:
                metrics = dict(v.get("metrics") or {})
                break

        _finish_job(
            job_id,
            status_value="done",
            version=version,
            metrics=metrics,
        )
        log.info("Retrain job complete", job_id=job_id, version=version)

        # Swap the running process's model singleton in-place so the next
        # prediction uses the new model without requiring a service restart.
        try:
            from bot.ml.model import reload_model
            reload_model()
        except Exception as exc:  # noqa: BLE001
            log.warning("reload_model failed", error=str(exc))
    finally:
        _train_lock.release()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/versions",
    summary="List all registered model versions (newest first)",
    dependencies=[Depends(require_auth)],
)
async def list_model_versions() -> list[dict[str, Any]]:
    current = get_current_version()
    versions = list_versions()
    return [{**v, "is_current": v.get("version") == current} for v in versions]


@router.get(
    "/current",
    summary="Return the currently active model version",
    dependencies=[Depends(require_auth)],
)
async def current_model_version() -> dict[str, Any]:
    return {"version": get_current_version()}


@router.post(
    "/retrain",
    summary="Kick off a retrain job",
    dependencies=[Depends(require_auth)],
    response_model=JobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retrain_endpoint(body: RetrainRequest, request: Request) -> JobCreated:
    if _train_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A retrain is already in progress. Try again when it finishes.",
        )
    requested_by = _get_client_ip(request)
    job_id = _create_job(
        "retrain",
        {
            "symbol": body.symbol.upper(),
            "n_bars": body.n_bars,
            "forward_bars": body.forward_bars,
            "long_threshold_pct": body.long_threshold_pct,
            "short_threshold_pct": body.short_threshold_pct,
        },
        requested_by,
    )
    # Daemon=True: the thread dies with the process.  We record terminal
    # state from inside the target so abrupt shutdowns still leave a trace.
    t = threading.Thread(
        target=_run_retrain_job,
        args=(job_id, body),
        name=f"ml-retrain-{job_id}",
        daemon=True,
    )
    t.start()
    log.info("Retrain job queued", job_id=job_id, ip=requested_by)
    return JobCreated(job_id=job_id)


@router.post(
    "/rollback",
    summary="Roll back to a previous model version",
    dependencies=[Depends(require_auth)],
    response_model=JobCreated,
)
async def rollback_endpoint(body: RollbackRequest, request: Request) -> JobCreated:
    requested_by = _get_client_ip(request)
    job_id = _create_job("rollback", {"version": body.version}, requested_by)
    _mark_running(job_id)
    try:
        rollback(body.version)
    except ValueError as exc:
        _finish_job(job_id, status_value="failed", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        _finish_job(job_id, status_value="failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Rollback failed.")
    _finish_job(job_id, status_value="done", version=body.version)
    log.info("Rollback complete", job_id=job_id, version=body.version, ip=requested_by)

    # Reload so the running process picks up the rolled-back model.
    try:
        from bot.ml.model import reload_model
        reload_model()
    except Exception as exc:  # noqa: BLE001
        log.warning("reload_model failed after rollback", error=str(exc))

    return JobCreated(job_id=job_id)


@router.get(
    "/jobs/{job_id}",
    summary="Get the status of a single ML job",
    dependencies=[Depends(require_auth)],
)
async def get_job(job_id: int) -> dict[str, Any]:
    with get_session() as session:
        job = session.get(MlJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_to_dict(job)


@router.get(
    "/jobs",
    summary="List recent ML jobs (newest first)",
    dependencies=[Depends(require_auth)],
)
async def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    with get_session() as session:
        rows = session.scalars(
            select(MlJob).order_by(desc(MlJob.id)).limit(limit)
        ).all()
    return [_job_to_dict(r) for r in rows]
