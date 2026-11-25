"""Tests for the static Netlify site: a recorded run that the browser replays."""

import json

import pytest

from presswatch.static_site import build_site


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    build_site(out, frame_every=10)
    return out


@pytest.fixture(scope="module")
def recording(site):
    return json.loads((site / "data" / "recording.json").read_text(encoding="utf-8"))


def test_site_contains_pages_assets_and_netlify_files(site):
    for name in ("index.html", "werker.html", "_headers", "_redirects", "site-config.js"):
        assert (site / name).is_file(), name
    for name in ("app.js", "charts.js", "styles.css", "replay.js", "vendor/archivo-wdth.woff2"):
        assert (site / "static" / name).is_file(), name


def test_pages_load_the_replay_before_the_dashboard(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert html.index("/static/replay.js") < html.index("/static/app.js")
    assert "/werker /werker.html 200" in (site / "_redirects").read_text(encoding="utf-8")


def test_frames_move_forward_through_the_whole_run(recording):
    cycles = [frame["cycle"] for frame in recording["frames"]]
    assert cycles == sorted(cycles)
    assert cycles[-1] == recording["meta"]["n_cycles"]
    assert len(recording["cycles"]) == recording["meta"]["n_cycles"]


def test_the_story_contains_every_step_and_the_detected_alarms(recording):
    titles = [c["title"] for c in recording["chapters"]]
    for expected in (
        "Einlaufen",
        "Werkzeugverschleiß beginnt",
        "Alarm: Der Prozess hat sich verändert",
        "Retraining vorgeschlagen",
        "hybrid-v2 freigegeben",
        "Werkzeug gewechselt",
        "Rollback auf hybrid-v1",
        "Thermoelement driftet",
        "Alarm: Sensorproblem wahrscheinlich",
    ):
        assert expected in titles, f"{expected} fehlt in {titles}"
    cycles = [c["cycle"] for c in recording["chapters"]]
    assert cycles == sorted(cycles)


def test_detected_alarm_follows_its_cause(recording):
    at = {c["title"]: c["cycle"] for c in recording["chapters"]}
    assert at["Werkzeugverschleiß beginnt"] < at["Alarm: Der Prozess hat sich verändert"]
    assert at["Thermoelement driftet"] < at["Alarm: Sensorproblem wahrscheinlich"]


def test_model_and_audit_snapshots_change_only_when_something_happens(recording):
    assert len(recording["models"]) < len(recording["frames"])
    final_models = recording["models"][recording["frames"][-1]["models"]]
    assert {m["version"] for m in final_models} >= {"hybrid-v1", "hybrid-v2"}
    final_audit = recording["audits"][recording["frames"][-1]["audit"]]
    actions = {entry["action"] for entry in final_audit}
    assert {"retraining_proposed", "model_activated", "model_rollback"} <= actions
    assert {"sim_tool_wear", "sim_sensor_fault", "monitoring_reset"} <= actions


def test_every_cycle_has_a_detail_file_with_curves(site, recording):
    first, last = recording["cycles"][0]["cycle_id"], recording["cycles"][-1]["cycle_id"]
    for cycle_id in (first, last):
        detail = json.loads((site / "data" / "zyklus" / f"{cycle_id}.json").read_text("utf-8"))
        assert len(detail["curves"]["force_n"]) > 10


def test_recording_stays_small_enough_for_a_quick_page_load(site):
    assert (site / "data" / "recording.json").stat().st_size < 6_000_000
