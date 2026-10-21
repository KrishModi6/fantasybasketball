"""Inference wrapper around the trained LightGBM residual models.

Each target (minutes, availability, and the per-36 rate of every modelled
stat) has its own booster that predicts the *residual* from the SPS baseline,
so with no signal the model falls back to the statistical projection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np

from ..history import DATA_DIR
from .features import feature_group, feature_names

MODEL_PATH = DATA_DIR / "model" / "projection_model.joblib"


class ProjectionModel:
    def __init__(self, bundle: dict[str, Any]):
        self.bundle = bundle
        self.boosters = bundle["boosters"]  # target -> lightgbm.Booster
        self.features: list[str] = bundle["features"]
        self.ensemble: dict[str, dict[str, float]] = bundle.get("ensemble", {})
        self.prior_minutes: dict[str, float] = bundle.get("prior_minutes", {})
        self.metrics: dict[str, Any] = bundle.get("metrics", {})
        self.trained_through: int | None = bundle.get("trained_through")

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ProjectionModel | None":
        p = Path(path) if path else MODEL_PATH
        if not p.exists():
            return None
        bundle = joblib.load(p)
        if bundle.get("features") != feature_names():
            return None  # trained against a different feature set; retrain
        return cls(bundle)

    def _matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        return np.array([[r.get(f, np.nan) for f in self.features] for r in rows], dtype=float)

    @staticmethod
    def _base_value(target: str, row: dict[str, float]) -> float:
        if target == "mpg":
            return row["sps_mpg"]
        if target == "avail":
            return row["sps_avail"]
        return row[f"sps_{target}"]

    def predict(self, rows: list[dict[str, float]]) -> dict[str, np.ndarray]:
        """Absolute predictions per target (rates per 36, mpg, availability fraction)."""
        if not rows:
            return {}
        X = self._matrix(rows)
        out = {}
        for target, booster in self.boosters.items():
            base = np.array([self._base_value(target, r) for r in rows])
            out[target] = base + booster.predict(X)
        return out

    def contributions(self, row: dict[str, float]) -> dict[str, dict[str, float]]:
        """TreeSHAP contributions of each feature group to each target's residual."""
        X = self._matrix([row])
        out: dict[str, dict[str, float]] = {}
        for target, booster in self.boosters.items():
            contrib = booster.predict(X, pred_contrib=True)[0]
            groups: dict[str, float] = {}
            for name, value in zip(self.features, contrib[:-1]):
                g = feature_group(name)
                groups[g] = groups.get(g, 0.0) + float(value)
            out[target] = groups
        return out
