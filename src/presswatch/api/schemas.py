"""Request bodies of the REST API (validated by pydantic)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ParamsIn(BaseModel):
    heating_time_s: float
    press_time_s: float
    press_force_n: float
    temp_upper_c: float
    temp_lower_c: float


class CurvesIn(BaseModel):
    """Sensor curves. A missing value is sent as null and is caught by the quality check."""

    heating_rate_hz: float = Field(gt=0)
    pressing_rate_hz: float = Field(gt=0)
    furnace_temp_c: list[float | None] = Field(min_length=1)
    tool_temp_upper_c: list[float | None] = Field(min_length=1)
    tool_temp_lower_c: list[float | None] = Field(min_length=1)
    force_n: list[float | None] = Field(min_length=1)


class CycleIn(BaseModel):
    machine_id: str = "NGM-1"
    cycle_id: int
    recipe: str = "unbekannt"
    produced_at: datetime | None = None
    params: ParamsIn
    gob_mass_mg: float
    curves: CurvesIn


class MeasurementIn(BaseModel):
    machine_id: str = "NGM-1"
    cycle_id: int
    center_thickness_mm: float = Field(gt=0)
    pv_um: float = Field(ge=0)
    source: str = "messstation"
    measured_at: datetime | None = None


class ActionIn(BaseModel):
    """Every decision needs a person and a reason – that is what makes it auditable."""

    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=500)


class SimActionIn(BaseModel):
    action: str = Field(pattern="^(tool_wear|sensor_fault|maintenance|reset_faults)$")
    value: float | None = None
