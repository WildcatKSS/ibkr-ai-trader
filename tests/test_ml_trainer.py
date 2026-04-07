"""
Tests for bot/ml/trainer.py

Uses a temporary directory for model output so no real filesystem paths are
touched.  LightGBM is trained on a small synthetic dataset; the test only
verifies that the public API works correctly — it does not validate the model's
predictive quality.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 600) -> pd.DataFrame:
    """
    Return a deterministic OHLCV DataFrame large enough for training
    (need > 100 valid samples after NaN rows are dropped).
    """
    rng = np.random.default_rng(42)
    closes = np.cumprod(1 + rng.normal(0.0003, 0.005, n)) * 100
    highs = closes * 1.006
    lows = closes * 0.994
    opens = lows + (highs - lows) * rng.uniform(0.2, 0.8, n)
    volumes = np.full(n, 1_000_000.0)
    idx = pd.date_range("2024-01-02 09:30", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


# ---------------------------------------------------------------------------
# TestTrain — happy path
# ---------------------------------------------------------------------------


class TestTrain:
    def test_returns_version_string(self, tmp_path):
        from bot.ml.trainer import train
        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest001")
        assert version == "vtest001"

    def test_model_file_created(self, tmp_path):
        from bot.ml.trainer import train
        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest002")
        model_file = tmp_path / f"model_{version}.lgbm"
        assert model_file.exists()

    def test_model_file_is_nonzero(self, tmp_path):
        from bot.ml.trainer import train
        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest003")
        assert (tmp_path / f"model_{version}.lgbm").stat().st_size > 0

    def test_version_registered_in_manifest(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest004")
        versions = versioning_module.list_versions()
        assert any(v["version"] == version for v in versions)

    def test_version_set_as_current(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest005")
        assert versioning_module.get_current_version() == version

    def test_auto_version_string_generated(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(_make_ohlcv(), output_dir=tmp_path)
        assert version.startswith("v")

    def test_uppercase_columns_accepted(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        df = _make_ohlcv()
        df.columns = [c.upper() for c in df.columns]
        version = train(df, output_dir=tmp_path, version="vtest006")
        assert (tmp_path / f"model_{version}.lgbm").exists()

    def test_metrics_stored_in_manifest(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest007")
        versions = versioning_module.list_versions()
        entry = next(v for v in versions if v["version"] == version)
        assert "val_accuracy" in entry["metrics"]
        assert "val_logloss" in entry["metrics"]

    def test_n_samples_stored_in_manifest(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(_make_ohlcv(), output_dir=tmp_path, version="vtest008")
        versions = versioning_module.list_versions()
        entry = next(v for v in versions if v["version"] == version)
        assert entry["n_samples"] > 0


# ---------------------------------------------------------------------------
# TestTrain — label thresholds
# ---------------------------------------------------------------------------


class TestTrainLabelThresholds:
    def test_custom_thresholds_accepted(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(
            _make_ohlcv(),
            output_dir=tmp_path,
            version="vtest009",
            long_threshold_pct=0.5,
            short_threshold_pct=0.5,
        )
        assert (tmp_path / f"model_{version}.lgbm").exists()

    def test_custom_forward_bars(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        version = train(
            _make_ohlcv(),
            output_dir=tmp_path,
            version="vtest010",
            forward_bars=3,
        )
        assert (tmp_path / f"model_{version}.lgbm").exists()


# ---------------------------------------------------------------------------
# TestTrain — error handling
# ---------------------------------------------------------------------------


class TestTrainErrors:
    def test_raises_on_too_few_samples(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        # Only 20 rows — far too few after NaN drop
        with pytest.raises(ValueError, match="Too few valid training samples"):
            train(_make_ohlcv(n=20), output_dir=tmp_path, version="vtestfail")

    def test_output_dir_created_if_missing(self, tmp_path, monkeypatch):
        from bot.ml.trainer import train
        import bot.ml.versioning as versioning_module

        monkeypatch.setattr(versioning_module, "_MODELS_DIR", tmp_path)
        monkeypatch.setattr(versioning_module, "_MANIFEST", tmp_path / "version.json")

        new_dir = tmp_path / "nested" / "subdir"
        assert not new_dir.exists()
        train(_make_ohlcv(), output_dir=new_dir, version="vtest011")
        assert new_dir.exists()
