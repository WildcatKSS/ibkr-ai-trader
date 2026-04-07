"""
LightGBM inference module.

Loads the current model version once (thread-safe lazy load) and exposes
``predict()`` for the signal pipeline.

Usage::

    from bot.ml.model import predict

    label, prob = predict(feature_row_df)
    # label: "long" | "short" | "no_trade"
    # prob:  confidence score (0.0–1.0)

The model file is loaded from the path returned by
``bot.ml.versioning.get_model_path()``.  If no model exists, ``predict``
returns ``("no_trade", 0.0)`` so the pipeline degrades gracefully.

Never call the Claude API from this module.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd

from bot.utils.logger import get_logger

log = get_logger("ml")

# ---------------------------------------------------------------------------
# Class labels — order must match the integers used during training.
# ---------------------------------------------------------------------------
_LABELS = ["no_trade", "long", "short"]

# ---------------------------------------------------------------------------
# Thread-safe singleton
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_booster = None          # lgb.Booster or None
_loaded_path: Path | None = None


class Prediction(NamedTuple):
    label: str    # "long" | "short" | "no_trade"
    probability: float  # confidence of the predicted class


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def predict(features: pd.DataFrame) -> Prediction:
    """
    Run inference on a single-row feature DataFrame.

    Parameters
    ----------
    features:
        One-row DataFrame with columns matching ``bot.ml.features.FEATURE_NAMES``.
        Typically produced by ``features.build(enriched_df).iloc[[-1]]``.

    Returns
    -------
    Prediction
        ``("no_trade", 0.0)`` when no model is loaded or features contain NaN.
    """
    booster = _get_booster()
    if booster is None:
        return Prediction("no_trade", 0.0)

    X = features.values.astype(np.float64)
    if np.isnan(X).any():
        log.debug("Prediction skipped — NaN in feature row")
        return Prediction("no_trade", 0.0)

    try:
        probs = booster.predict(X)   # shape (1, 3)
        row = probs[0]
        class_idx = int(np.argmax(row))
        label = _LABELS[class_idx]
        probability = float(row[class_idx])
        log.debug("Model prediction", label=label, probability=probability)
        return Prediction(label, probability)
    except Exception as exc:  # noqa: BLE001
        log.error("LightGBM inference failed", error=str(exc))
        return Prediction("no_trade", 0.0)


def reload_model() -> bool:
    """
    Force-reload the model from disk (e.g., after retraining).

    Returns True on success, False if no model file is found.
    """
    global _booster, _loaded_path  # noqa: PLW0603
    with _lock:
        _booster = None
        _loaded_path = None
    return _get_booster() is not None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_booster():
    """Return the cached Booster, loading it if necessary."""
    global _booster, _loaded_path  # noqa: PLW0603

    with _lock:
        if _booster is not None:
            return _booster

        path = _find_model_path()
        if path is None:
            log.warning("No LightGBM model file found — predictions return no_trade")
            return None

        try:
            import lightgbm as lgb
            _booster = lgb.Booster(model_file=str(path))
            _loaded_path = path
            log.info("LightGBM model loaded", path=str(path))
            return _booster
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to load LightGBM model", path=str(path), error=str(exc))
            return None


def _find_model_path() -> Path | None:
    """
    Return the path to the current model file, or None if not found.

    Delegates to ``versioning.get_model_path()`` which reads ``version.json``.
    """
    try:
        from bot.ml.versioning import get_model_path
        return get_model_path()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not resolve model path", error=str(exc))
        return None
