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

import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from bot.utils.logger import get_logger

log = get_logger("web")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 24
_bearer = HTTPBearer()


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
    return jwt.encode({"exp": exp}, _secret_key(), algorithm=_ALGORITHM)


def _decode_token(token: str) -> dict:
    return jwt.decode(token, _secret_key(), algorithms=[_ALGORITHM])


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
async def login(body: LoginRequest) -> dict:
    """
    Exchange the admin password for a signed JWT.

    The password is compared in constant time to prevent timing attacks.
    Tokens expire after 24 hours; re-authenticate to obtain a new one.
    """
    expected = _web_password()
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        log.warning("Failed login attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = _create_token()
    log.info("Login successful")
    return {"access_token": token, "token_type": "bearer"}
