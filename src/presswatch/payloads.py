"""JSON payloads exchanged between collector, measuring station and API."""

from __future__ import annotations

from datetime import datetime, timezone

from presswatch.sim.process import CycleRecord, Quality


def _iso(ts: datetime | None) -> str:
    return (ts or datetime.now(timezone.utc)).isoformat()


def cycle_payload(
    record: CycleRecord, machine_id: str = "NGM-1", produced_at: datetime | None = None
) -> dict:
    return {
        "machine_id": machine_id,
        "cycle_id": record.cycle_id,
        "recipe": record.recipe,
        "produced_at": _iso(produced_at),
        "params": record.params.as_dict(),
        "gob_mass_mg": record.gob_mass_mg,
        "curves": record.curves.as_dict(),
    }


def measurement_payload(
    cycle_id: int,
    quality: Quality,
    machine_id: str = "NGM-1",
    source: str = "messstation",
    measured_at: datetime | None = None,
) -> dict:
    return {
        "machine_id": machine_id,
        "cycle_id": cycle_id,
        "center_thickness_mm": quality.center_thickness_mm,
        "pv_um": quality.pv_um,
        "source": source,
        "measured_at": _iso(measured_at),
    }
