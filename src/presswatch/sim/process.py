"""Simplified physics model of non-isothermal glass molding (NGM).

One press cycle:

1. A glass gob is weighed and heated in a furnace (heating time).
2. It is transferred into the press and loses a little heat.
3. It is pressed between two *cooler* forming tools (press force, press time,
   upper and lower tool temperature). While it is pressed, the glass cools down
   and its viscosity rises quickly, so the lens "freezes" into shape.
4. The lens cools to room temperature and shrinks slightly.

The flow during pressing follows Stefan's squeeze-flow law for a constant-volume
disc, the viscosity follows a Vogel-Fulcher-Tammann (VFT) curve. Form deviation
(peak-to-valley, PV) comes from tool temperature asymmetry, the temperature
difference between glass and tools, releasing glass that is still too soft and
pressing glass that is already too stiff – plus tool wear.

IMPORTANT: every constant in this file is invented for a demonstration. The model
shows realistic *relationships*, not the behaviour of a real machine or glass.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

# Allowed process window. Parameter names match the NGM inputs used in
# Mende et al. (2026), "Automated Hybrid Machine Learning System for Production".
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "heating_time_s": (140.0, 220.0),
    "press_time_s": (4.0, 16.0),
    "press_force_n": (600.0, 1800.0),
    "temp_upper_c": (460.0, 560.0),
    "temp_lower_c": (460.0, 560.0),
}

PARAM_LABELS: dict[str, str] = {
    "heating_time_s": "Heizzeit [s]",
    "press_time_s": "Presszeit [s]",
    "press_force_n": "Presskraft [N]",
    "temp_upper_c": "Temperatur Oberwerkzeug [°C]",
    "temp_lower_c": "Temperatur Unterwerkzeug [°C]",
}

HEATING_RATE_HZ = 1.0
PRESSING_RATE_HZ = 10.0


@dataclass(frozen=True)
class ProcessParams:
    """Set points of one press cycle (a "recipe")."""

    heating_time_s: float
    press_time_s: float
    press_force_n: float
    temp_upper_c: float
    temp_lower_c: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def validate(self) -> ProcessParams:
        for name, (low, high) in PARAM_BOUNDS.items():
            value = getattr(self, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(
                    f"{name}={value} liegt außerhalb des Prozessfensters [{low}, {high}]"
                )
        return self


@dataclass
class MachineState:
    """Slowly changing state of the machine. Not visible to the data pipeline."""

    tool_wear_um: float = 0.0  # extra form deviation caused by a worn tool coating
    sensor_offset_upper_c: float = 0.0  # faulty thermocouple at the upper tool
    furnace_offset_c: float = 0.0  # slow drift of the furnace controller


@dataclass(frozen=True)
class Curves:
    """Sensor time series of one cycle, as a machine would record them."""

    heating_rate_hz: float
    pressing_rate_hz: float
    furnace_temp_c: list[float]
    tool_temp_upper_c: list[float]
    tool_temp_lower_c: list[float]
    force_n: list[float]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Quality:
    center_thickness_mm: float
    pv_um: float


@dataclass(frozen=True)
class QualitySpec:
    """Specification of the demo lens."""

    pv_max_um: float = 5.0
    ct_nominal_mm: float = 2.0
    ct_tolerance_mm: float = 0.05

    def ct_ok(self, ct_mm: float) -> bool:
        return abs(ct_mm - self.ct_nominal_mm) <= self.ct_tolerance_mm

    def pv_ok(self, pv_um: float) -> bool:
        return pv_um <= self.pv_max_um

    def is_ok(self, quality: Quality) -> bool:
        return self.ct_ok(quality.center_thickness_mm) and self.pv_ok(quality.pv_um)


SPEC = QualitySpec()


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: int
    recipe: str
    params: ProcessParams
    gob_mass_mg: float  # reading of the scale
    curves: Curves
    truth: Quality  # true lens quality – only the measurement station can see it
    internals: dict[str, float] = field(default_factory=dict)  # for tests and debugging


@dataclass(frozen=True)
class _Physics:
    ambient_c: float = 25.0
    furnace_setpoint_c: float = 720.0
    heating_tau_s: float = 60.0
    door_dip_c: float = 12.0
    door_recovery_s: float = 15.0
    transfer_drop_c: float = 15.0
    vft_a: float = -1.33
    vft_b: float = 2800.0
    vft_t0_c: float = 300.0
    glass_density_mg_per_mm3: float = 3.0
    gob_mass_nominal_mg: float = 635.0
    cooling_tau_s: float = 7.0
    force_rise_s: float = 0.3
    contact_heating_c: float = 4.0
    thermal_expansion_per_k: float = 9e-6
    pv_base_um: float = 0.8
    pv_asymmetry_um_per_k: float = 0.045
    pv_quench_um_per_k: float = 0.010
    soft_release_log_visc: float = 9.6
    pv_soft_release_um: float = 1.6
    stiff_start_log_visc: float = 7.2
    pv_stiff_start_um: float = 1.5


@dataclass(frozen=True)
class _Noise:
    """Standard deviations of the random variation (only used if an rng is given)."""

    furnace_c: float = 0.6
    transfer_c: float = 0.6
    gob_mass_mg: float = 4.0
    scale_mg: float = 0.3
    tool_control_c: float = 0.8
    temperature_sensor_c: float = 0.3
    force_sensor_n: float = 4.0
    pv_process_um: float = 0.3
    gauge_ct_mm: float = 0.002
    gauge_pv_um: float = 0.15


PHYSICS = _Physics()
NOISE = _Noise()


def log_viscosity(temp_c: float, a: float, b: float, t0_c: float) -> float:
    """VFT equation: log10 of the viscosity in Pa·s."""
    return a + b / max(temp_c - t0_c, 1.0)


def simulate_cycle(
    params: ProcessParams,
    state: MachineState | None = None,
    rng: np.random.Generator | None = None,
    cycle_id: int = 0,
    recipe: str = "custom",
) -> CycleRecord:
    """Simulate one press cycle. Without ``rng`` the result is free of random noise."""
    params.validate()
    state = state or MachineState()
    phys, noise = PHYSICS, NOISE

    def scalar_noise(std: float) -> float:
        return float(rng.normal(0.0, std)) if rng is not None else 0.0

    def array_noise(std: float, size: int) -> np.ndarray:
        return rng.normal(0.0, std, size) if rng is not None else np.zeros(size)

    # 1) Gob
    gob_mass_true = phys.gob_mass_nominal_mg + scalar_noise(noise.gob_mass_mg)
    gob_mass_reading = gob_mass_true + scalar_noise(noise.scale_mg)
    volume_mm3 = gob_mass_true / phys.glass_density_mg_per_mm3
    volume_m3 = volume_mm3 * 1e-9
    gob_diameter_m = (6.0 * volume_mm3 / math.pi) ** (1.0 / 3.0) * 1e-3

    # 2) Heating (first-order heat transfer from the furnace into the gob)
    n_heat = int(params.heating_time_s * HEATING_RATE_HZ)
    t_heat = np.arange(n_heat) / HEATING_RATE_HZ
    furnace_level = phys.furnace_setpoint_c + state.furnace_offset_c + scalar_noise(noise.furnace_c)
    furnace_actual = furnace_level - phys.door_dip_c * np.exp(-t_heat / phys.door_recovery_s)
    decay_heat = math.exp(-(1.0 / HEATING_RATE_HZ) / phys.heating_tau_s)
    glass_temp = phys.ambient_c
    for furnace_temp in furnace_actual:
        glass_temp = furnace_temp + (glass_temp - furnace_temp) * decay_heat
    glass_temp_start = float(glass_temp) - phys.transfer_drop_c + scalar_noise(noise.transfer_c)

    # 3) Pressing (Stefan squeeze flow while the glass cools towards the tools)
    n_press = int(params.press_time_s * PRESSING_RATE_HZ)
    dt = 1.0 / PRESSING_RATE_HZ
    t_press = np.arange(n_press) * dt
    contact_bump = phys.contact_heating_c * (1.0 - np.exp(-t_press / 1.5)) * np.exp(-t_press / 6.0)
    upper_actual = params.temp_upper_c + scalar_noise(noise.tool_control_c) + contact_bump
    lower_actual = params.temp_lower_c + scalar_noise(noise.tool_control_c) + contact_bump
    tool_mean = (upper_actual + lower_actual) / 2.0
    force_actual = params.press_force_n * (1.0 - np.exp(-(t_press + dt) / phys.force_rise_s))

    inv_h4 = gob_diameter_m**-4
    decay_cool = math.exp(-dt / phys.cooling_tau_s)
    glass_temp = glass_temp_start
    for force, tool_temp in zip(force_actual, tool_mean, strict=True):
        eta = 10.0 ** log_viscosity(glass_temp, phys.vft_a, phys.vft_b, phys.vft_t0_c)
        inv_h4 += 8.0 * math.pi * force * dt / (3.0 * eta * volume_m3**2)
        glass_temp = tool_temp + (glass_temp - tool_temp) * decay_cool
    glass_temp_end = glass_temp
    height_mm = inv_h4**-0.25 * 1e3

    log_visc_start = log_viscosity(glass_temp_start, phys.vft_a, phys.vft_b, phys.vft_t0_c)
    log_visc_end = log_viscosity(glass_temp_end, phys.vft_a, phys.vft_b, phys.vft_t0_c)

    # 4) Quality
    center_thickness = height_mm * (
        1.0 - phys.thermal_expansion_per_k * (glass_temp_end - phys.ambient_c)
    )
    asymmetry = abs(float(np.mean(upper_actual) - np.mean(lower_actual)))
    quench = max(0.0, glass_temp_start - float(np.mean(tool_mean)))
    pv = (
        phys.pv_base_um
        + phys.pv_asymmetry_um_per_k * asymmetry
        + phys.pv_quench_um_per_k * quench
        + phys.pv_soft_release_um * max(0.0, phys.soft_release_log_visc - log_visc_end)
        + phys.pv_stiff_start_um * max(0.0, log_visc_start - phys.stiff_start_log_visc)
        + state.tool_wear_um
        + scalar_noise(noise.pv_process_um)
    )
    pv = max(pv, 0.2)

    # 5) What the sensors report
    curves = Curves(
        heating_rate_hz=HEATING_RATE_HZ,
        pressing_rate_hz=PRESSING_RATE_HZ,
        furnace_temp_c=_rounded(furnace_actual + array_noise(noise.temperature_sensor_c, n_heat)),
        tool_temp_upper_c=_rounded(
            upper_actual
            + state.sensor_offset_upper_c
            + array_noise(noise.temperature_sensor_c, n_press)
        ),
        tool_temp_lower_c=_rounded(lower_actual + array_noise(noise.temperature_sensor_c, n_press)),
        force_n=_rounded(force_actual + array_noise(noise.force_sensor_n, n_press)),
    )
    internals = {
        "gob_mass_true_mg": gob_mass_true,
        "glass_temp_start_c": float(glass_temp_start),
        "glass_temp_end_c": float(glass_temp_end),
        "log_visc_start": log_visc_start,
        "log_visc_end": log_visc_end,
        "tool_temp_upper_actual_c": float(np.mean(upper_actual)),
        "tool_temp_lower_actual_c": float(np.mean(lower_actual)),
    }
    return CycleRecord(
        cycle_id=cycle_id,
        recipe=recipe,
        params=params,
        gob_mass_mg=round(gob_mass_reading, 2),
        curves=curves,
        truth=Quality(center_thickness_mm=float(center_thickness), pv_um=float(pv)),
        internals=internals,
    )


def measure(quality: Quality, rng: np.random.Generator) -> Quality:
    """Measurement station: tactile probe for center thickness, profilometer for PV."""
    return Quality(
        center_thickness_mm=round(
            quality.center_thickness_mm + float(rng.normal(0.0, NOISE.gauge_ct_mm)), 4
        ),
        pv_um=round(max(quality.pv_um + float(rng.normal(0.0, NOISE.gauge_pv_um)), 0.0), 3),
    )


def _rounded(values: np.ndarray) -> list[float]:
    return [round(float(v), 2) for v in values]
