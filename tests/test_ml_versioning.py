"""
Tests for bot/ml/versioning.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.ml.versioning import (
    get_current_version,
    get_model_path,
    list_versions,
    make_version_string,
    register_version,
    rollback,
)


# ---------------------------------------------------------------------------
# Helpers — redirect MODELS_DIR to a tmp dir for every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_models_dir(tmp_path, monkeypatch):
    """Redirect all versioning operations to a temporary directory."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    monkeypatch.setattr("bot.ml.versioning._MODELS_DIR", models_dir)
    monkeypatch.setattr("bot.ml.versioning._MANIFEST", models_dir / "version.json")
    return models_dir


def _fake_model(models_dir: Path, version: str) -> Path:
    """Create a dummy .lgbm file so get_model_path returns a real path."""
    p = models_dir / f"model_{version}.lgbm"
    p.write_bytes(b"fake")
    return p


# ---------------------------------------------------------------------------
# TestGetCurrentVersion
# ---------------------------------------------------------------------------


class TestGetCurrentVersion:
    def test_returns_none_when_no_manifest(self):
        assert get_current_version() is None

    def test_returns_registered_current(self, tmp_models_dir):
        _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=100, metrics={})
        assert get_current_version() == "v001"


# ---------------------------------------------------------------------------
# TestRegisterVersion
# ---------------------------------------------------------------------------


class TestRegisterVersion:
    def test_registers_version(self, tmp_models_dir):
        _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=500, metrics={"val_accuracy": 0.6})
        versions = list_versions()
        assert len(versions) == 1
        assert versions[0]["version"] == "v001"
        assert versions[0]["n_samples"] == 500

    def test_sets_current(self, tmp_models_dir):
        _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=100, metrics={}, set_current=True)
        assert get_current_version() == "v001"

    def test_set_current_false(self, tmp_models_dir):
        _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=100, metrics={}, set_current=False)
        assert get_current_version() is None

    def test_duplicate_version_replaced(self, tmp_models_dir):
        _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=100, metrics={})
        register_version("v001", n_samples=200, metrics={})
        versions = list_versions()
        assert len(versions) == 1
        assert versions[0]["n_samples"] == 200

    def test_multiple_versions_stored(self, tmp_models_dir):
        for v in ("v001", "v002", "v003"):
            _fake_model(tmp_models_dir, v)
            register_version(v, n_samples=100, metrics={})
        assert len(list_versions()) == 3


# ---------------------------------------------------------------------------
# TestListVersions
# ---------------------------------------------------------------------------


class TestListVersions:
    def test_empty_when_no_manifest(self):
        assert list_versions() == []

    def test_newest_first(self, tmp_models_dir):
        for v in ("v001", "v002", "v003"):
            _fake_model(tmp_models_dir, v)
            register_version(v, n_samples=100, metrics={})
        versions = list_versions()
        assert versions[0]["version"] == "v003"
        assert versions[-1]["version"] == "v001"


# ---------------------------------------------------------------------------
# TestGetModelPath
# ---------------------------------------------------------------------------


class TestGetModelPath:
    def test_returns_none_when_no_current(self):
        assert get_model_path() is None

    def test_returns_path_for_existing_file(self, tmp_models_dir):
        p = _fake_model(tmp_models_dir, "v001")
        register_version("v001", n_samples=100, metrics={})
        assert get_model_path() == p

    def test_returns_none_when_file_missing(self, tmp_models_dir):
        register_version("v001", n_samples=100, metrics={})
        # No actual .lgbm file created
        assert get_model_path() is None

    def test_explicit_version(self, tmp_models_dir):
        p = _fake_model(tmp_models_dir, "v002")
        register_version("v001", n_samples=100, metrics={})
        register_version("v002", n_samples=100, metrics={})
        assert get_model_path("v002") == p


# ---------------------------------------------------------------------------
# TestRollback
# ---------------------------------------------------------------------------


class TestRollback:
    def test_rolls_back_to_earlier_version(self, tmp_models_dir):
        for v in ("v001", "v002"):
            _fake_model(tmp_models_dir, v)
            register_version(v, n_samples=100, metrics={})
        assert get_current_version() == "v002"
        rollback("v001")
        assert get_current_version() == "v001"

    def test_raises_for_unknown_version(self):
        with pytest.raises(ValueError, match="Unknown version"):
            rollback("vXXX")

    def test_raises_when_file_missing(self, tmp_models_dir):
        register_version("v001", n_samples=100, metrics={})
        # No .lgbm file
        with pytest.raises(ValueError, match="Model file not found"):
            rollback("v001")


# ---------------------------------------------------------------------------
# TestMakeVersionString
# ---------------------------------------------------------------------------


class TestMakeVersionString:
    def test_starts_with_v(self):
        assert make_version_string().startswith("v")

    def test_versions_are_unique(self):
        # Two calls in quick succession should differ (timestamp precision)
        import time
        v1 = make_version_string()
        time.sleep(1.1)
        v2 = make_version_string()
        assert v1 != v2
