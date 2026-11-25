"""Performance drift: is the model's error changing?

Only some lenses are measured, so the error (residual = measured − predicted) is
known for a sample of cycles. After a model is activated, the first
``baseline_n`` residuals define "normal": a mean per recipe (models are often a
bit off in a recipe-specific way) and a spread. Afterwards a two-sided tabular
CUSUM adds up standardized deviations and raises an alarm once the sum crosses
the threshold ``h``. CUSUM reacts to small, persistent shifts – exactly what tool
wear looks like – while ignoring single outliers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import numpy as np

DEFAULT_K = 0.75  # allowance in standard deviations
DEFAULT_H = 10.0  # decision threshold in standard deviations
DEFAULT_BASELINE_N = 60  # measured lenses that define "normal" after a model is activated
# Tuned on simulated runs: 0 false alarms in 12 healthy runs of 1500 cycles each,
# tool wear of 0.01 µm/cycle found after 65–75 cycles (see docs/LERNPFAD.md).
MIN_PER_RECIPE = 3


@dataclass
class PerformanceReport:
    state: str  # "einlaufen" (still collecting the baseline) | "aktiv"
    n: int
    baseline_n: int
    k: float
    threshold: float
    sigma: float | None = None
    recipe_bias: dict[str, float] = field(default_factory=dict)
    cusum_upper: list[float] = field(default_factory=list)
    cusum_lower: list[float] = field(default_factory=list)
    alarm_index: int | None = None
    direction: str | None = None  # "up": parts worse than predicted, "down": better
    recent_mean: float | None = None
    recent_mae: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def performance_drift(
    residuals: Sequence[float],
    recipes: Sequence[str],
    baseline_n: int = DEFAULT_BASELINE_N,
    k: float = DEFAULT_K,
    h: float = DEFAULT_H,
    min_sigma: float = 0.0,
) -> PerformanceReport:
    res = np.asarray(residuals, dtype=float)
    report = PerformanceReport(
        state="einlaufen", n=len(res), baseline_n=baseline_n, k=k, threshold=h
    )
    if len(res):
        report.recent_mean = float(np.mean(res[-10:]))
        report.recent_mae = float(np.mean(np.abs(res[-20:])))
    if len(res) <= baseline_n:
        return report

    base, base_recipes = res[:baseline_n], list(recipes[:baseline_n])
    global_mean = float(np.mean(base))
    bias = {}
    for recipe in set(base_recipes):
        values = [v for v, r in zip(base, base_recipes, strict=True) if r == recipe]
        if len(values) >= MIN_PER_RECIPE:
            bias[recipe] = float(np.mean(values))
    centered_base = np.array(
        [v - bias.get(r, global_mean) for v, r in zip(base, base_recipes, strict=True)]
    )
    sigma = max(float(np.std(centered_base, ddof=1)), min_sigma, 1e-9)

    report.state, report.sigma = "aktiv", sigma
    report.recipe_bias = {r: round(b, 5) for r, b in sorted(bias.items())}

    upper = lower = 0.0
    for i in range(baseline_n, len(res)):
        z = (res[i] - bias.get(recipes[i], global_mean)) / sigma
        upper = max(0.0, upper + z - k)
        lower = max(0.0, lower - z - k)
        report.cusum_upper.append(round(upper, 4))
        report.cusum_lower.append(round(lower, 4))
        if report.alarm_index is None and (upper > h or lower > h):
            report.alarm_index = i
            report.direction = "up" if upper > h else "down"
    return report
