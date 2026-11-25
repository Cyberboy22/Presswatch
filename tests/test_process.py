"""Tests for the simplified non-isothermal glass molding (NGM) process model."""

import numpy as np
import pytest

from presswatch.sim.process import (
    PARAM_BOUNDS,
    SPEC,
    MachineState,
    ProcessParams,
    measure,
    simulate_cycle,
)

NOMINAL = ProcessParams(
    heating_time_s=170.0,
    press_time_s=10.0,
    press_force_n=1000.0,
    temp_upper_c=500.0,
    temp_lower_c=500.0,
)


def _with(**changes) -> ProcessParams:
    values = NOMINAL.as_dict() | changes
    return ProcessParams(**values)


def _noise_free(params: ProcessParams, state: MachineState | None = None):
    """Simulate without random variation so that physical trends are visible."""
    return simulate_cycle(params, state=state, rng=None)


def test_same_seed_gives_identical_cycle():
    a = simulate_cycle(NOMINAL, rng=np.random.default_rng(7))
    b = simulate_cycle(NOMINAL, rng=np.random.default_rng(7))
    assert a.truth == b.truth
    assert a.curves.force_n == b.curves.force_n


def test_more_force_gives_thinner_lens():
    soft = _noise_free(_with(press_force_n=700.0)).truth.center_thickness_mm
    hard = _noise_free(_with(press_force_n=1600.0)).truth.center_thickness_mm
    assert hard < soft


def test_longer_heating_gives_hotter_glass_and_thinner_lens():
    short = _noise_free(_with(heating_time_s=150.0))
    long = _noise_free(_with(heating_time_s=210.0))
    assert long.internals["glass_temp_start_c"] > short.internals["glass_temp_start_c"]
    assert long.truth.center_thickness_mm < short.truth.center_thickness_mm


def test_tool_temperature_asymmetry_increases_form_deviation():
    symmetric = _noise_free(_with(temp_upper_c=500.0, temp_lower_c=500.0)).truth.pv_um
    asymmetric = _noise_free(_with(temp_upper_c=540.0, temp_lower_c=470.0)).truth.pv_um
    assert asymmetric > symmetric + 2.0


def test_too_short_pressing_releases_soft_glass_and_increases_form_deviation():
    short = _noise_free(_with(press_time_s=4.0)).truth.pv_um
    normal = _noise_free(_with(press_time_s=10.0)).truth.pv_um
    assert short > normal


def test_tool_wear_raises_form_deviation_but_leaves_sensors_unchanged():
    fresh = _noise_free(NOMINAL, MachineState())
    worn = _noise_free(NOMINAL, MachineState(tool_wear_um=3.0))
    assert worn.truth.pv_um == pytest.approx(fresh.truth.pv_um + 3.0)
    assert worn.curves == fresh.curves


def test_sensor_offset_changes_the_reading_but_not_the_lens():
    healthy = _noise_free(NOMINAL, MachineState())
    faulty = _noise_free(NOMINAL, MachineState(sensor_offset_upper_c=20.0))
    assert faulty.truth == healthy.truth
    reading_shift = np.mean(faulty.curves.tool_temp_upper_c) - np.mean(
        healthy.curves.tool_temp_upper_c
    )
    assert reading_shift == pytest.approx(20.0)
    assert faulty.curves.tool_temp_lower_c == healthy.curves.tool_temp_lower_c


def test_curves_match_phase_durations_and_force_setpoint():
    cycle = _noise_free(NOMINAL)
    curves = cycle.curves
    assert len(curves.furnace_temp_c) == int(NOMINAL.heating_time_s * curves.heating_rate_hz)
    n_press = int(NOMINAL.press_time_s * curves.pressing_rate_hz)
    assert len(curves.force_n) == n_press
    assert len(curves.tool_temp_upper_c) == n_press
    assert max(curves.force_n) == pytest.approx(NOMINAL.press_force_n, rel=0.02)


def test_parameters_outside_bounds_are_rejected():
    low, _ = PARAM_BOUNDS["press_force_n"]
    with pytest.raises(ValueError, match="press_force_n"):
        _with(press_force_n=low - 1.0).validate()


def test_nominal_recipe_is_mostly_within_specification():
    rng = np.random.default_rng(1)
    cycles = [simulate_cycle(NOMINAL, rng=rng) for _ in range(200)]
    in_spec = [SPEC.is_ok(c.truth) for c in cycles]
    assert np.mean(in_spec) > 0.9


def test_measurement_adds_small_gauge_noise():
    cycle = simulate_cycle(NOMINAL, rng=np.random.default_rng(3))
    measured = measure(cycle.truth, np.random.default_rng(4))
    assert measured != cycle.truth
    assert abs(measured.center_thickness_mm - cycle.truth.center_thickness_mm) < 0.01
    assert abs(measured.pv_um - cycle.truth.pv_um) < 1.0
