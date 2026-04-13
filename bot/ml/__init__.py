"""
bot/ml — LightGBM signal model for intraday trading.

Public API
----------
- ``features.build``   — extract feature matrix from enriched OHLCV DataFrame
- ``model.predict``    — run inference: "long" / "short" / "no_trade" + confidence
- ``trainer.train``    — train model on historical data and save versioned file
- ``versioning.*``     — list, set, and rollback model versions

Imports are deferred so that missing native dependencies (e.g. the ``ta``
library) do not make the entire package unimportable at collection time.
"""


def __getattr__(name: str):
    """Lazy-load public symbols on first access."""
    if name in ("FEATURE_NAMES", "build"):
        from bot.ml.features import FEATURE_NAMES, build
        globals()["FEATURE_NAMES"] = FEATURE_NAMES
        globals()["build"] = build
        return globals()[name]
    if name in ("predict", "reload_model"):
        from bot.ml.model import predict, reload_model
        globals()["predict"] = predict
        globals()["reload_model"] = reload_model
        return globals()[name]
    if name in ("get_current_version", "list_versions", "rollback"):
        from bot.ml.versioning import get_current_version, list_versions, rollback
        globals()["get_current_version"] = get_current_version
        globals()["list_versions"] = list_versions
        globals()["rollback"] = rollback
        return globals()[name]
    raise AttributeError(f"module 'bot.ml' has no attribute {name!r}")


__all__ = [
    "FEATURE_NAMES",
    "build",
    "predict",
    "reload_model",
    "get_current_version",
    "list_versions",
    "rollback",
]
