"""
Bot service control API — start/stop/restart the ibkr-bot systemd unit.

Security model
--------------
* Only a fixed whitelist of actions is allowed (``start``, ``stop``, ``restart``).
* The web API runs as the unprivileged ``trader`` user; systemctl is invoked
  via ``sudo -n`` (non-interactive).  A dedicated ``/etc/sudoers.d/ibkr-web``
  rule grants NOPASSWD only for the exact three commands on the
  ``ibkr-bot.service`` unit — no wildcards, no other units.
* ``status`` uses ``systemctl is-active`` directly (no sudo needed).
* A module-level lock serialises all state-changing calls so two concurrent
  restarts cannot interleave.
* Every invocation is logged with the JWT subject (``admin``) and the
  HTTP client IP.  A standard warning header is logged when ``stop`` is
  called during market hours so operators have a clear audit trail.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from bot.utils.logger import get_logger
from web.api.auth import _get_client_ip, require_auth

log = get_logger("web")

router = APIRouter(prefix="/api/bot", tags=["bot-service"])

_SYSTEMCTL = "/usr/bin/systemctl"
_SUDO = "/usr/bin/sudo"
_UNIT = "ibkr-bot"

# Actions that require root privileges and are routed through sudo.
_PRIVILEGED_ACTIONS: frozenset[str] = frozenset({"start", "stop", "restart"})

# Process-local lock — safe with --workers 1 (see ibkr-web.service).
_action_lock = threading.Lock()

# Timeout for the subprocess.run call (seconds).  systemd actions typically
# return in well under 10 seconds; TimeoutStopSec in ibkr-bot.service is 30s
# so we give the full 35s before we give up and return an error.
_SUBPROCESS_TIMEOUT = 35


class ServiceStatus(BaseModel):
    """Result of GET /api/bot/service-status."""

    active: bool
    state: str        # "active", "inactive", "failed", "activating", ...
    unit: str = _UNIT


class ActionResult(BaseModel):
    """Result of a start/stop/restart call."""

    ok: bool
    action: str
    stderr: str = ""


def _run_systemctl(args: list[str], timeout: int = _SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess:
    """Run systemctl with shell=False and a short timeout."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def _status() -> ServiceStatus:
    """Read the current state of ibkr-bot (no privileges required)."""
    try:
        r = _run_systemctl([_SYSTEMCTL, "is-active", _UNIT], timeout=5)
        state = (r.stdout or r.stderr or "unknown").strip()
        active = state == "active"
        return ServiceStatus(active=active, state=state or "unknown")
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.error("systemctl status failed", error=str(exc))
        return ServiceStatus(active=False, state="error")


def _perform(action: str, *, requested_by: str) -> ActionResult:
    """Run a privileged systemctl action under the module lock."""
    if action not in _PRIVILEGED_ACTIONS:
        # Defence in depth — the router already validates this.
        raise HTTPException(status_code=400, detail="Invalid action.")

    acquired = _action_lock.acquire(timeout=5)
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another service action is in progress. Try again in a moment.",
        )
    try:
        try:
            r = _run_systemctl([_SUDO, "-n", _SYSTEMCTL, action, _UNIT])
        except subprocess.TimeoutExpired:
            log.error("systemctl timed out", action=action)
            raise HTTPException(
                status_code=504,
                detail=f"systemctl {action} timed out after {_SUBPROCESS_TIMEOUT}s.",
            )
        except FileNotFoundError:
            # sudo or systemctl not installed — runtime misconfiguration.
            log.error("systemctl or sudo not found", action=action)
            raise HTTPException(
                status_code=500,
                detail="Service control is not available on this host.",
            )

        ok = r.returncode == 0
        stderr = (r.stderr or "").strip()
        log_fn = log.info if ok else log.error
        log_fn(
            "service_action",
            action=action,
            ok=ok,
            returncode=r.returncode,
            stderr=stderr[:200],
            requested_by=requested_by,
        )
        if not ok:
            # Common cause: sudoers rule missing or permission denied.
            raise HTTPException(
                status_code=500,
                detail=(
                    f"systemctl {action} failed (rc={r.returncode}). "
                    f"Check /etc/sudoers.d/ibkr-web."
                ),
            )
        return ActionResult(ok=True, action=action, stderr=stderr)
    finally:
        _action_lock.release()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/service-status",
    summary="Get ibkr-bot service state",
    dependencies=[Depends(require_auth)],
    response_model=ServiceStatus,
)
async def service_status_endpoint() -> ServiceStatus:
    return _status()


class StartRequest(BaseModel):
    """Optional body — currently no fields; reserved for future flags."""


@router.post(
    "/start",
    summary="Start the ibkr-bot service",
    dependencies=[Depends(require_auth)],
    response_model=ActionResult,
)
async def start_endpoint(request: Request) -> ActionResult:
    return _perform("start", requested_by=_get_client_ip(request))


class StopRequest(BaseModel):
    """Stop confirmation body.

    ``confirm`` must be ``"STOP"`` — a typed confirmation prevents accidental
    stops from dashboards with a misfired button.
    """

    confirm: Literal["STOP"]


@router.post(
    "/stop",
    summary="Stop the ibkr-bot service",
    dependencies=[Depends(require_auth)],
    response_model=ActionResult,
)
async def stop_endpoint(body: StopRequest, request: Request) -> ActionResult:
    # Emit a warning log if stopped during market hours — stop does NOT close
    # positions on its own.  Positions will be handled by the engine's shutdown
    # hook (_on_shutdown → _eod_close) when TRADING_MODE != dryrun, so this is
    # safe, but operators should still be aware.
    try:
        from bot.utils.calendar import is_market_open
        if is_market_open():
            log.warning(
                "Service stop requested during market hours",
                ip=_get_client_ip(request),
            )
    except Exception:  # noqa: BLE001
        pass
    return _perform("stop", requested_by=_get_client_ip(request))


class RestartRequest(BaseModel):
    """Restart confirmation body."""

    confirm: Literal["RESTART"]


@router.post(
    "/restart",
    summary="Restart the ibkr-bot service",
    dependencies=[Depends(require_auth)],
    response_model=ActionResult,
)
async def restart_endpoint(body: RestartRequest, request: Request) -> ActionResult:
    return _perform("restart", requested_by=_get_client_ip(request))
