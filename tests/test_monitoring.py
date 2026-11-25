"""Tests for data drift, performance drift (CUSUM) and the recommendation logic."""

import numpy as np
import pytest

from presswatch.features import features_of
from presswatch.monitoring.decision import recommend
from presswatch.monitoring.drift import data_drift, psi
from presswatch.monitoring.performance import performance_drift
from presswatch.sim.scenario import ProductionLine


def _rows(line, n):
    rows = []
    for _ in range(n):
        cycle = line.next_cycle()
        rows.append(features_of(cycle) | {"recipe": cycle.recipe})
    return rows


# --- PSI -----------------------------------------------------------------


def test_psi_is_close_to_zero_for_the_same_distribution():
    rng = np.random.default_rng(0)
    assert psi(rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)) < 0.02


def test_psi_is_large_for_a_shifted_distribution():
    rng = np.random.default_rng(0)
    assert psi(rng.normal(0, 1, 500), rng.normal(1.5, 1, 500)) > 0.25


def test_psi_stays_small_for_small_windows_without_change():
    # 100 vs 150 samples from the same distribution: PSI must not look like drift
    rng = np.random.default_rng(5)
    values = [psi(rng.normal(0, 1, 150), rng.normal(0, 1, 100)) for _ in range(200)]
    assert np.percentile(values, 95) < 0.25


def test_psi_of_a_constant_feature_is_zero():
    assert psi(np.zeros(100), np.zeros(100)) == 0.0


# --- data drift ----------------------------------------------------------


def test_healthy_production_with_recipe_changes_raises_no_data_drift():
    line = ProductionLine(seed=11)
    reference = _rows(line, 150)
    for _ in range(8):
        report = data_drift(reference, _rows(line, 100))
        assert report.drifted == [], report.drifted


def test_faulty_upper_thermocouple_is_found_as_the_only_drifted_feature():
    line = ProductionLine(seed=12)
    reference = _rows(line, 150)
    line.faults.sensor_offset_target_c = 4.0
    line.faults.sensor_offset_ramp_c_per_cycle = 4.0  # jump instead of a slow ramp
    report = data_drift(reference, _rows(line, 100))
    assert report.drifted == ["dev_tool_upper_c"]


def test_too_few_cycles_give_no_verdict():
    line = ProductionLine(seed=13)
    report = data_drift(_rows(line, 150), _rows(line, 10))
    assert not report.enough_data
    assert report.drifted == []


# --- performance drift (CUSUM on residuals) -------------------------------


def _residuals(n, shift_at=None, shift=0.0, seed=0, bias=None):
    rng = np.random.default_rng(seed)
    recipes = [("a", "b", "c")[(i // 8) % 3] for i in range(n)]
    bias = bias or {"a": 0.0, "b": 0.0, "c": 0.0}
    res = [bias[r] + rng.normal(0, 0.3) for r in recipes]
    if shift_at is not None:
        res = [v + (shift if i >= shift_at else 0.0) for i, v in enumerate(res)]
    return res, recipes


def test_stationary_residuals_raise_no_alarm():
    res, recipes = _residuals(400, seed=1)
    report = performance_drift(res, recipes, min_sigma=0.15)
    assert report.alarm_index is None


def test_constant_bias_per_recipe_raises_no_alarm():
    res, recipes = _residuals(400, seed=2, bias={"a": -0.2, "b": 0.3, "c": -0.15})
    report = performance_drift(res, recipes, min_sigma=0.15)
    assert report.alarm_index is None


def test_upward_shift_is_detected_quickly_and_in_the_right_direction():
    res, recipes = _residuals(200, shift_at=100, shift=0.4, seed=3)
    report = performance_drift(res, recipes, min_sigma=0.15)
    assert report.direction == "up"
    assert 100 <= report.alarm_index < 130


def test_downward_shift_is_reported_as_down():
    res, recipes = _residuals(200, shift_at=100, shift=-0.6, seed=4)
    assert performance_drift(res, recipes, min_sigma=0.15).direction == "down"


def test_shift_inside_the_baseline_phase_is_not_judged_yet():
    res, recipes = _residuals(20, seed=5)
    report = performance_drift(res, recipes, min_sigma=0.15, baseline_n=30)
    assert report.state == "einlaufen"
    assert report.alarm_index is None


# --- recommendation --------------------------------------------------------


def _drift(features):
    class Report:
        enough_data = True
        drifted = features

    return Report()


def _perf(direction):
    class Report:
        state = "aktiv"
        alarm_index = None if direction is None else 5

    report = Report()
    report.direction = direction
    return report


def test_everything_normal_means_ok():
    rec = recommend(data=_drift([]), performance={"pv_um": _perf(None)})
    assert rec.status == "ok"


def test_model_error_drift_with_normal_inputs_points_to_the_process_and_allows_retraining():
    rec = recommend(data=_drift([]), performance={"pv_um": _perf("up")})
    assert rec.status == "alarm"
    assert rec.cause == "prozess"
    assert "propose_retraining" in rec.allowed_actions
    assert any("Werkzeug" in action for action in rec.actions)


def test_input_drift_together_with_error_drift_points_to_a_sensor_and_forbids_retraining():
    rec = recommend(data=_drift(["dev_tool_upper_c"]), performance={"pv_um": _perf("down")})
    assert rec.status == "alarm"
    assert rec.cause == "sensor"
    assert "propose_retraining" not in rec.allowed_actions
    assert any("Oberwerkzeug" in action for action in rec.actions)


def test_input_drift_alone_is_a_warning():
    rec = recommend(data=_drift(["gob_mass_mg"]), performance={"pv_um": _perf(None)})
    assert rec.status == "warnung"
    assert rec.cause == "eingangsdaten"


def test_repeated_data_errors_beat_everything_else():
    rec = recommend(data=_drift([]), performance={"pv_um": _perf("up")}, recent_errors=4)
    assert rec.cause == "datenfehler"
    assert "propose_retraining" not in rec.allowed_actions


def test_predicted_scrap_is_a_warning_for_the_operator():
    rec = recommend(data=_drift([]), performance={}, predicted_scrap_share=0.3)
    assert rec.status == "warnung"
    assert rec.cause == "ausschuss"


@pytest.mark.parametrize("share", [0.0, 0.1])
def test_little_predicted_scrap_is_ok(share):
    rec = recommend(data=_drift([]), performance={}, predicted_scrap_share=share)
    assert rec.status == "ok"
