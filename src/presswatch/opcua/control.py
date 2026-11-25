"""Demo control: the dashboard switches faults on and off in the simulated cell via OPC UA."""

from __future__ import annotations

from asyncua import Client

from presswatch.opcua.nodes import SCENARIO_VARIABLES, STATE_VARIABLES, resolve, variant

DEFAULTS = {"tool_wear": 0.03, "sensor_fault": 20.0}
ACTIONS = ("tool_wear", "sensor_fault", "maintenance", "reset_faults")


class SimController:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint

    async def state(self) -> dict:
        async with Client(url=self.endpoint, timeout=4) as client:
            return await self._read(client)

    async def apply(self, action: str, value: float | None) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"Unbekannte Aktion '{action}' (erlaubt: {ACTIONS})")
        async with Client(url=self.endpoint, timeout=4) as client:
            nodes = await resolve(client, "Scenario", SCENARIO_VARIABLES)
            writes = {
                "tool_wear": {
                    "ToolWearRate": value if value is not None else DEFAULTS["tool_wear"]
                },
                "sensor_fault": {
                    "SensorOffsetTarget": value if value is not None else DEFAULTS["sensor_fault"]
                },
                "maintenance": {"MaintenanceRequest": True},
                "reset_faults": {"ToolWearRate": 0.0, "SensorOffsetTarget": 0.0},
            }[action]
            for name, new_value in writes.items():
                cast = bool(new_value) if name == "MaintenanceRequest" else float(new_value)
                await nodes[name].write_value(variant(cast, SCENARIO_VARIABLES[name]))
            return await self._read(client)

    @staticmethod
    async def _read(client) -> dict:
        scenario = await resolve(client, "Scenario", SCENARIO_VARIABLES)
        state = await resolve(client, "SimState", STATE_VARIABLES)
        counter = await resolve(client, "NGMPress", ["CycleCounter", "Recipe"])
        return {
            "tool_wear_rate_um_per_cycle": float(await scenario["ToolWearRate"].read_value()),
            "sensor_offset_target_c": float(await scenario["SensorOffsetTarget"].read_value()),
            "maintenance_requested": bool(await scenario["MaintenanceRequest"].read_value()),
            "tool_wear_um": round(float(await state["ToolWear"].read_value()), 4),
            "sensor_offset_upper_c": round(float(await state["SensorOffset"].read_value()), 3),
            "cycle": int(await counter["CycleCounter"].read_value()),
            "recipe": await counter["Recipe"].read_value(),
        }
