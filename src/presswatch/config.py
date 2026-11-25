"""Configuration. Every field can be set with an environment variable PRESSWATCH_<NAME>."""

from __future__ import annotations

import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./presswatch.db"
    machine_id: str = "NGM-1"
    # OPC UA: the production cell serves, the collector reads
    opcua_endpoint: str = "opc.tcp://127.0.0.1:4840/presswatch/"
    opcua_listen: str = "opc.tcp://0.0.0.0:4840/presswatch/"
    api_url: str = "http://127.0.0.1:8000"
    # Production cell simulation
    cycle_period_s: float = 1.0
    sample_every: int = 5  # the measuring station checks every n-th lens
    measurement_delay_cycles: int = 3  # measuring takes a while
    sim_seed: int = 7
    startup_delay_s: float = 3.0  # the cell waits a moment so the collector can connect first
    # Initial training
    doe_size: int = 150
    seed: int = 42
    # Monitoring
    reference_cycles: int = 150
    window_cycles: int = 100
    baseline_n: int = 60
    retrain_window: int = 150
    # Demo features
    sim_control: bool = True  # allow the dashboard to inject faults into the simulator
    embedded_sim: bool = False  # run cell + collector inside the API process (presswatch demo)

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        values = {}
        for f in fields(cls):
            raw = os.environ.get(f"PRESSWATCH_{f.name.upper()}")
            if raw is None:
                continue
            if f.type in ("bool", bool):
                values[f.name] = raw.strip().lower() in ("1", "true", "yes", "ja", "on")
            elif f.type in ("int", int):
                values[f.name] = int(raw)
            elif f.type in ("float", float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        return cls(**(values | overrides))
