"""
Tests for db/models.py.

Verifies that ORM models can be instantiated, persisted, and queried
using the in-memory SQLite database provided by the db_session fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.models import LogEntry, Setting


NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# LogEntry
# ---------------------------------------------------------------------------


class TestLogEntry:
    def test_insert_and_query(self, db_session):
        entry = LogEntry(
            timestamp=NOW,
            level="INFO",
            category="trading",
            module="executor",
            funcName="place_order",
            lineno=87,
            message="Order placed",
            extra={"order_id": 42, "symbol": "AAPL"},
        )
        db_session.add(entry)
        db_session.flush()

        result = db_session.query(LogEntry).one()
        assert result.level == "INFO"
        assert result.category == "trading"
        assert result.message == "Order placed"
        assert result.extra == {"order_id": 42, "symbol": "AAPL"}
        assert result.lineno == 87

    def test_extra_can_be_none(self, db_session):
        entry = LogEntry(
            timestamp=NOW,
            level="DEBUG",
            category="signals",
            module="generator",
            funcName="run",
            lineno=10,
            message="No extra fields",
            extra=None,
        )
        db_session.add(entry)
        db_session.flush()
        result = db_session.query(LogEntry).one()
        assert result.extra is None

    def test_autoincrement_id(self, db_session):
        for i in range(3):
            db_session.add(LogEntry(
                timestamp=NOW, level="INFO", category="ml",
                module="model", funcName="predict", lineno=i,
                message=f"Entry {i}", extra=None,
            ))
        db_session.flush()
        entries = db_session.query(LogEntry).order_by(LogEntry.id).all()
        ids = [e.id for e in entries]
        assert ids == sorted(ids)
        assert len(set(ids)) == 3  # All unique.

    def test_repr_contains_level_and_category(self, db_session):
        entry = LogEntry(
            timestamp=NOW, level="ERROR", category="risk",
            module="manager", funcName="check", lineno=1,
            message="Limit exceeded", extra=None,
        )
        db_session.add(entry)
        db_session.flush()
        assert "ERROR" in repr(entry)
        assert "risk" in repr(entry)


# ---------------------------------------------------------------------------
# Setting
# ---------------------------------------------------------------------------


class TestSetting:
    def test_insert_and_query(self, db_session):
        setting = Setting(
            key="TRADING_MODE",
            value="dryrun",
            description="Trading mode",
            updated_at=NOW,
        )
        db_session.add(setting)
        db_session.flush()

        result = db_session.query(Setting).one()
        assert result.key == "TRADING_MODE"
        assert result.value == "dryrun"
        assert result.description == "Trading mode"

    def test_primary_key_is_key_column(self, db_session):
        db_session.add(Setting(key="K1", value="v1", description=None, updated_at=NOW))
        db_session.add(Setting(key="K2", value="v2", description=None, updated_at=NOW))
        db_session.flush()
        assert db_session.query(Setting).count() == 2

    def test_duplicate_key_raises(self, db_session):
        db_session.add(Setting(key="DUP", value="a", description=None, updated_at=NOW))
        db_session.flush()
        db_session.add(Setting(key="DUP", value="b", description=None, updated_at=NOW))
        with pytest.raises(Exception):  # IntegrityError (PK violation)
            db_session.flush()

    def test_description_can_be_none(self, db_session):
        setting = Setting(key="NO_DESC", value="val", description=None, updated_at=NOW)
        db_session.add(setting)
        db_session.flush()
        result = db_session.query(Setting).filter_by(key="NO_DESC").one()
        assert result.description is None

    def test_repr_contains_key(self, db_session):
        setting = Setting(key="MY_KEY", value="MY_VAL", description=None, updated_at=NOW)
        db_session.add(setting)
        db_session.flush()
        assert "MY_KEY" in repr(setting)
