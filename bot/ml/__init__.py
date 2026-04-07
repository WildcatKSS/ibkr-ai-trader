"""
bot/ml — LightGBM signal model for intraday trading.

Public API
----------
- ``features.build``   — extract feature matrix from enriched OHLCV DataFrame
- ``model.predict``    — run inference: "long" / "short" / "no_trade" + confidence
- ``trainer.train``    — train model on historical data and save versioned file
- ``versioning.*``     — list, set, and rollback model versions
"""

from bot.ml.features import FEATURE_NAMES, build
from bot.ml.model import predict, reload_model
from bot.ml.versioning import get_current_version, list_versions, rollback

__all__ = [
    "FEATURE_NAMES",
    "build",
    "predict",
    "reload_model",
    "get_current_version",
    "list_versions",
    "rollback",
]
