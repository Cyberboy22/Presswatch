"""Training, evaluation, model cards and serialization."""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

import joblib
import numpy as np

from presswatch.features import PARAM_FEATURES
from presswatch.models.baseline import BaselineModel
from presswatch.models.hybrid import HybridModel

TARGETS = ["center_thickness_mm", "pv_um"]
TARGET_LABELS = {
    "center_thickness_mm": "Mittendicke [mm]",
    "pv_um": "Formabweichung PV [µm]",
}
MODEL_KINDS = {"baseline": BaselineModel, "hybrid": HybridModel}
VALIDITY_FEATURES = [*PARAM_FEATURES, "gob_mass_mg"]

_CARD_TEXT = {
    "hybrid": {
        "title": "Hybridmodell (Physik + Lasso)",
        "description": (
            "Physikalisch motivierte Merkmale (Aufheizung, VFT-Viskosität, Stefan-Quetschströmung, "
            "Temperaturdifferenzen) mit groben Expertenkonstanten; eine Lasso-Regression gewichtet "
            "und korrigiert sie."
        ),
    },
    "baseline": {
        "title": "Black-Box (Gradient Boosting)",
        "description": (
            "Gradient Boosting auf Sollwerten und Sensormerkmalen, ohne Prozesswissen. "
            "Dient als Vergleich."
        ),
    },
}

LIMITATIONS = [
    "Trainiert auf simulierten Daten eines vereinfachten Prozessmodells, "
    "nicht auf einer echten Presse.",
    "Gültig nur innerhalb des Trainingsbereichs (siehe validity_range); außerhalb wird gewarnt.",
    "Werkzeugverschleiß ist für das Modell unsichtbar und muss über Messungen erkannt werden.",
    "Vorhersagen ersetzen keine Stichprobenmessung, sie priorisieren sie.",
]


@dataclass
class TrainedModel:
    kind: str
    model: BaselineModel | HybridModel
    metrics: dict
    card: dict = field(default_factory=dict)

    def out_of_range(self, row: Mapping[str, float]) -> list[str]:
        return out_of_range(self.card, row)


def out_of_range(card: Mapping, row: Mapping[str, float]) -> list[str]:
    flagged = []
    for name, (low, high) in card.get("validity_range", {}).items():
        margin = 0.01 * (high - low)
        if name in row and not low - margin <= row[name] <= high + margin:
            flagged.append(name)
    return flagged


def train_model(
    kind: str,
    rows: Sequence[Mapping[str, float]],
    targets: Mapping[str, np.ndarray],
    seed: int = 0,
    holdout_fraction: float = 0.2,
    sources: Mapping | None = None,
) -> TrainedModel:
    """Evaluate on a random holdout split, then refit on all data."""
    if kind not in MODEL_KINDS:
        raise ValueError(f"Modelltyp '{kind}' ist unbekannt (erlaubt: {sorted(MODEL_KINDS)})")
    rows = list(rows)
    y = {t: np.asarray(targets[t], dtype=float) for t in TARGETS}
    n = len(rows)
    if n < 10:
        raise ValueError("Mindestens 10 Trainingsbeispiele nötig")

    rng = np.random.default_rng(seed)
    order = rng.permutation(n)
    n_holdout = max(2, int(round(holdout_fraction * n)))
    test_idx, train_idx = order[:n_holdout], order[n_holdout:]

    evaluator = MODEL_KINDS[kind](TARGETS, seed=seed)
    evaluator.fit([rows[i] for i in train_idx], {t: y[t][train_idx] for t in TARGETS})
    predicted = evaluator.predict([rows[i] for i in test_idx])
    metrics = {t: _metrics(y[t][test_idx], predicted[t]) for t in TARGETS}
    metrics["n_holdout"] = int(n_holdout)

    model = MODEL_KINDS[kind](TARGETS, seed=seed).fit(rows, y)
    card = _model_card(kind, model, rows, y, metrics, seed, sources or {})
    return TrainedModel(kind=kind, model=model, metrics=metrics, card=card)


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - actual
    ss_res = float(np.sum(error**2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2)) or 1e-12
    return {
        "rmse": float(np.sqrt(np.mean(error**2))),
        "mae": float(np.mean(np.abs(error))),
        "r2": 1.0 - ss_res / ss_tot,
    }


def data_hash(rows: Sequence[Mapping[str, float]], y: Mapping[str, np.ndarray]) -> str:
    payload = {
        "rows": [{k: round(float(v), 6) for k, v in sorted(row.items())} for row in rows],
        "y": {t: [round(float(v), 6) for v in y[t]] for t in sorted(y)},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _model_card(kind, model, rows, y, metrics, seed, sources) -> dict:
    validity = {
        name: [float(min(r[name] for r in rows)), float(max(r[name] for r in rows))]
        for name in VALIDITY_FEATURES
    }
    return {
        "kind": kind,
        **_CARD_TEXT[kind],
        "targets": {t: TARGET_LABELS[t] for t in TARGETS},
        "features": model.features_used(),
        "weights": model.coefficients(),
        "training": {
            "n_samples": len(rows),
            "data_hash": data_hash(rows, y),
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seed": seed,
            "sources": dict(sources),
        },
        "metrics": metrics,
        "validity_range": validity,
        "intended_use": (
            "Vorhersage von Mittendicke und Formabweichung jeder gepressten Linse, um Ausschuss "
            "früh zu erkennen und Stichprobenmessungen zu priorisieren."
        ),
        "limitations": list(LIMITATIONS),
    }


def serialize(model: BaselineModel | HybridModel) -> bytes:
    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    return buffer.getvalue()


def deserialize(blob: bytes) -> BaselineModel | HybridModel:
    """Only load artifacts this application created itself (joblib uses pickle)."""
    return joblib.load(io.BytesIO(blob))
