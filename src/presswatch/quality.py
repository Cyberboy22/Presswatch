"""Data quality checks for incoming cycles.

Errors mean "do not predict on this data" (the cycle is stored but quarantined).
Warnings mean "predict, but tell the operator something looks wrong".
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import numpy as np

from presswatch.sim.process import PARAM_BOUNDS, PARAM_LABELS

SENSOR_RANGES = {
    "furnace_temp_c": (500.0, 850.0, "Ofentemperatur"),
    "tool_temp_upper_c": (300.0, 700.0, "Temperatur Oberwerkzeug"),
    "tool_temp_lower_c": (300.0, 700.0, "Temperatur Unterwerkzeug"),
    "force_n": (-50.0, 3000.0, "Presskraft"),
}
SETPOINT_TOLERANCE_C = 8.0
FORCE_TOLERANCE = 0.05
GOB_MASS_RANGE_MG = (600.0, 670.0)
CURVE_LENGTH_TOLERANCE = 2


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str  # "warning" | "error"
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _de(value: float, digits: int = 1) -> str:
    """German number format for messages shown to people (comma as decimal mark)."""
    return f"{value:,.{digits}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def has_errors(issues: list[Issue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def check_cycle(params: Mapping[str, float], gob_mass_mg: float, curves: Mapping) -> list[Issue]:
    issues: list[Issue] = []
    issues += _check_params(params)
    arrays = {name: np.asarray(curves[name], dtype=float) for name in SENSOR_RANGES}

    if any(not np.all(np.isfinite(values)) for values in arrays.values()):
        issues.append(Issue("NAN_VALUES", "error", "Sensordaten enthalten fehlende Werte (NaN)."))
        return issues

    issues += _check_lengths(params, curves, arrays)
    issues += _check_ranges(arrays)
    if any(issue.code == "SENSOR_RANGE" for issue in issues):
        return issues
    issues += _check_frozen(arrays)
    issues += _check_setpoints(params, arrays)

    low, high = GOB_MASS_RANGE_MG
    if not low <= gob_mass_mg <= high:
        issues.append(
            Issue(
                "GOB_MASS",
                "warning",
                f"Glasposten wiegt {_de(gob_mass_mg)} mg (erwartet {low:.0f}–{high:.0f} mg).",
            )
        )
    return issues


def _check_params(params: Mapping[str, float]) -> list[Issue]:
    issues = []
    for name, (low, high) in PARAM_BOUNDS.items():
        value = float(params[name])
        if not math.isfinite(value) or not low <= value <= high:
            issues.append(
                Issue(
                    "PARAM_OUT_OF_RANGE",
                    "error",
                    f"{PARAM_LABELS[name]} = {_de(value, 1)} liegt außerhalb von "
                    f"{_de(low, 0)} bis {_de(high, 0)}.",
                )
            )
    return issues


def _check_lengths(params, curves, arrays) -> list[Issue]:
    expected_heat = int(float(params["heating_time_s"]) * float(curves["heating_rate_hz"]))
    expected_press = int(float(params["press_time_s"]) * float(curves["pressing_rate_hz"]))
    expected = {
        "furnace_temp_c": expected_heat,
        "tool_temp_upper_c": expected_press,
        "tool_temp_lower_c": expected_press,
        "force_n": expected_press,
    }
    issues = []
    for name, n_expected in expected.items():
        n_actual = len(arrays[name])
        if abs(n_actual - n_expected) > CURVE_LENGTH_TOLERANCE:
            issues.append(
                Issue(
                    "CURVE_LENGTH",
                    "error",
                    f"{SENSOR_RANGES[name][2]}: {n_actual} Messpunkte, erwartet ~{n_expected}.",
                )
            )
    return issues


def _check_ranges(arrays) -> list[Issue]:
    issues = []
    for name, (low, high, label) in SENSOR_RANGES.items():
        values = arrays[name]
        if values.size and (values.min() < low or values.max() > high):
            issues.append(
                Issue(
                    "SENSOR_RANGE",
                    "error",
                    f"{label} außerhalb des physikalisch möglichen Bereichs "
                    f"({_de(values.min(), 0)} bis {_de(values.max(), 0)}).",
                )
            )
    return issues


def _check_frozen(arrays) -> list[Issue]:
    issues = []
    for name in ("tool_temp_upper_c", "tool_temp_lower_c", "force_n"):
        values = arrays[name]
        if values.size > 5 and np.ptp(values) < 1e-6:
            issues.append(
                Issue(
                    "SENSOR_FROZEN",
                    "warning",
                    f"{SENSOR_RANGES[name][2]} ändert sich nicht – Sensor eingefroren?",
                )
            )
    return issues


def _check_setpoints(params, arrays) -> list[Issue]:
    issues = []
    for name, set_name, label in (
        ("tool_temp_upper_c", "temp_upper_c", "Oberwerkzeug"),
        ("tool_temp_lower_c", "temp_lower_c", "Unterwerkzeug"),
    ):
        actual, setpoint = float(np.mean(arrays[name])), float(params[set_name])
        if abs(actual - setpoint) > SETPOINT_TOLERANCE_C:
            issues.append(
                Issue(
                    "SETPOINT_DEVIATION",
                    "warning",
                    f"{label}: Ist {_de(actual)} °C, Soll {_de(setpoint, 0)} °C – "
                    "Thermoelement oder Regelung prüfen.",
                )
            )
    force_max, force_set = float(np.max(arrays["force_n"])), float(params["press_force_n"])
    if abs(force_max - force_set) > FORCE_TOLERANCE * force_set:
        issues.append(
            Issue(
                "FORCE_DEVIATION",
                "warning",
                f"Presskraft: Ist {_de(force_max, 0)} N, Soll {_de(force_set, 0)} N.",
            )
        )
    return issues
