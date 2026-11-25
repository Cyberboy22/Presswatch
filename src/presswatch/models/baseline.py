"""Black-box baseline: gradient boosting on set points and sensor features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor

from presswatch.features import FEATURE_NAMES


class BaselineModel:
    kind = "baseline"

    def __init__(self, targets: Sequence[str], seed: int = 0) -> None:
        self.targets = list(targets)
        self.seed = seed
        self._models: dict = {}

    @staticmethod
    def _matrix(rows: Sequence[Mapping[str, float]]) -> np.ndarray:
        return np.array([[row[name] for name in FEATURE_NAMES] for row in rows])

    def fit(self, rows, y: Mapping[str, np.ndarray]) -> BaselineModel:
        x = self._matrix(rows)
        for target in self.targets:
            model = GradientBoostingRegressor(
                n_estimators=400,
                learning_rate=0.05,
                max_depth=3,
                subsample=0.8,
                random_state=self.seed,
            )
            model.fit(x, np.asarray(y[target]))
            self._models[target] = model
        return self

    def predict(self, rows) -> dict[str, np.ndarray]:
        x = self._matrix(rows)
        return {t: self._models[t].predict(x) for t in self.targets}

    def features_used(self) -> dict[str, list[str]]:
        return {t: list(FEATURE_NAMES) for t in self.targets}

    def coefficients(self) -> dict[str, dict[str, float]]:
        """Feature importances (gradient boosting has no coefficients)."""
        return {
            target: {
                name: round(float(value), 5)
                for name, value in zip(FEATURE_NAMES, model.feature_importances_, strict=True)
            }
            for target, model in self._models.items()
        }
