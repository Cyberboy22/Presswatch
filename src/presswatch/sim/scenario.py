"""Production line: recipes in batches, natural variation and injectable faults."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from presswatch.sim.process import CycleRecord, MachineState, ProcessParams, simulate_cycle

# Three recipes for the same lens (nominal center thickness 2.00 mm).
# "schnell" trades a shorter press time for a slightly higher form deviation.
RECIPES: dict[str, ProcessParams] = {
    "standard": ProcessParams(
        heating_time_s=170.0,
        press_time_s=10.0,
        press_force_n=1000.0,
        temp_upper_c=500.0,
        temp_lower_c=500.0,
    ),
    "schnell": ProcessParams(
        heating_time_s=158.0,
        press_time_s=6.0,
        press_force_n=1430.0,
        temp_upper_c=510.0,
        temp_lower_c=510.0,
    ),
    "schonend": ProcessParams(
        heating_time_s=180.0,
        press_time_s=12.0,
        press_force_n=800.0,
        temp_upper_c=490.0,
        temp_lower_c=490.0,
    ),
}


@dataclass
class FaultSettings:
    """Faults that can be switched on while the line is running (demo control)."""

    tool_wear_rate_um_per_cycle: float = 0.0
    sensor_offset_target_c: float = 0.0
    sensor_offset_ramp_c_per_cycle: float = 0.25


@dataclass
class ProductionLine:
    seed: int = 0
    batch_size: int = 40
    start_cycle_id: int = 0  # a real machine keeps counting after a restart
    noise: bool = True
    recipes: dict[str, ProcessParams] = field(default_factory=lambda: dict(RECIPES))
    state: MachineState = field(default_factory=MachineState)
    faults: FaultSettings = field(default_factory=FaultSettings)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)
        self._cycle_id = self.start_cycle_id
        self._recipe_names = list(self.recipes)

    @property
    def cycle_count(self) -> int:
        return self._cycle_id

    def current_recipe(self) -> str:
        batch = self._cycle_id // self.batch_size
        return self._recipe_names[batch % len(self._recipe_names)]

    def next_cycle(self) -> CycleRecord:
        """Advance the machine by one cycle and return what happened."""
        recipe = self.current_recipe()
        self._apply_faults()
        self._cycle_id += 1
        return self._simulate(recipe, self._cycle_id)

    def run_recipe(self, name: str) -> CycleRecord:
        """Run a single cycle with a given recipe without advancing the schedule."""
        return self._simulate(name, self._cycle_id)

    def maintenance(self) -> None:
        """Replace the worn tool coating."""
        self.state.tool_wear_um = 0.0

    def _apply_faults(self) -> None:
        self.state.tool_wear_um += self.faults.tool_wear_rate_um_per_cycle
        offset, target = self.state.sensor_offset_upper_c, self.faults.sensor_offset_target_c
        step = self.faults.sensor_offset_ramp_c_per_cycle
        if offset < target:
            self.state.sensor_offset_upper_c = min(offset + step, target)
        elif offset > target:
            self.state.sensor_offset_upper_c = max(offset - step, target)

    def _simulate(self, recipe: str, cycle_id: int) -> CycleRecord:
        return simulate_cycle(
            self.recipes[recipe],
            state=self.state,
            rng=self._rng if self.noise else None,
            cycle_id=cycle_id,
            recipe=recipe,
        )
