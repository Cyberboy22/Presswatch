"""Feature extraction from one press cycle.

The same function is used for training *and* in the running API, so there is no
difference between the features a model was trained on and the features it sees
in production ("training/serving skew").

The sensor features follow the NGM features in Mende et al. (2026): minimum
temperature near the tools, area under the furnace temperature curve, standard
deviation of the tool temperatures, maximum press force and force duration.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from presswatch.sim.process import PARAM_BOUNDS, CycleRecord

FURNACE_SETPOINT_C = 720.0

PARAM_FEATURES = list(PARAM_BOUNDS)

SENSOR_FEATURES = [
    "gob_mass_mg",
    "furnace_temp_area_kcs",
    "furnace_temp_plateau_c",
    "tool_upper_mean_c",
    "tool_lower_mean_c",
    "tool_upper_min_c",
    "tool_lower_min_c",
    "tool_temp_std_c",
    "force_max_n",
    "force_duration_s",
]

# Features a black-box model gets: set points plus sensor summaries.
FEATURE_NAMES = PARAM_FEATURES + SENSOR_FEATURES

# "Actual minus set point" features. They should stay stable no matter which
# recipe is running, so a recipe change does not look like drift.
DRIFT_FEATURES = [
    "dev_tool_upper_c",
    "dev_tool_lower_c",
    "dev_force_n",
    "dev_force_duration_s",
    "dev_furnace_c",
    "gob_mass_mg",
]

FEATURE_LABELS = {
    "dev_tool_upper_c": "Oberwerkzeug Ist − Soll [°C]",
    "dev_tool_lower_c": "Unterwerkzeug Ist − Soll [°C]",
    "dev_force_n": "Presskraft Ist − Soll [N]",
    "dev_force_duration_s": "Kraftdauer Ist − Soll [s]",
    "dev_furnace_c": "Ofen Ist − Soll [°C]",
    "gob_mass_mg": "Glasposten-Masse [mg]",
}


def extract_features(
    params: Mapping[str, float], gob_mass_mg: float, curves: Mapping
) -> dict[str, float]:
    furnace = np.asarray(curves["furnace_temp_c"], dtype=float)
    upper = np.asarray(curves["tool_temp_upper_c"], dtype=float)
    lower = np.asarray(curves["tool_temp_lower_c"], dtype=float)
    force = np.asarray(curves["force_n"], dtype=float)
    heating_dt = 1.0 / float(curves["heating_rate_hz"])
    pressing_dt = 1.0 / float(curves["pressing_rate_hz"])

    plateau = float(np.mean(furnace[len(furnace) // 2 :]))
    force_max = float(np.max(force))
    force_duration = float(np.count_nonzero(force > 0.1 * force_max) * pressing_dt)
    upper_mean, lower_mean = float(np.mean(upper)), float(np.mean(lower))

    features = {name: float(params[name]) for name in PARAM_FEATURES}
    features |= {
        "gob_mass_mg": float(gob_mass_mg),
        "furnace_temp_area_kcs": float(np.sum(furnace) * heating_dt / 1000.0),
        "furnace_temp_plateau_c": plateau,
        "tool_upper_mean_c": upper_mean,
        "tool_lower_mean_c": lower_mean,
        "tool_upper_min_c": float(np.min(upper)),
        "tool_lower_min_c": float(np.min(lower)),
        "tool_temp_std_c": float(np.std(np.concatenate([upper, lower]))),
        "force_max_n": force_max,
        "force_duration_s": force_duration,
        "dev_tool_upper_c": upper_mean - float(params["temp_upper_c"]),
        "dev_tool_lower_c": lower_mean - float(params["temp_lower_c"]),
        "dev_force_n": force_max - float(params["press_force_n"]),
        "dev_force_duration_s": force_duration - float(params["press_time_s"]),
        "dev_furnace_c": plateau - FURNACE_SETPOINT_C,
    }
    return features


def features_of(record: CycleRecord) -> dict[str, float]:
    return extract_features(record.params.as_dict(), record.gob_mass_mg, record.curves.as_dict())
