"""Tests for feature extraction and data quality checks."""

import math

import numpy as np
import pytest

from presswatch.features import DRIFT_FEATURES, FEATURE_NAMES, extract_features, features_of
from presswatch.quality import check_cycle
from presswatch.sim.process import MachineState, simulate_cycle
from presswatch.sim.scenario import RECIPES

STANDARD = RECIPES["standard"]


def _cycle(state=None, seed=0):
    return simulate_cycle(STANDARD, state=state, rng=np.random.default_rng(seed))


def test_all_feature_names_are_present_and_finite():
    features = features_of(_cycle())
    assert set(FEATURE_NAMES) | set(DRIFT_FEATURES) <= set(features)
    assert all(math.isfinite(v) for v in features.values())


def test_force_features_match_the_set_point():
    features = features_of(_cycle())
    assert features["force_max_n"] == pytest.approx(STANDARD.press_force_n, rel=0.02)
    assert features["force_duration_s"] == pytest.approx(STANDARD.press_time_s, abs=0.3)


def test_deviation_features_are_close_to_zero_on_a_healthy_machine():
    features = features_of(_cycle())
    assert abs(features["dev_tool_upper_c"]) < 5.0
    assert abs(features["dev_force_n"]) < 30.0


def test_sensor_offset_shows_up_in_the_deviation_feature():
    healthy = features_of(_cycle(MachineState(), seed=1))
    faulty = features_of(_cycle(MachineState(sensor_offset_upper_c=15.0), seed=1))
    assert faulty["dev_tool_upper_c"] - healthy["dev_tool_upper_c"] == pytest.approx(15.0, abs=0.01)


def test_extract_features_accepts_plain_dicts_like_the_api_receives_them():
    cycle = _cycle()
    from_dicts = extract_features(cycle.params.as_dict(), cycle.gob_mass_mg, cycle.curves.as_dict())
    assert from_dicts == features_of(cycle)


def _payload(cycle):
    return cycle.params.as_dict(), cycle.gob_mass_mg, cycle.curves.as_dict()


def test_healthy_cycle_has_no_quality_issues():
    assert check_cycle(*_payload(_cycle())) == []


def test_setpoint_deviation_of_upper_tool_is_a_warning():
    issues = check_cycle(*_payload(_cycle(MachineState(sensor_offset_upper_c=20.0))))
    assert [i.code for i in issues] == ["SETPOINT_DEVIATION"]
    assert issues[0].severity == "warning"
    assert "Oberwerkzeug" in issues[0].message


def test_missing_values_are_an_error():
    params, mass, curves = _payload(_cycle())
    curves["force_n"][5] = float("nan")
    codes = {i.code: i.severity for i in check_cycle(params, mass, curves)}
    assert codes["NAN_VALUES"] == "error"


def test_null_values_from_json_count_as_missing():
    params, mass, curves = _payload(_cycle())
    curves["tool_temp_upper_c"][0] = None
    codes = {i.code: i.severity for i in check_cycle(params, mass, curves)}
    assert codes["NAN_VALUES"] == "error"


def test_curve_that_does_not_match_the_press_time_is_an_error():
    params, mass, curves = _payload(_cycle())
    curves["force_n"] = curves["force_n"][:20]
    codes = {i.code for i in check_cycle(params, mass, curves)}
    assert "CURVE_LENGTH" in codes


def test_frozen_sensor_is_detected():
    params, mass, curves = _payload(_cycle())
    curves["tool_temp_lower_c"] = [500.0] * len(curves["tool_temp_lower_c"])
    codes = {i.code for i in check_cycle(params, mass, curves)}
    assert "SENSOR_FROZEN" in codes


def test_physically_impossible_temperature_is_an_error():
    params, mass, curves = _payload(_cycle())
    curves["furnace_temp_c"][0] = 2000.0
    codes = {i.code: i.severity for i in check_cycle(params, mass, curves)}
    assert codes["SENSOR_RANGE"] == "error"


def test_parameters_outside_the_process_window_are_an_error():
    params, mass, curves = _payload(_cycle())
    params["press_force_n"] = 5000.0
    codes = {i.code: i.severity for i in check_cycle(params, mass, curves)}
    assert codes["PARAM_OUT_OF_RANGE"] == "error"
