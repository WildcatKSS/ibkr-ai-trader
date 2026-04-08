"""
LightGBM model trainer.

Trains a 3-class signal classifier (long / short / no_trade) on historical
5-minute OHLCV data and saves a versioned model file.

Label generation
----------------
For each bar *t*, the forward return over ``forward_bars`` (default 6 = 30 min)
is computed::

    forward_return = log(close[t + forward_bars] / close[t])

Labels:
    1 (long)     if forward_return ≥  long_threshold_pct / 100
    2 (short)    if forward_return ≤ −short_threshold_pct / 100
    0 (no_trade) otherwise

CLI::

    python -m bot.ml.trainer --retrain --data path/to/data.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

from bot.ml.features import build
from bot.ml.versioning import (
    make_version_string,
    register_version,
    _MODELS_DIR,
)
from bot.signals.indicators import calculate
from bot.utils.logger import get_logger

log = get_logger("ml")

# ---------------------------------------------------------------------------
# Default training hyper-parameters
# (all overridable via keyword arguments to ``train()``)
# ---------------------------------------------------------------------------
_DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 30,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
    "verbose": -1,
    "n_jobs": -1,
}

_NUM_BOOST_ROUND = 300
_EARLY_STOPPING_ROUNDS = 50
_VAL_SIZE = 0.2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def train(
    df: pd.DataFrame,
    *,
    forward_bars: int = 6,
    long_threshold_pct: float = 0.3,
    short_threshold_pct: float = 0.3,
    num_boost_round: int = _NUM_BOOST_ROUND,
    lgbm_params: dict[str, Any] | None = None,
    output_dir: Path | str | None = None,
    version: str | None = None,
) -> str:
    """
    Train LightGBM on historical 5-min OHLCV data.

    Parameters
    ----------
    df:
        Historical 5-min OHLCV DataFrame.  Must contain at minimum:
        ``open``, ``high``, ``low``, ``close``, ``volume``.
        Column names are normalised to lowercase internally.
    forward_bars:
        Number of bars ahead used to compute the label (default 6 = 30 min).
    long_threshold_pct:
        Minimum forward return (%) for a long label.
    short_threshold_pct:
        Minimum forward drop (%) for a short label.
    num_boost_round:
        Maximum LightGBM boosting rounds.
    lgbm_params:
        Override default LightGBM parameters.
    output_dir:
        Directory to save the model file.  Defaults to ``bot/ml/models/``.
    version:
        Version string; auto-generated from the current UTC timestamp if None.

    Returns
    -------
    str
        The version string (e.g. ``"v20240101_120000"``).
    """
    import lightgbm as lgb

    output_dir = Path(output_dir) if output_dir else _MODELS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    version = version or make_version_string()
    params = {**_DEFAULT_PARAMS, **(lgbm_params or {})}

    log.info("Starting model training", version=version, rows=len(df))

    # ── 1. Compute indicators ─────────────────────────────────────────────
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    enriched = calculate(df)

    # ── 2. Build features ─────────────────────────────────────────────────
    features = build(enriched)

    # ── 3. Generate labels ────────────────────────────────────────────────
    close = df["close"]
    fwd_return = np.log(close.shift(-forward_bars) / close)
    long_thr = long_threshold_pct / 100.0
    short_thr = short_threshold_pct / 100.0
    labels = pd.Series(0, index=df.index)
    labels[fwd_return >= long_thr] = 1
    labels[fwd_return <= -short_thr] = 2

    # ── 4. Drop NaN rows ─────────────────────────────────────────────────
    valid = features.notna().all(axis=1) & labels.notna()
    X = features[valid].values.astype(np.float64)
    y = labels[valid].values.astype(int)

    if len(X) < 100:
        raise ValueError(
            f"Too few valid training samples after dropping NaN: {len(X)}. "
            "Provide more history (at least ~500 rows recommended)."
        )

    log.info("Training samples", total=len(X),
             long=int((y == 1).sum()), short=int((y == 2).sum()),
             no_trade=int((y == 0).sum()))

    # ── 5. Train / validate split ─────────────────────────────────────────
    X_tr, X_val, y_tr, y_val = train_test_split(
        X, y, test_size=_VAL_SIZE, shuffle=False
    )

    lgb_train = lgb.Dataset(X_tr, y_tr, feature_name=features.columns.tolist())
    lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)

    # ── 6. Fit ────────────────────────────────────────────────────────────
    callbacks = [
        lgb.early_stopping(stopping_rounds=_EARLY_STOPPING_ROUNDS, verbose=False),
        lgb.log_evaluation(period=0),  # silence per-round output
    ]
    booster = lgb.train(
        params,
        lgb_train,
        num_boost_round=num_boost_round,
        valid_sets=[lgb_val],
        callbacks=callbacks,
    )

    # ── 7. Evaluate ───────────────────────────────────────────────────────
    val_preds = np.argmax(booster.predict(X_val), axis=1)
    val_accuracy = float((val_preds == y_val).mean())
    val_logloss = float(booster.best_score.get("valid_0", {}).get("multi_logloss", -1))
    metrics = {"val_accuracy": round(val_accuracy, 4), "val_logloss": round(val_logloss, 4)}
    log.info("Training complete", version=version, **metrics)

    # ── 8. Save model ─────────────────────────────────────────────────────
    model_path = output_dir / f"model_{version}.lgbm"
    booster.save_model(str(model_path))
    log.info("Model saved", path=str(model_path))

    # ── 9. Register in manifest ───────────────────────────────────────────
    register_version(version, n_samples=len(X), metrics=metrics)

    return version


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the LightGBM signal model")
    parser.add_argument("--retrain", action="store_true", required=True,
                        help="Trigger retraining (required flag)")
    parser.add_argument("--data", required=True,
                        help="Path to historical 5-min OHLCV CSV file")
    parser.add_argument("--forward-bars", type=int, default=6)
    parser.add_argument("--long-threshold-pct", type=float, default=0.3)
    parser.add_argument("--short-threshold-pct", type=float, default=0.3)
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        log.error("Data file not found", path=str(data_path))
        sys.stderr.write(f"Error: data file not found: {data_path}\n")
        sys.exit(1)

    df = pd.read_csv(data_path, parse_dates=True, index_col=0)
    version = train(
        df,
        forward_bars=args.forward_bars,
        long_threshold_pct=args.long_threshold_pct,
        short_threshold_pct=args.short_threshold_pct,
    )
    sys.stdout.write(f"Trained model version: {version}\n")
