"""
Shared test fixtures for ibkr-ai-trader.

Rules (from CLAUDE.md):
- Tests must never connect to real IBKR or call the real Claude API.
- Tests must never depend on .env values.
- All database tests use an in-memory SQLite engine — no MariaDB required.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_engine():
    """
    In-memory SQLite engine with the full schema applied.

    SQLite is schema-compatible with our models for testing purposes.
    Each test gets a fresh database; the engine is disposed afterwards.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """
    SQLAlchemy Session bound to the in-memory SQLite engine.

    The session is rolled back after each test so tests are isolated.
    """
    Session = sessionmaker(bind=db_engine, autoflush=False)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def patch_get_session(db_session):
    """
    Patch db.session.get_session to yield the test SQLite session.

    Use this in any test that calls code which internally calls get_session().
    The test session is already bound to the in-memory DB, so no real
    MariaDB connection is made.
    """
    @contextmanager
    def _fake_get_session():
        yield db_session
        db_session.flush()

    with patch("db.session.get_session", side_effect=_fake_get_session):
        yield db_session


# ---------------------------------------------------------------------------
# Logging fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_log_dir(tmp_path, monkeypatch):
    """
    Redirect all logger file output to a temporary directory.

    Patches bot.utils.logger.LOG_DIR so no files are written to the
    real logs/ directory during tests.  The logger module is reloaded
    so the patched value takes effect for fresh get_logger() calls.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("LOG_DIR", str(log_dir))

    import bot.utils.logger as logger_module
    monkeypatch.setattr(logger_module, "LOG_DIR", log_dir)

    yield log_dir


# ---------------------------------------------------------------------------
# Config cache fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_config_cache():
    """
    Reset the config module's in-memory cache before every test.

    config.py uses module-level globals (_cache, _loaded_at) that would
    carry state between tests without this reset.
    """
    import bot.utils.config as cfg
    cfg._cache = {}
    cfg._loaded_at = 0.0
    yield
    cfg._cache = {}
    cfg._loaded_at = 0.0
