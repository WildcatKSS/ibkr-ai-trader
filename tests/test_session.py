"""
Tests for db/session.py.

Verifies URL construction and the get_session() context manager.
Uses SQLite instead of MariaDB — no real database connection is made.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL

import db.session as session_module
from db.models import Base


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def _build(self, env: dict[str, str]) -> URL:
        """Call _build_url() with a controlled environment."""
        with patch.dict("os.environ", env, clear=False):
            # Reset cached engine so _build_url is called fresh.
            session_module._engine = None
            session_module._SessionLocal = None
            return session_module._build_url()

    def test_default_values(self):
        env = {
            "DB_HOST": "localhost", "DB_PORT": "3306",
            "DB_NAME": "ibkr_trader", "DB_USER": "ibkr_trader",
            "DB_PASSWORD": "",
        }
        url = self._build(env)
        assert url.host == "localhost"
        assert url.port == 3306
        assert url.database == "ibkr_trader"
        assert url.username == "ibkr_trader"

    def test_custom_values(self):
        env = {
            "DB_HOST": "db.example.com", "DB_PORT": "3307",
            "DB_NAME": "mydb", "DB_USER": "myuser",
            "DB_PASSWORD": "secret",
        }
        url = self._build(env)
        assert url.host == "db.example.com"
        assert url.port == 3307
        assert url.database == "mydb"
        assert url.username == "myuser"

    def test_password_with_special_characters_is_encoded(self):
        """Special chars in the password must not break the URL."""
        env = {
            "DB_HOST": "localhost", "DB_PORT": "3306",
            "DB_NAME": "db", "DB_USER": "user",
            "DB_PASSWORD": "p@ss:w/ord%21",
        }
        url = self._build(env)
        rendered = url.render_as_string(hide_password=False)
        # The literal special chars must not appear unencoded in the authority.
        authority = rendered.split("@")[0]  # everything before @host
        assert "p@ss" not in authority  # '@' in password must be encoded
        # But the full URL must still round-trip correctly.
        assert url.password == "p@ss:w/ord%21"

    def test_returns_sqlalchemy_url_object(self):
        env = {
            "DB_HOST": "localhost", "DB_PORT": "3306",
            "DB_NAME": "db", "DB_USER": "u", "DB_PASSWORD": "",
        }
        url = self._build(env)
        assert isinstance(url, URL)

    def test_drivername_is_pymysql(self):
        env = {
            "DB_HOST": "localhost", "DB_PORT": "3306",
            "DB_NAME": "db", "DB_USER": "u", "DB_PASSWORD": "",
        }
        url = self._build(env)
        assert url.drivername == "mysql+pymysql"


# ---------------------------------------------------------------------------
# get_session() context manager
# ---------------------------------------------------------------------------


class TestGetSession:
    @pytest.fixture(autouse=True)
    def sqlite_engine(self, db_engine):
        """
        Override the module-level engine/factory with the SQLite test engine.
        Restored after each test.
        """
        from sqlalchemy.orm import sessionmaker as SM

        original_engine = session_module._engine
        original_factory = session_module._SessionLocal

        session_module._engine = db_engine
        session_module._SessionLocal = SM(bind=db_engine, autoflush=False)

        yield

        session_module._engine = original_engine
        session_module._SessionLocal = original_factory

    def test_yields_session(self):
        from db.session import get_session
        from sqlalchemy.orm import Session

        with get_session() as session:
            assert isinstance(session, Session)

    def test_commits_on_success(self):
        from db.session import get_session
        from db.models import Setting
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        with get_session() as session:
            session.add(Setting(key="TEST_COMMIT", value="1",
                                description=None, updated_at=now))

        # Open a new session to verify the commit was persisted.
        with get_session() as session:
            result = session.query(Setting).filter_by(key="TEST_COMMIT").one_or_none()
            assert result is not None
            assert result.value == "1"

    def test_rolls_back_on_exception(self):
        from db.session import get_session
        from db.models import Setting
        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)
        with pytest.raises(RuntimeError):
            with get_session() as session:
                session.add(Setting(key="ROLLBACK_TEST", value="x",
                                    description=None, updated_at=now))
                raise RuntimeError("Simulated error")

        # The insert must have been rolled back.
        with get_session() as session:
            result = session.query(Setting).filter_by(key="ROLLBACK_TEST").one_or_none()
            assert result is None
