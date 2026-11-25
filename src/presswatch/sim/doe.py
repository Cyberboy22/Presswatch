"""Design of experiments: Latin Hypercube Sampling over the process window.

Mende et al. (2026) sampled the NGM design space with Latin Hypercube Sampling;
we do the same to create the first training data set ("Einfahrversuche").
"""

from __future__ import annotations

import numpy as np

from presswatch.sim.process import (
    PARAM_BOUNDS,
    CycleRecord,
    MachineState,
    ProcessParams,
    simulate_cycle,
)


def latin_hypercube(n: int, seed: int = 0) -> list[ProcessParams]:
    """Return ``n`` parameter sets; every parameter hits each of its ``n`` strata once."""
    if n < 1:
        raise ValueError("n muss mindestens 1 sein")
    rng = np.random.default_rng(seed)
    columns = {}
    for name, (low, high) in PARAM_BOUNDS.items():
        strata = rng.permutation(n)
        unit = (strata + rng.uniform(0.0, 1.0, n)) / n
        columns[name] = low + unit * (high - low)
    return [
        ProcessParams(**{name: float(columns[name][i]) for name in PARAM_BOUNDS}) for i in range(n)
    ]


def doe_cycles(n: int, seed: int = 0) -> list[CycleRecord]:
    """Run a DoE on a healthy machine. Cycle ids are negative to mark them as trials."""
    rng = np.random.default_rng(seed + 10_000)
    return [
        simulate_cycle(params, state=MachineState(), rng=rng, cycle_id=-(i + 1), recipe="doe")
        for i, params in enumerate(latin_hypercube(n, seed))
    ]
