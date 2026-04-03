"""
Tests for db/seed.py.

Verifies that the seed script inserts the correct defaults and is
idempotent.  Uses the SQLite in-memory database — no MariaDB required.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from db.models import Setting
from db.seed import DEFAULTS, seed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_session(db_session):
    """Run seed() against the test DB and return the session."""
    @contextmanager
    def _fake_get_session():
        yield db_session
        db_session.flush()

    with patch("db.seed.get_session", side_effect=_fake_get_session):
        seed()

    return db_session


# ---------------------------------------------------------------------------
# DEFAULTS list integrity
# ---------------------------------------------------------------------------


class TestDefaultsList:
    def test_all_entries_are_three_tuples(self):
        for entry in DEFAULTS:
            assert len(entry) == 3, f"Entry {entry!r} is not a 3-tuple"

    def test_all_keys_are_non_empty_strings(self):
        for key, _, _ in DEFAULTS:
            assert isinstance(key, str) and key.strip(), f"Bad key: {key!r}"

    def test_all_values_are_strings(self):
        for _, value, _ in DEFAULTS:
            assert isinstance(value, str), f"Non-string value: {value!r}"

    def test_no_duplicate_keys(self):
        keys = [key for key, _, _ in DEFAULTS]
        assert len(keys) == len(set(keys)), "Duplicate keys found in DEFAULTS"

    def test_required_settings_present(self):
        keys = {key for key, _, _ in DEFAULTS}
        required = {
            "TRADING_MODE",
            "EOD_CLOSE_MINUTES",
            "CIRCUIT_BREAKER_DAILY_LOSS_PCT",
            "POSITION_SIZING_METHOD",
            "UNIVERSE_MAX_SYMBOLS",
        }
        missing = required - keys
        assert not missing, f"Required settings missing from DEFAULTS: {missing}"

    def test_trading_mode_default_is_dryrun(self):
        """The safe default for TRADING_MODE must always be 'dryrun'."""
        defaults_map = {k: v for k, v, _ in DEFAULTS}
        assert defaults_map["TRADING_MODE"] == "dryrun"


# ---------------------------------------------------------------------------
# seed() behaviour
# ---------------------------------------------------------------------------


class TestSeed:
    def test_inserts_all_defaults(self, seeded_session):
        count = seeded_session.query(Setting).count()
        assert count == len(DEFAULTS)

    def test_inserted_keys_match_defaults(self, seeded_session):
        expected_keys = {key for key, _, _ in DEFAULTS}
        actual_keys = {row.key for row in seeded_session.query(Setting).all()}
        assert actual_keys == expected_keys

    def test_is_idempotent(self, db_session):
        """Running seed() twice must not insert duplicates."""
        @contextmanager
        def _fake_get_session():
            yield db_session
            db_session.flush()

        with patch("db.seed.get_session", side_effect=_fake_get_session):
            seed()
            seed()  # Second run.

        count = db_session.query(Setting).count()
        assert count == len(DEFAULTS)

    def test_does_not_overwrite_existing_value(self, db_session):
        """A manually changed setting must survive a seed() re-run."""
        now = datetime.now(tz=timezone.utc)
        db_session.add(Setting(
            key="TRADING_MODE", value="live",
            description="Overridden", updated_at=now,
        ))
        db_session.flush()

        @contextmanager
        def _fake_get_session():
            yield db_session
            db_session.flush()

        with patch("db.seed.get_session", side_effect=_fake_get_session):
            seed()

        result = db_session.query(Setting).filter_by(key="TRADING_MODE").one()
        assert result.value == "live"  # Must not be reset to "dryrun".
