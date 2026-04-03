"""
Database session factory for ibkr-ai-trader.

Usage:
    from db.session import get_session

    with get_session() as session:
        session.add(obj)
        session.commit()

The engine is created lazily on first use so that importing this module
at startup does not require the database to be available yet.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_lock = threading.Lock()
_engine = None
_SessionLocal: sessionmaker | None = None


def _build_url() -> str:
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "ibkr_trader")
    user = os.getenv("DB_USER", "ibkr_trader")
    password = os.getenv("DB_PASSWORD", "")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"


def _get_session_factory() -> sessionmaker:
    global _engine, _SessionLocal  # noqa: PLW0603
    if _SessionLocal is None:
        with _lock:
            if _SessionLocal is None:
                _engine = create_engine(
                    _build_url(),
                    # Re-check connections before use to avoid stale connection
                    # errors after MariaDB restarts or connection timeouts.
                    pool_pre_ping=True,
                    # Recycle connections after 1 hour to stay within MariaDB's
                    # wait_timeout (default 8 hours, but recycle earlier to be safe).
                    pool_recycle=3600,
                    # Keep a small pool; the async DB handler uses a single thread.
                    pool_size=5,
                    max_overflow=10,
                )
                _SessionLocal = sessionmaker(bind=_engine, autoflush=False)
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Yield a SQLAlchemy Session with automatic commit/rollback.

    On success the session is committed and closed.
    On exception the session is rolled back and the exception is re-raised.
    """
    factory = _get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
