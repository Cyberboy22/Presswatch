"""OPC UA address space of the simulated glass press, shared by server and clients.

Objects/
  NGMPress/
    MachineId, CycleCounter, DataCycleId, Recipe, ProducedAt
    HeatingTime, PressTime, PressForce, TempUpperSet, TempLowerSet, GobMass
    FurnaceTemp[], ToolTempUpper[], ToolTempLower[], Force[], HeatingRate, PressingRate
  Scenario/           (writable – only exists because this is a simulation)
    ToolWearRate, SensorOffsetTarget, MaintenanceRequest
  SimState/           (read-only "hidden truth" for the demo)
    ToolWear, SensorOffset
"""

from __future__ import annotations

from datetime import datetime, timezone

from asyncua import ua

NAMESPACE = "urn:presswatch:ngm-cell"

D, I64, S, DT, B = (
    ua.VariantType.Double,
    ua.VariantType.Int64,
    ua.VariantType.String,
    ua.VariantType.DateTime,
    ua.VariantType.Boolean,
)

PRESS_VARIABLES: dict[str, ua.VariantType] = {
    "MachineId": S,
    "CycleCounter": I64,
    "DataCycleId": I64,
    "Recipe": S,
    "ProducedAt": DT,
    "HeatingTime": D,
    "PressTime": D,
    "PressForce": D,
    "TempUpperSet": D,
    "TempLowerSet": D,
    "GobMass": D,
    "FurnaceTemp": D,
    "ToolTempUpper": D,
    "ToolTempLower": D,
    "Force": D,
    "HeatingRate": D,
    "PressingRate": D,
}
ARRAY_VARIABLES = {"FurnaceTemp", "ToolTempUpper", "ToolTempLower", "Force"}
SCENARIO_VARIABLES: dict[str, ua.VariantType] = {
    "ToolWearRate": D,
    "SensorOffsetTarget": D,
    "MaintenanceRequest": B,
}
STATE_VARIABLES: dict[str, ua.VariantType] = {"ToolWear": D, "SensorOffset": D}

# OPC UA variable → field of the REST payload
PARAM_NODES = {
    "HeatingTime": "heating_time_s",
    "PressTime": "press_time_s",
    "PressForce": "press_force_n",
    "TempUpperSet": "temp_upper_c",
    "TempLowerSet": "temp_lower_c",
}
CURVE_NODES = {
    "FurnaceTemp": "furnace_temp_c",
    "ToolTempUpper": "tool_temp_upper_c",
    "ToolTempLower": "tool_temp_lower_c",
    "Force": "force_n",
    "HeatingRate": "heating_rate_hz",
    "PressingRate": "pressing_rate_hz",
}


def initial_value(name: str, vtype: ua.VariantType):
    if name in ARRAY_VARIABLES:
        return [0.0]
    if vtype == DT:
        return datetime.now(timezone.utc)
    return {D: 0.0, I64: 0, S: "", B: False}[vtype]


async def resolve(client, folder: str, names) -> dict:
    """Look up nodes by browse path on the client side."""
    idx = await client.get_namespace_index(NAMESPACE)
    objects = client.nodes.objects
    return {name: await objects.get_child([f"{idx}:{folder}", f"{idx}:{name}"]) for name in names}


def variant(value, vtype: ua.VariantType) -> ua.DataValue:
    return ua.DataValue(ua.Variant(value, vtype))
