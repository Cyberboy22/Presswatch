"""From monitoring signals to a recommendation a person can act on.

The key idea: *which* signals fire together tells us the likely cause.

=====================  ======================  =====================================
Input data drift?      Model error drift?      Likely cause → what to do
=====================  ======================  =====================================
no                     no                      all fine
yes                    no                      a sensor or the material changed → check
yes                    yes                     a sensor is wrong → fix it, do NOT retrain
no                     yes                     the process changed (e.g. tool wear) →
                                               check the tool; retrain only if the
                                               change is meant to stay
=====================  ======================  =====================================

Retraining on data from a broken sensor would teach the model wrong physics, so
the system never retrains on its own: it proposes, a person approves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from presswatch.features import FEATURE_LABELS

SENSOR_FOR_FEATURE = {
    "dev_tool_upper_c": "Thermoelement am Oberwerkzeug",
    "dev_tool_lower_c": "Thermoelement am Unterwerkzeug",
    "dev_force_n": "Kraftsensor der Presse",
    "dev_force_duration_s": "Pressensteuerung (Presszeit)",
    "dev_furnace_c": "Ofenthermoelement bzw. Ofenregelung",
    "gob_mass_mg": "Waage bzw. Glasposten-Zufuhr",
}
TARGET_NAMES = {"pv_um": "Formabweichung", "center_thickness_mm": "Mittendicke"}

ERROR_LIMIT = 3
SCRAP_LIMIT = 0.2
OUT_OF_RANGE_LIMIT = 0.2


@dataclass
class Recommendation:
    status: str  # "ok" | "warnung" | "alarm"
    cause: str
    title: str
    explanation: str
    actions: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def recommend(
    data,
    performance: Mapping[str, object],
    recent_errors: int = 0,
    predicted_scrap_share: float = 0.0,
    out_of_range_share: float = 0.0,
) -> Recommendation:
    drifted = list(getattr(data, "drifted", []) or [])
    alarms = {
        target: report.direction
        for target, report in performance.items()
        if getattr(report, "alarm_index", None) is not None
    }
    evidence = [f"Daten-Drift: {FEATURE_LABELS.get(f, f)}" for f in drifted]
    evidence += [
        f"Fehler-Drift {TARGET_NAMES.get(t, t)}: Teile "
        + ("schlechter" if d == "up" else "besser")
        + " als vorhergesagt"
        for t, d in alarms.items()
    ]
    sensors = [SENSOR_FOR_FEATURE.get(f, f) for f in drifted]

    if recent_errors >= ERROR_LIMIT:
        return Recommendation(
            status="alarm",
            cause="datenfehler",
            title="Datenfehler an der Maschinenanbindung",
            explanation=(
                f"{recent_errors} der letzten Zyklen hatten Datenfehler. Diese Zyklen werden "
                "gespeichert, aber nicht bewertet."
            ),
            actions=[
                "Maschinenanbindung und Sensoren prüfen (Details unter Datenqualität).",
                "Betroffene Linsen messen, weil für sie keine Vorhersage existiert.",
            ],
            evidence=evidence + [f"{recent_errors} Zyklen mit Datenfehlern"],
        )

    if drifted and alarms:
        return Recommendation(
            status="alarm",
            cause="sensor",
            title="Sensorproblem wahrscheinlich",
            explanation=(
                "Eingangsdaten und Modellfehler haben sich gleichzeitig verändert. Das passt zu "
                "einem Sensor, der falsche Werte liefert: Das Modell rechnet mit falschen "
                "Eingängen, die Linsen selbst sind nicht unbedingt schlechter."
            ),
            actions=[f"{s} prüfen (Kalibrierung, Sitz, Kabel)." for s in sensors]
            + [
                "Nicht neu trainieren – das Modell würde die falschen Messwerte lernen.",
                "Nach der Reparatur die Überwachung zurücksetzen.",
            ],
            allowed_actions=["reset_monitoring"],
            evidence=evidence,
        )

    if drifted:
        return Recommendation(
            status="warnung",
            cause="eingangsdaten",
            title="Eingangsdaten haben sich verändert",
            explanation=(
                "Die Sensordaten weichen vom Referenzzeitraum ab, der Modellfehler ist "
                "(noch) unauffällig."
            ),
            actions=[f"{s} prüfen." for s in sensors]
            + ["Vorhersagen mit Vorsicht verwenden, bis die Ursache klar ist."],
            allowed_actions=["reset_monitoring"],
            evidence=evidence,
        )

    if alarms:
        worse = "up" in alarms.values()
        actions = (
            [
                "Werkzeug (Beschichtung) prüfen – Verschleiß erhöht die Formabweichung.",
                "Bleibt der Prozess dauerhaft so: Retraining-Vorschlag erstellen und prüfen.",
                "Nach einem Werkzeugwechsel: Rollback auf die Modellversion vor dem Verschleiß.",
            ]
            if worse
            else [
                "Wurde gerade das Werkzeug gewechselt? Dann Rollback auf die vorherige "
                "Modellversion.",
                "Sonst: Retraining-Vorschlag erstellen und prüfen.",
            ]
        )
        return Recommendation(
            status="alarm",
            cause="prozess",
            title="Der Prozess hat sich verändert",
            explanation=(
                "Die Eingangsdaten sind unauffällig, aber das Modell liegt systematisch daneben. "
                "Etwas, das kein Sensor sieht, hat sich verändert – typisch ist "
                "Werkzeugverschleiß."
            ),
            actions=actions,
            allowed_actions=["propose_retraining", "rollback", "reset_monitoring"],
            evidence=evidence,
        )

    if predicted_scrap_share >= SCRAP_LIMIT:
        return Recommendation(
            status="warnung",
            cause="ausschuss",
            title="Ausschuss erwartet",
            explanation=(
                f"{predicted_scrap_share:.0%} der letzten Linsen liegen laut Vorhersage "
                "außerhalb der Toleranz."
            ),
            actions=[
                "Betroffene Linsen aussortieren und messen.",
                "Rezeptparameter prüfen.",
            ],
            evidence=evidence,
        )

    if out_of_range_share >= OUT_OF_RANGE_LIMIT:
        return Recommendation(
            status="warnung",
            cause="extrapolation",
            title="Außerhalb des Trainingsbereichs",
            explanation=(
                "Viele Zyklen liegen außerhalb der Daten, mit denen das Modell trainiert wurde. "
                "Die Vorhersagen sind dort unsicher."
            ),
            actions=["Mehr Linsen messen und das Modell mit diesen Daten erweitern."],
            allowed_actions=["propose_retraining"],
            evidence=evidence,
        )

    warming_up = any(getattr(r, "state", "") == "einlaufen" for r in performance.values())
    return Recommendation(
        status="ok",
        cause="ok",
        title="Alles im grünen Bereich",
        explanation=(
            "Die Fehlerüberwachung sammelt noch Messungen für ihren Referenzwert."
            if warming_up
            else "Eingangsdaten und Modellfehler sind unauffällig."
        ),
        evidence=evidence,
    )
