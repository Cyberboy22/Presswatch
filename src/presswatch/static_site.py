"""Static site for Netlify: record a real PressWatch run, replay it in the browser.

Netlify serves static files only, so the Python services cannot run there. Instead
``presswatch record`` plays a scripted story against the real API (same code as
the live app), records what the dashboard sees every few cycles and writes a
folder that can be dropped onto https://app.netlify.com/drop. In the browser,
``static/replay.js`` answers the dashboard's ``/api/*`` requests from that
recording, with a player and chapters.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from presswatch import __version__
from presswatch.api import services
from presswatch.api.app import STATIC_DIR, create_app
from presswatch.config import Settings
from presswatch.payloads import cycle_payload, measurement_payload
from presswatch.sim.process import measure
from presswatch.sim.scenario import ProductionLine

ACTOR = "Arif"
SAMPLE_EVERY = 2
MEASUREMENT_DELAY = 2
N_CYCLES = 960


@dataclass
class Story:
    """What happens during the recording and who does it."""

    client: TestClient
    app: object
    line: ProductionLine
    proposed_version: str | None = None

    def sim(self, action: str, **details) -> None:
        with self.app.state.session_factory() as session:
            services.audit(session, "Simulator", f"sim_{action}", **details)
            session.commit()

    def post(self, path: str, reason: str) -> dict:
        response = self.client.post(path, json={"actor": ACTOR, "reason": reason})
        if response.status_code >= 400:
            raise RuntimeError(f"{path}: {response.status_code} {response.text}")
        return response.json()


def _start(story: Story) -> str | None:
    return None


def _wear_on(story: Story) -> str | None:
    story.line.faults.tool_wear_rate_um_per_cycle = 0.03
    story.sim("tool_wear", value=0.03)
    return None


def _wear_stable(story: Story) -> str | None:
    story.line.faults.tool_wear_rate_um_per_cycle = 0.0
    story.sim("tool_wear", value=0.0)
    return None


def _propose(story: Story) -> str:
    result = story.post(
        "/api/models/proposals", "Verschleißalarm: Modell an das abgenutzte Werkzeug anpassen"
    )
    story.proposed_version = result["version"]
    cmp = result["metrics"]["comparison"]
    candidate = f"{cmp['candidate']['pv_um']['rmse']:.2f}".replace(".", ",")
    active = f"{cmp['active']['pv_um']['rmse']:.2f}".replace(".", ",")
    return (
        f"Arif lässt einen Kandidaten trainieren: {result['version']}. Auf den {cmp['n']} neuesten "
        f"Linsen liegt er bei der Formabweichung {candidate} µm daneben, das aktive Modell "
        f"{active} µm. Aktiv wird er erst nach einer Freigabe."
    )


def _approve(story: Story) -> str | None:
    story.post(
        f"/api/models/{story.proposed_version}/activate",
        "Kandidat auf den neuesten Linsen klar besser, Werkzeugwechsel erst später möglich",
    )
    return None


def _maintenance(story: Story) -> str | None:
    story.line.maintenance()
    story.sim("maintenance")
    return None


def _rollback(story: Story) -> str | None:
    story.post("/api/models/rollback", "Neues Werkzeug: das ursprüngliche Modell passt wieder")
    return None


def _sensor_fault(story: Story) -> str | None:
    story.line.faults.sensor_offset_target_c = 20.0
    story.line.faults.sensor_offset_ramp_c_per_cycle = 0.25
    story.sim("sensor_fault", value=20.0)
    return None


def _repair(story: Story) -> str | None:
    story.line.faults.sensor_offset_target_c = 0.0
    story.line.faults.sensor_offset_ramp_c_per_cycle = 100.0
    story.sim("reset_faults")
    story.post("/api/monitoring/reset", "Thermoelement am Oberwerkzeug getauscht")
    return None


@dataclass(frozen=True)
class Event:
    cycle: int
    title: str
    text: str
    action: Callable[[Story], str | None]


def _story_events() -> list[Event]:
    return [
        Event(
            1,
            "Einlaufen",
            "Die Presse fertigt Linsen mit drei Rezepten, jede zweite wird gemessen. PressWatch "
            "sammelt eine Referenz: 150 Zyklen für die Sensordaten und 60 gemessene Linsen für "
            "den Modellfehler.",
            _start,
        ),
        Event(
            240,
            "Werkzeugverschleiß beginnt",
            "Die Beschichtung des Werkzeugs nutzt sich ab. Die Linsen werden schlechter, aber "
            "kein Sensor merkt es. Das Modell sagt weiter gute Linsen vorher.",
            _wear_on,
        ),
        Event(
            330,
            "Arif prüft das Werkzeug",
            "Die Beschichtung ist sichtbar abgenutzt, ein Ersatz kommt erst später. Bis dahin "
            "bleibt der Verschleiß auf diesem Stand (im Simulator vereinfacht).",
            _wear_stable,
        ),
        Event(360, "Retraining vorgeschlagen", "", _propose),
        Event(
            366,
            "hybrid-v2 freigegeben",
            "Arif gibt den Kandidaten frei. Ab jetzt rechnet PressWatch mit dem abgenutzten "
            "Werkzeug, und die Überwachung sammelt eine neue Referenz.",
            _approve,
        ),
        Event(
            560,
            "Werkzeug gewechselt",
            "Neue Beschichtung. Die Linsen sind jetzt besser, als hybrid-v2 erwartet, denn das "
            "Modell hat den Verschleiß mitgelernt.",
            _maintenance,
        ),
        Event(
            600,
            "Rollback auf hybrid-v1",
            "Das ursprüngliche Modell passt wieder zum neuen Werkzeug. Arif aktiviert es erneut, "
            "die Überwachung läuft neu ein.",
            _rollback,
        ),
        Event(
            760,
            "Thermoelement driftet",
            "Das Thermoelement am Oberwerkzeug zeigt nach und nach bis zu 20 °C zu viel an. Die "
            "Linsen selbst bleiben gut, aber das Modell rechnet mit falschen Eingängen.",
            _sensor_fault,
        ),
        Event(
            900,
            "Sensor getauscht",
            "Arif tauscht das Thermoelement und setzt die Überwachung zurück. PressWatch "
            "sammelt eine neue Referenz.",
            _repair,
        ),
    ]


def _detected_title(status: dict) -> str:
    rec = status["recommendation"]
    directions = {
        report.get("direction")
        for report in status["performance"].values()
        if report.get("alarm_cycle_id") is not None
    }
    if rec["cause"] == "prozess" and directions == {"down"}:
        return "Alarm: Linsen besser als vorhergesagt"
    prefix = "Alarm" if rec["status"] == "alarm" else "Warnung"
    return f"{prefix}: {rec['title']}"


def _sim_state(line: ProductionLine, recipe: str) -> dict:
    return {
        "tool_wear_rate_um_per_cycle": line.faults.tool_wear_rate_um_per_cycle,
        "sensor_offset_target_c": line.faults.sensor_offset_target_c,
        "maintenance_requested": False,
        "tool_wear_um": round(line.state.tool_wear_um, 4),
        "sensor_offset_upper_c": round(line.state.sensor_offset_upper_c, 3),
        "cycle": line.cycle_count,
        "recipe": recipe,
    }


class _Snapshots:
    """Store a list only when it changed; frames point to an index."""

    def __init__(self) -> None:
        self.items: list = []
        self._last_hash: str | None = None

    def add(self, value) -> int:
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        if digest != self._last_hash:
            self.items.append(value)
            self._last_hash = digest
        return len(self.items) - 1


def record(n_cycles: int = N_CYCLES, frame_every: int = 5, seed: int = 7) -> tuple[dict, dict]:
    """Play the story against the real API. Returns (recording, cycle details)."""
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sim_control=False,
        sample_every=SAMPLE_EVERY,
        measurement_delay_cycles=MEASUREMENT_DELAY,
    )
    app = create_app(settings)
    events = {event.cycle: event for event in _story_events()}
    chapters, frames = [], []
    models, audits = _Snapshots(), _Snapshots()
    with TestClient(app) as client:
        story = Story(client=client, app=app, line=ProductionLine(seed=seed))
        gauge = np.random.default_rng(seed + 1)
        pending = []
        last_state = ("ok", "ok")
        for i in range(1, n_cycles + 1):
            event = events.get(i)
            if event:
                text = event.action(story) or event.text
                chapters.append({"cycle": i, "title": event.title, "text": text, "kind": "event"})

            cycle = story.line.next_cycle()
            client.post("/api/cycles", json=cycle_payload(cycle))
            if cycle.cycle_id % SAMPLE_EVERY == 0:
                pending.append((cycle.cycle_id + MEASUREMENT_DELAY, cycle))
            for _, done in [p for p in pending if p[0] <= i]:
                client.post(
                    "/api/measurements",
                    json=measurement_payload(done.cycle_id, measure(done.truth, gauge)),
                )
            pending = [p for p in pending if p[0] > i]

            if i % frame_every and i != n_cycles and not event:
                continue
            status = client.get("/api/status").json()
            rec = status["recommendation"]
            state = (rec["status"], _detected_title(status))
            if rec["status"] != "ok" and state != last_state:
                chapters.append(
                    {"cycle": i, "title": state[1], "text": rec["explanation"], "kind": "detected"}
                )
            last_state = state
            frames.append(
                {
                    "cycle": i,
                    "status": status,
                    "sim": _sim_state(story.line, cycle.recipe),
                    "models": models.add(client.get("/api/models").json()),
                    "audit": audits.add(client.get("/api/audit", params={"limit": 200}).json()),
                }
            )

        cycles = client.get("/api/cycles", params={"limit": 5000}).json()
        details = {c["cycle_id"]: client.get(f"/api/cycles/{c['cycle_id']}").json() for c in cycles}

    recording = {
        "meta": {
            "version": __version__,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_cycles": n_cycles,
            "frame_every": frame_every,
            "sample_every": SAMPLE_EVERY,
            "measurement_delay": MEASUREMENT_DELAY,
            "seed": seed,
        },
        "chapters": chapters,
        "cycles": cycles,
        "frames": frames,
        "models": models.items,
        "audits": audits.items,
    }
    return recording, details


HEADERS = """/*
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: same-origin

/static/*
  Cache-Control: public, max-age=3600

/data/*
  Cache-Control: public, max-age=3600
"""

SITE_CONFIG = """// Einstellungen der statischen PressWatch-Seite.
// Trag hier den Link zu deinem GitHub-Repository ein, dann erscheint er auf der Seite.
// Beispiel: repoUrl: "https://github.com/Cyberboy22/presswatch"
window.PRESSWATCH_SITE = {
  repoUrl: "",
};
"""


def _inject(html: str, marker: str, scripts: str) -> str:
    if marker not in html:
        raise ValueError(f"Marker {marker!r} nicht gefunden")
    return html.replace(marker, f"{scripts}\n  {marker}", 1)


def build_site(out: Path, n_cycles: int = N_CYCLES, frame_every: int = 5, seed: int = 7) -> Path:
    out = Path(out)
    recording, details = record(n_cycles=n_cycles, frame_every=frame_every, seed=seed)

    if (out / "static").exists():
        shutil.rmtree(out / "static")
    shutil.copytree(STATIC_DIR, out / "static")
    data = out / "data"
    (data / "zyklus").mkdir(parents=True, exist_ok=True)
    compact = {"separators": (",", ":"), "ensure_ascii": False}
    (data / "recording.json").write_text(json.dumps(recording, **compact), encoding="utf-8")
    for cycle_id, detail in details.items():
        (data / "zyklus" / f"{cycle_id}.json").write_text(
            json.dumps(detail, **compact), encoding="utf-8"
        )

    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    index = index.replace(
        "<title>PressWatch – Glaspresse NGM-1</title>",
        "<title>PressWatch – Aufzeichnung einer Demo</title>",
    )
    index = _inject(
        index,
        '<script type="module" src="/static/app.js"></script>',
        '<script src="/site-config.js"></script>\n  <script src="/static/replay.js"></script>',
    )
    (out / "index.html").write_text(index, encoding="utf-8")

    werker = (STATIC_DIR / "werker.html").read_text(encoding="utf-8")
    werker = _inject(
        werker,
        '<script type="module">',
        '<script src="/site-config.js"></script>\n  '
        '<script src="/static/replay.js" data-mode="auto"></script>',
    )
    (out / "werker.html").write_text(werker, encoding="utf-8")

    (out / "site-config.js").write_text(SITE_CONFIG, encoding="utf-8")
    (out / "_headers").write_text(HEADERS, encoding="utf-8")
    (out / "_redirects").write_text("/werker /werker.html 200\n", encoding="utf-8")
    return out
