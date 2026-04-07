"""
Tests for bot/ml/model.py

A tiny LightGBM model is trained in a fixture and saved to a temporary
directory.  The model module is patched to use that directory so no real
IBKR data or production model files are needed.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from bot.ml.features import FEATURE_NAMES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tiny_model_path(tmp_path_factory):
    """
    Train a tiny 3-class LightGBM model on random data and return its path.
    Uses module scope so it is trained only once per test session.
    """
    import lightgbm as lgb

    tmp = tmp_path_factory.mktemp("ml_models")
    n = 300
    rng = np.random.default_rng(99)
    X = rng.standard_normal((n, len(FEATURE_NAMES)))
    y = rng.integers(0, 3, n)

    ds = lgb.Dataset(X, y, feature_name=FEATURE_NAMES)
    params = {
        "objective": "multiclass",
        "num_class": 3,
        "num_leaves": 4,
        "n_estimators": 10,
        "verbose": -1,
    }
    booster = lgb.train(params, ds, num_boost_round=10)
    path = tmp / "model_vtest.lgbm"
    booster.save_model(str(path))
    return path


@pytest.fixture()
def model_pointing_at(tiny_model_path):
    """
    Patch versioning so model.py loads the tiny test model.
    Resets the module-level singleton before and after.
    """
    import bot.ml.model as model_module

    with patch("bot.ml.versioning.get_model_path", return_value=tiny_model_path):
        # Clear singleton so the patched path is used
        with model_module._lock:
            model_module._booster = None
            model_module._loaded_path = None
        yield
        # Cleanup singleton after test
        with model_module._lock:
            model_module._booster = None
            model_module._loaded_path = None


@pytest.fixture()
def no_model():
    """Patch versioning so no model file is found."""
    import bot.ml.model as model_module

    with patch("bot.ml.versioning.get_model_path", return_value=None):
        with model_module._lock:
            model_module._booster = None
            model_module._loaded_path = None
        yield
        with model_module._lock:
            model_module._booster = None
            model_module._loaded_path = None


def _make_feature_row(value: float = 0.5) -> pd.DataFrame:
    """Return a one-row feature DataFrame with all valid (non-NaN) values."""
    data = {col: [value] for col in FEATURE_NAMES}
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# TestPredict — with model loaded
# ---------------------------------------------------------------------------


class TestPredictWithModel:
    def test_returns_prediction_namedtuple(self, model_pointing_at):
        from bot.ml.model import Prediction, predict
        result = predict(_make_feature_row())
        assert isinstance(result, Prediction)

    def test_label_is_valid(self, model_pointing_at):
        from bot.ml.model import predict
        label, _ = predict(_make_feature_row())
        assert label in ("long", "short", "no_trade")

    def test_probability_between_0_and_1(self, model_pointing_at):
        from bot.ml.model import predict
        _, prob = predict(_make_feature_row())
        assert 0.0 <= prob <= 1.0

    def test_nan_feature_returns_no_trade(self, model_pointing_at):
        from bot.ml.model import predict
        row = _make_feature_row()
        row.iloc[0, 0] = float("nan")
        label, prob = predict(row)
        assert label == "no_trade"
        assert prob == 0.0

    def test_repeated_calls_consistent(self, model_pointing_at):
        from bot.ml.model import predict
        row = _make_feature_row(0.3)
        r1 = predict(row)
        r2 = predict(row)
        assert r1 == r2


# ---------------------------------------------------------------------------
# TestPredict — without model
# ---------------------------------------------------------------------------


class TestPredictNoModel:
    def test_returns_no_trade_when_no_model(self, no_model):
        from bot.ml.model import predict
        label, prob = predict(_make_feature_row())
        assert label == "no_trade"
        assert prob == 0.0


# ---------------------------------------------------------------------------
# TestReloadModel
# ---------------------------------------------------------------------------


class TestReloadModel:
    def test_reload_returns_true_with_model(self, model_pointing_at):
        from bot.ml.model import reload_model
        assert reload_model() is True

    def test_reload_returns_false_without_model(self, no_model):
        from bot.ml.model import reload_model
        assert reload_model() is False
