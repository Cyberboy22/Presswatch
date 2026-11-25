"""Tests for the production line (recipes, faults, maintenance) and the DoE."""

import numpy as np
import pytest

from presswatch.sim.doe import latin_hypercube
from presswatch.sim.process import PARAM_BOUNDS, SPEC
from presswatch.sim.scenario import RECIPES, ProductionLine


def test_every_recipe_produces_the_nominal_center_thickness():
    line = ProductionLine(seed=0, noise=False)
    for name in RECIPES:
        cycle = line.run_recipe(name)
        assert cycle.truth.center_thickness_mm == pytest.approx(SPEC.ct_nominal_mm, abs=0.01)


def test_recipes_run_in_batches_and_rotate():
    line = ProductionLine(seed=0, batch_size=3)
    recipes = [line.next_cycle().recipe for _ in range(9)]
    assert recipes[0:3] == [recipes[0]] * 3
    assert len(set(recipes)) == 3


def test_cycle_ids_count_up_from_one():
    line = ProductionLine(seed=0)
    assert [line.next_cycle().cycle_id for _ in range(3)] == [1, 2, 3]


def test_tool_wear_accumulates_while_wear_rate_is_set():
    line = ProductionLine(seed=0)
    line.faults.tool_wear_rate_um_per_cycle = 0.02
    for _ in range(50):
        line.next_cycle()
    assert line.state.tool_wear_um == pytest.approx(1.0)


def test_maintenance_resets_tool_wear():
    line = ProductionLine(seed=0)
    line.state.tool_wear_um = 2.5
    line.maintenance()
    assert line.state.tool_wear_um == 0.0


def test_sensor_offset_ramps_towards_target_and_stops_there():
    line = ProductionLine(seed=0)
    line.faults.sensor_offset_target_c = 1.0
    line.faults.sensor_offset_ramp_c_per_cycle = 0.25
    offsets = []
    for _ in range(6):
        line.next_cycle()
        offsets.append(line.state.sensor_offset_upper_c)
    assert offsets == pytest.approx([0.25, 0.5, 0.75, 1.0, 1.0, 1.0])


def test_same_seed_gives_same_production_run():
    a = [ProductionLine(seed=5).next_cycle().truth for _ in range(1)]
    b = [ProductionLine(seed=5).next_cycle().truth for _ in range(1)]
    assert a == b


def test_latin_hypercube_stays_in_bounds_and_hits_every_stratum_once():
    n = 20
    samples = latin_hypercube(n, seed=3)
    assert len(samples) == n
    for name, (low, high) in PARAM_BOUNDS.items():
        values = np.array([getattr(s, name) for s in samples])
        assert np.all((values >= low) & (values <= high))
        strata = np.floor((values - low) / (high - low) * n).clip(0, n - 1)
        assert sorted(strata.astype(int)) == list(range(n))
