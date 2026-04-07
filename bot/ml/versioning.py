"""
LightGBM model versioning.

Model files live in ``bot/ml/models/`` and are never committed to git.
A ``version.json`` manifest tracks which version is active and stores
basic metadata for each trained version.

Manifest format::

    {
        "current": "v20240101_120000",
        "versions": [
            {
                "version":    "v20240101_120000",
                "trained_at": "2024-01-01T12:00:00+00:00",
                "n_samples":  12500,
                "metrics":    {"val_logloss": 0.842, "val_accuracy": 0.613}
            }
        ]
    }

CLI::

    python -m bot.ml.versioning --list
    python -m bot.ml.versioning --current
    python -m bot.ml.versioning --rollback v20240101_120000
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.utils.logger import get_logger

log = get_logger("ml")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_MODELS_DIR = Path(__file__).parent / "models"
_MANIFEST = _MODELS_DIR / "version.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_version() -> str | None:
    """Return the name of the currently active model version, or None."""
    manifest = _load_manifest()
    return manifest.get("current")


def get_model_path(version: str | None = None) -> Path | None:
    """
    Return the filesystem path to *version*'s model file.

    Uses the current version when *version* is None.
    Returns None when no version is registered or the file does not exist.
    """
    if version is None:
        version = get_current_version()
    if version is None:
        return None
    path = _MODELS_DIR / f"model_{version}.lgbm"
    return path if path.exists() else None


def list_versions() -> list[dict[str, Any]]:
    """Return all registered versions (newest first)."""
    return list(reversed(_load_manifest().get("versions", [])))


def register_version(
    version: str,
    *,
    n_samples: int,
    metrics: dict[str, float],
    set_current: bool = True,
) -> None:
    """
    Register a newly trained model version in the manifest.

    Called by ``trainer.train`` immediately after saving the model file.
    """
    manifest = _load_manifest()
    entry = {
        "version": version,
        "trained_at": datetime.now(tz=timezone.utc).isoformat(),
        "n_samples": n_samples,
        "metrics": metrics,
    }
    # Remove duplicate if version was previously registered
    manifest["versions"] = [
        v for v in manifest.get("versions", []) if v["version"] != version
    ]
    manifest["versions"].append(entry)
    if set_current:
        manifest["current"] = version
    _save_manifest(manifest)
    log.info("Model version registered", version=version, set_current=set_current)


def rollback(version: str) -> None:
    """
    Set *version* as the active model.

    Raises ValueError when the version is not registered or its model file
    is missing.
    """
    manifest = _load_manifest()
    known = {v["version"] for v in manifest.get("versions", [])}
    if version not in known:
        raise ValueError(f"Unknown version '{version}'. Known: {sorted(known)}")
    path = _MODELS_DIR / f"model_{version}.lgbm"
    if not path.exists():
        raise ValueError(f"Model file not found: {path}")
    manifest["current"] = version
    _save_manifest(manifest)
    log.info("Model rolled back", version=version)


def make_version_string() -> str:
    """Return a new version string based on the current UTC timestamp."""
    return "v" + datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST.exists():
        return {"current": None, "versions": []}
    with _MANIFEST.open() as f:
        return json.load(f)


def _save_manifest(manifest: dict[str, Any]) -> None:
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with _MANIFEST.open("w") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Manage LightGBM model versions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List all versions")
    group.add_argument("--current", action="store_true", help="Show current version")
    group.add_argument("--rollback", metavar="VERSION", help="Roll back to VERSION")
    args = parser.parse_args()

    if args.list:
        versions = list_versions()
        if not versions:
            print("No versions registered.")
        for v in versions:
            marker = " ← current" if v["version"] == get_current_version() else ""
            print(f"{v['version']}{marker}  trained={v['trained_at']}  "
                  f"samples={v['n_samples']}  metrics={v['metrics']}")

    elif args.current:
        cur = get_current_version()
        print(cur if cur else "No current version set.")

    elif args.rollback:
        try:
            rollback(args.rollback)
            print(f"Rolled back to {args.rollback}.")
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
