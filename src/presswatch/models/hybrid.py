"""Hybrid model: physical expert knowledge + a small linear model.

A process expert knows roughly how the process works (heat transfer, VFT
viscosity, squeeze flow) but not the exact constants. We compute physically
motivated features with *deliberately approximate* constants (they differ from
the simulator) and let a Lasso regression correct and weight them. This is the
"expert features + Lasso" structure that worked best in Mende et al. (2026).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
from sklearn.linear_model import LassoCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

AMBIENT_C = 25.0

# Expert guesses – intentionally not identical to the simulator's true physics.
EXPERT = {
    "heating_tau_s": 55.0,
    "transfer_drop_c": 12.0,
    "vft_a": -1.2,
    "vft_b": 2750.0,
    "vft_t0_c": 305.0,
    "cooling_tau_s": 6.0,
    "glass_density_mg_per_mm3": 3.0,
    "soft_release_log_visc": 9.5,
    "stiff_start_log_visc": 7.3,
}

HYBRID_FEATURES: dict[str, list[str]] = {
    "center_thickness_mm": ["est_ct_mm", "est_log_visc_start", "gob_mass_mg"],
    "pv_um": ["asymmetry_c", "quench_c", "soft_release", "stiff_start"],
}


def _log_visc(temp_c: float) -> float:
    return EXPERT["vft_a"] + EXPERT["vft_b"] / max(temp_c - EXPERT["vft_t0_c"], 1.0)


def expert_features(row: Mapping[str, float]) -> dict[str, float]:
    """Physically motivated features computed only from data the pipeline can see."""
    furnace = row["furnace_temp_plateau_c"]
    temp_heated = furnace - (furnace - AMBIENT_C) * math.exp(
        -row["heating_time_s"] / EXPERT["heating_tau_s"]
    )
    temp_start = temp_heated - EXPERT["transfer_drop_c"]
    tool_mean = (row["tool_upper_mean_c"] + row["tool_lower_mean_c"]) / 2.0

    volume_mm3 = row["gob_mass_mg"] / EXPERT["glass_density_mg_per_mm3"]
    volume_m3 = volume_mm3 * 1e-9
    inv_h4 = ((6.0 * volume_mm3 / math.pi) ** (1.0 / 3.0) * 1e-3) ** -4
    dt = 0.1
    decay = math.exp(-dt / EXPERT["cooling_tau_s"])
    temp = temp_start
    for _ in range(int(row["force_duration_s"] / dt)):
        eta = 10.0 ** _log_visc(temp)
        inv_h4 += 8.0 * math.pi * row["force_max_n"] * dt / (3.0 * eta * volume_m3**2)
        temp = tool_mean + (temp - tool_mean) * decay

    log_visc_start, log_visc_end = _log_visc(temp_start), _log_visc(temp)
    return {
        "est_ct_mm": inv_h4**-0.25 * 1e3,
        "est_log_visc_start": log_visc_start,
        "est_log_visc_end": log_visc_end,
        "gob_mass_mg": row["gob_mass_mg"],
        "asymmetry_c": abs(row["tool_upper_mean_c"] - row["tool_lower_mean_c"]),
        "quench_c": max(0.0, temp_start - tool_mean),
        "soft_release": max(0.0, EXPERT["soft_release_log_visc"] - log_visc_end),
        "stiff_start": max(0.0, log_visc_start - EXPERT["stiff_start_log_visc"]),
    }


class HybridModel:
    kind = "hybrid"

    def __init__(self, targets: Sequence[str], seed: int = 0) -> None:
        self.targets = list(targets)
        self.seed = seed
        self._models: dict = {}

    def _matrix(self, rows: Sequence[Mapping[str, float]], target: str) -> np.ndarray:
        expert = [expert_features(row) for row in rows]
        return np.array([[e[name] for name in HYBRID_FEATURES[target]] for e in expert])

    def fit(self, rows, y: Mapping[str, np.ndarray]) -> HybridModel:
        for target in self.targets:
            model = make_pipeline(
                StandardScaler(), LassoCV(cv=5, random_state=self.seed, max_iter=50_000)
            )
            model.fit(self._matrix(rows, target), np.asarray(y[target]))
            self._models[target] = model
        return self

    def predict(self, rows) -> dict[str, np.ndarray]:
        return {t: self._models[t].predict(self._matrix(rows, t)) for t in self.targets}

    def features_used(self) -> dict[str, list[str]]:
        return {t: list(HYBRID_FEATURES[t]) for t in self.targets}

    def coefficients(self) -> dict[str, dict[str, float]]:
        """Lasso weights on standardized features – shows what the model relies on."""
        result = {}
        for target, model in self._models.items():
            lasso = model[-1]
            result[target] = {
                name: round(float(coef), 5)
                for name, coef in zip(HYBRID_FEATURES[target], lasso.coef_, strict=True)
            }
        return result
