"""Offline scenario runs: the whole monitoring chain without servers.

Each run trains the initial model on a DoE, then produces lenses, measures every
``sample_every``-th lens and evaluates the monitoring every 10 cycles – the same
logic the API uses, only faster. The result tells us *when* a fault is detected
compared to *when* scrap really starts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from presswatch.features import features_of
from presswatch.models import TARGETS, train_model
from presswatch.monitoring.decision import recommend
from presswatch.monitoring.drift import data_drift
from presswatch.monitoring.performance import performance_drift
from presswatch.sim.doe import doe_cycles
from presswatch.sim.process import SPEC, measure
from presswatch.sim.scenario import ProductionLine

MIN_SIGMA = {"center_thickness_mm": 0.002, "pv_um": 0.15}
REFERENCE_CYCLES = 150
WINDOW_CYCLES = 100
SCENARIOS = ("normal", "werkzeugverschleiss", "sensorfehler")
SCRAP_WINDOW = 50  # "scrap has started" = at least 20 % of the last 50 lenses out of spec
SCRAP_SHARE = 0.2


@dataclass
class ScenarioResult:
    name: str
    n_cycles: int
    fault_start: int | None
    seed: int = 0
    first_warning_cycle: int | None = None
    first_warning_cause: str | None = None
    first_alarm_cycle: int | None = None
    first_alarm_cause: str | None = None
    first_scrap_cycle: int | None = None
    timeline: list[dict] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _training_set(n: int, seed: int):
    cycles = doe_cycles(n, seed=seed)
    rows = [features_of(c) for c in cycles]
    y = {t: np.array([getattr(c.truth, t) for c in cycles]) for t in TARGETS}
    return rows, y


def run_scenario(
    name: str,
    n_cycles: int = 1200,
    fault_start: int = 400,
    seed: int = 7,
    sample_every: int = 5,
    doe_size: int = 150,
    wear_rate_um: float = 0.01,
    sensor_offset_c: float = 20.0,
) -> ScenarioResult:
    if name not in SCENARIOS:
        raise ValueError(f"Szenario '{name}' ist unbekannt (erlaubt: {SCENARIOS})")
    model = train_model("hybrid", *_training_set(doe_size, seed=42), seed=0).model
    line = ProductionLine(seed=seed)
    gauge = np.random.default_rng(seed + 1)
    result = ScenarioResult(name, n_cycles, None if name == "normal" else fault_start, seed=seed)

    rows, residuals, recipes, scrap = [], {t: [] for t in TARGETS}, [], []
    for i in range(1, n_cycles + 1):
        if i == fault_start and name == "werkzeugverschleiss":
            line.faults.tool_wear_rate_um_per_cycle = wear_rate_um
        if i == fault_start and name == "sensorfehler":
            line.faults.sensor_offset_target_c = sensor_offset_c
            line.faults.sensor_offset_ramp_c_per_cycle = 0.1

        cycle = line.next_cycle()
        row = features_of(cycle) | {"recipe": cycle.recipe}
        rows.append(row)
        prediction = {t: float(v[0]) for t, v in model.predict([row]).items()}
        scrap.append(not SPEC.is_ok(cycle.truth))
        if (
            result.first_scrap_cycle is None
            and i >= SCRAP_WINDOW
            and np.mean(scrap[-SCRAP_WINDOW:]) >= SCRAP_SHARE
        ):
            result.first_scrap_cycle = i

        if i % sample_every == 0:
            measured = measure(cycle.truth, gauge)
            for t in TARGETS:
                residuals[t].append(getattr(measured, t) - prediction[t])
            recipes.append(cycle.recipe)
            result.measurements.append(
                {
                    "cycle": i,
                    "predicted": prediction,
                    "measured": {t: getattr(measured, t) for t in TARGETS},
                }
            )

        if i % 10 == 0 and i > REFERENCE_CYCLES:
            drift = data_drift(rows[:REFERENCE_CYCLES], rows[-WINDOW_CYCLES:])
            perf = {
                t: performance_drift(residuals[t], recipes, min_sigma=MIN_SIGMA[t]) for t in TARGETS
            }
            rec = recommend(data=drift, performance=perf)
            result.timeline.append({"cycle": i, "status": rec.status, "cause": rec.cause})
            if rec.status != "ok" and result.first_warning_cycle is None:
                result.first_warning_cycle, result.first_warning_cause = i, rec.cause
            if rec.status == "alarm" and result.first_alarm_cycle is None:
                result.first_alarm_cycle, result.first_alarm_cause = i, rec.cause
    return result
