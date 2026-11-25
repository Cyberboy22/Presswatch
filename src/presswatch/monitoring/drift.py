"""Data drift: do the incoming sensor data still look like the reference period?

We compare the "actual minus set point" features of a recent window with a
known-good reference window. Values are centered per recipe first, so a recipe
change is not mistaken for drift. A feature only counts as drifted if both the
Population Stability Index (PSI, size of the change) and the Kolmogorov-Smirnov
test (is the change more than chance?) agree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.stats import ks_2samp

from presswatch.features import DRIFT_FEATURES, FEATURE_LABELS

PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25
KS_ALPHA_SIGNIFICANT = 0.001
KS_ALPHA_MODERATE = 0.01
MIN_SAMPLES = 30


def psi(reference: Sequence[float], current: Sequence[float], bins: int | None = None) -> float:
    """Population Stability Index with quantile bins taken from the reference.

    The number of bins follows the sample size (about 20 values per bin, 3 to 10
    bins) and empty bins are smoothed – otherwise small windows produce large PSI
    values by pure chance.
    """
    ref, cur = np.asarray(reference, dtype=float), np.asarray(current, dtype=float)
    if bins is None:
        bins = int(np.clip(min(ref.size, cur.size) // 20, 3, 10))
    if np.ptp(ref) == 0.0:
        if np.ptp(cur) == 0.0 and cur[0] == ref[0]:
            return 0.0
        edges = np.array([-np.inf, ref[0] - 1e-9, ref[0] + 1e-9, np.inf])
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0.0, 1.0, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
    k = len(edges) - 1
    ref_share = (np.histogram(ref, edges)[0] + 0.5) / (ref.size + 0.5 * k)
    cur_share = (np.histogram(cur, edges)[0] + 0.5) / (cur.size + 0.5 * k)
    return float(np.sum((cur_share - ref_share) * np.log(cur_share / ref_share)))


@dataclass
class FeatureDrift:
    feature: str
    label: str
    psi: float
    ks_pvalue: float
    shift: float
    reference_std: float
    level: str  # "ok" | "moderat" | "signifikant"


@dataclass
class DataDriftReport:
    enough_data: bool
    reference_size: int
    window_size: int
    features: list[FeatureDrift] = field(default_factory=list)

    @property
    def drifted(self) -> list[str]:
        return [f.feature for f in self.features if f.level == "signifikant"]

    def as_dict(self) -> dict:
        return asdict(self) | {"drifted": self.drifted}


def _centered(rows, feature, means, global_mean, group_key):
    return np.array(
        [row[feature] - means.get(row.get(group_key), global_mean) for row in rows], dtype=float
    )


def data_drift(
    reference: Sequence[Mapping],
    current: Sequence[Mapping],
    features: Sequence[str] = DRIFT_FEATURES,
    group_key: str = "recipe",
) -> DataDriftReport:
    report = DataDriftReport(
        enough_data=len(reference) >= MIN_SAMPLES and len(current) >= MIN_SAMPLES,
        reference_size=len(reference),
        window_size=len(current),
    )
    if not report.enough_data:
        return report

    for feature in features:
        groups = defaultdict(list)
        for row in reference:
            groups[row.get(group_key)].append(row[feature])
        global_mean = float(np.mean([row[feature] for row in reference]))
        means = {g: float(np.mean(v)) for g, v in groups.items() if len(v) >= 5}

        ref = _centered(reference, feature, means, global_mean, group_key)
        cur = _centered(current, feature, means, global_mean, group_key)
        value = psi(ref, cur)
        pvalue = 1.0 if np.ptp(np.r_[ref, cur]) == 0.0 else float(ks_2samp(ref, cur).pvalue)
        if value > PSI_SIGNIFICANT and pvalue < KS_ALPHA_SIGNIFICANT:
            level = "signifikant"
        elif value > PSI_MODERATE and pvalue < KS_ALPHA_MODERATE:
            level = "moderat"
        else:
            level = "ok"
        report.features.append(
            FeatureDrift(
                feature=feature,
                label=FEATURE_LABELS.get(feature, feature),
                psi=round(value, 4),
                ks_pvalue=pvalue,
                shift=round(float(np.mean(cur) - np.mean(ref)), 4),
                reference_std=round(float(np.std(ref)), 4),
                level=level,
            )
        )
    return report
