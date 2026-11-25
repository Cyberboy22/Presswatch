"""End-to-end check of the monitoring chain on three full production runs."""

import pytest

from presswatch.experiments.scenarios import run_scenario


@pytest.fixture(scope="module")
def runs():
    return {
        name: run_scenario(name, n_cycles=900, seed=8)
        for name in ("normal", "werkzeugverschleiss", "sensorfehler")
    }


def test_healthy_production_raises_no_warning(runs):
    result = runs["normal"]
    assert result.first_warning_cycle is None
    assert result.first_scrap_cycle is None


def test_tool_wear_is_reported_as_a_process_change_before_scrap_starts(runs):
    result = runs["werkzeugverschleiss"]
    assert result.first_alarm_cause == "prozess"
    assert result.fault_start < result.first_alarm_cycle < result.first_scrap_cycle


def test_drifting_thermocouple_is_diagnosed_as_a_sensor_problem(runs):
    result = runs["sensorfehler"]
    assert result.first_warning_cause == "eingangsdaten"
    assert result.first_alarm_cause == "sensor"
    assert result.first_scrap_cycle is None  # the lenses themselves stay fine
