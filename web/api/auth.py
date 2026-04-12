"""
JWT authentication for the ibkr-ai-trader web API.

Design:
  - Single admin password stored in WEB_PASSWORD (.env).
  - Tokens are signed with SECRET_KEY (.env) using HS256.
  - Token lifetime: 24 hours (not configurable — this is a personal tool).
  - Passwords are compared with hmac.compare_digest to prevent timing attacks.

Endpoints:
  POST /api/auth/login  — exchange password for a JWT Bearer token

Dependency:
  require_auth          — add to any route that needs protection:
                          async def my_route(..., _=Depends(require_auth))
"""

from __future__ import annotations

import collections
import hmac
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from bot.utils.logger import get_logger

log = get_logger("web")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 24
_bearer = HTTPBearer()

# ---------------------------------------------------------------------------
# Rate limiting — sliding window, per source IP
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS = 5       # failed attempts allowed per window
_WINDOW_SECS = 60       # seconds in the sliding window
_rl_lock = threading.Lock()
_failed_attempts: dict[str, collections.deque] = collections.defaultdict(collections.deque)


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP from the request.

    Behind Nginx the direct ``request.client.host`` is always ``127.0.0.1``.
    We trust the ``X-Forwarded-For`` / ``X-Real-IP`` headers that Nginx sets.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # X-Forwarded-For may contain multiple IPs; the first is the client.
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    """Raise HTTP 429 if *ip* has exceeded the failed-login threshold."""
    now = time.monotonic()
    with _rl_lock:
        dq = _failed_attempts[ip]
        while dq and now - dq[0] > _WINDOW_SECS:
            dq.popleft()
        if len(dq) >= _MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again in a minute.",
            )


def _record_failure(ip: str) -> None:
    """Record a failed login attempt for *ip*."""
    with _rl_lock:
        _failed_attempts[ip].append(time.monotonic())


# ---------------------------------------------------------------------------
# Env helpers — read at call time so tests can monkeypatch os.environ
# ---------------------------------------------------------------------------


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "")
    if not key:
        raise RuntimeError("SECRET_KEY is not set in .env")
    return key


def _web_password() -> str:
    pwd = os.getenv("WEB_PASSWORD", "")
    if not pwd:
        raise RuntimeError("WEB_PASSWORD is not set in .env")
    return pwd


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _create_token() -> str:
    exp = datetime.now(tz=timezone.utc) + timedelta(hours=_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": "admin", "exp": exp}, _secret_key(), algorithm=_ALGORITHM)


def _decode_token(token: str) -> dict:
    payload = jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])
    if payload.get("sub") != "admin":
        raise jwt.InvalidTokenError("Invalid token subject")
    return payload


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def require_auth(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    """
    Validate the Bearer JWT on protected routes.

    Raises HTTP 401 if the token is missing, expired, or has an invalid
    signature.  Add to any route handler as ``_: None = Depends(require_auth)``.
    """
    try:
        _decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Login route
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    password: str


@router.post("/login", summary="Obtain a JWT access token")
async def login(body: LoginRequest, request: Request) -> dict:
    """
    Exchange the admin password for a signed JWT.

    Brute-force protected: 5 failed attempts per IP within 60 seconds triggers
    HTTP 429.  The password is compared in constant time to prevent timing
    attacks.  Tokens expire after 24 hours; re-authenticate to obtain a new one.
    """
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip)

    expected = _web_password()
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        _record_failure(client_ip)
        log.warning("Failed login attempt", ip=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_token()
    log.info("Login successful", ip=client_ip)
    return {"access_token": token, "token_type": "bearer"}
