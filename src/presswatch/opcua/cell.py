"""Simulated production cell: the glass press as an OPC UA server, plus a measuring station.

The press publishes every finished cycle on its OPC UA server. The measuring
station checks every n-th lens a few cycles later and reports the result to the
REST API – a second, independent data source, as in a real factory.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import numpy as np
from asyncua import Server, ua

from presswatch.config import Settings
from presswatch.opcua.nodes import (
    NAMESPACE,
    PRESS_VARIABLES,
    SCENARIO_VARIABLES,
    STATE_VARIABLES,
    initial_value,
    variant,
)
from presswatch.payloads import measurement_payload
from presswatch.sim.process import CycleRecord, measure
from presswatch.sim.scenario import ProductionLine

log = logging.getLogger("presswatch.cell")
MAX_MEASUREMENT_RETRIES = 50


class ProductionCell:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.line: ProductionLine | None = None
        self._pending: list[tuple[int, int, CycleRecord]] = []  # (due cycle, attempts, record)
        self._gauge = np.random.default_rng(settings.sim_seed + 1)
        self._nodes: dict = {}

    async def _build(self, server: Server) -> None:
        idx = await server.register_namespace(NAMESPACE)
        objects = server.nodes.objects
        for folder, variables, writable in (
            ("NGMPress", PRESS_VARIABLES, False),
            ("Scenario", SCENARIO_VARIABLES, True),
            ("SimState", STATE_VARIABLES, False),
        ):
            obj = await objects.add_object(idx, folder)
            for name, vtype in variables.items():
                node = await obj.add_variable(
                    idx, name, initial_value(name, vtype), varianttype=vtype
                )
                if writable:
                    await node.set_writable()
                self._nodes[name] = node
        await self._write("MachineId", self.settings.machine_id)

    async def _write(self, name: str, value) -> None:
        vtype = PRESS_VARIABLES.get(name) or SCENARIO_VARIABLES.get(name) or STATE_VARIABLES[name]
        await self._nodes[name].write_value(variant(value, vtype))

    async def _start_cycle_id(self, http: httpx.AsyncClient) -> int:
        """Continue counting where the database stopped (wait for the API to be up)."""
        for _ in range(120):
            try:
                cycles = (await http.get("/api/cycles", params={"limit": 1})).json()
                return cycles[-1]["cycle_id"] if cycles else 0
            except (httpx.HTTPError, ValueError, KeyError):
                await asyncio.sleep(0.5)
        log.warning("API nicht erreichbar – Zählung beginnt bei 0")
        return 0

    async def _apply_scenario(self) -> None:
        faults = self.line.faults
        faults.tool_wear_rate_um_per_cycle = float(await self._nodes["ToolWearRate"].read_value())
        faults.sensor_offset_target_c = float(await self._nodes["SensorOffsetTarget"].read_value())
        if await self._nodes["MaintenanceRequest"].read_value():
            self.line.maintenance()
            await self._write("MaintenanceRequest", False)
            log.info("Wartung: Werkzeugbeschichtung erneuert")

    async def _publish(self, record: CycleRecord) -> None:
        params, curves = record.params, record.curves
        values = {
            "Recipe": record.recipe,
            "ProducedAt": datetime.now(timezone.utc),
            "HeatingTime": params.heating_time_s,
            "PressTime": params.press_time_s,
            "PressForce": params.press_force_n,
            "TempUpperSet": params.temp_upper_c,
            "TempLowerSet": params.temp_lower_c,
            "GobMass": record.gob_mass_mg,
            "FurnaceTemp": curves.furnace_temp_c,
            "ToolTempUpper": curves.tool_temp_upper_c,
            "ToolTempLower": curves.tool_temp_lower_c,
            "Force": curves.force_n,
            "HeatingRate": curves.heating_rate_hz,
            "PressingRate": curves.pressing_rate_hz,
        }
        for name, value in values.items():
            await self._write(name, value)
        # Written last: a client that sees the new counter finds consistent data.
        await self._write("DataCycleId", record.cycle_id)
        await self._write("CycleCounter", record.cycle_id)
        await self._write("ToolWear", self.line.state.tool_wear_um)
        await self._write("SensorOffset", self.line.state.sensor_offset_upper_c)

    async def _measure(self, http: httpx.AsyncClient, current: int) -> None:
        still_pending = []
        for due, attempts, record in self._pending:
            if due > current:
                still_pending.append((due, attempts, record))
                continue
            payload = measurement_payload(
                record.cycle_id, measure(record.truth, self._gauge), self.settings.machine_id
            )
            try:
                response = await http.post("/api/measurements", json=payload)
                delivered = response.status_code in (200, 201)
            except httpx.HTTPError:
                delivered = False
            if not delivered and attempts < MAX_MEASUREMENT_RETRIES:
                still_pending.append((due, attempts + 1, record))
            elif not delivered:
                log.warning(
                    "Messung für Zyklus %s verworfen (Zyklus nicht in der Datenbank)",
                    record.cycle_id,
                )
        self._pending = still_pending

    async def run(self) -> None:
        server = Server()
        await server.init()
        server.set_endpoint(self.settings.opcua_listen)
        server.set_server_name("PressWatch NGM-Zelle (Simulation)")
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        await self._build(server)
        async with server, httpx.AsyncClient(base_url=self.settings.api_url, timeout=5) as http:
            start = await self._start_cycle_id(http)
            self.line = ProductionLine(seed=self.settings.sim_seed + start, start_cycle_id=start)
            log.info("Zelle läuft: %s, ab Zyklus %s", self.settings.opcua_listen, start + 1)
            await asyncio.sleep(self.settings.startup_delay_s)  # the press warms up
            while True:
                await self._apply_scenario()
                record = self.line.next_cycle()
                await self._publish(record)
                if record.cycle_id % self.settings.sample_every == 0:
                    due = record.cycle_id + self.settings.measurement_delay_cycles
                    self._pending.append((due, 0, record))
                await self._measure(http, record.cycle_id)
                await asyncio.sleep(self.settings.cycle_period_s)
