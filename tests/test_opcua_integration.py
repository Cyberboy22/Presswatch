"""Integration test: OPC UA cell → collector → REST API → database, with real servers."""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
import uvicorn

from presswatch.api.app import create_app
from presswatch.opcua.cell import ProductionCell
from presswatch.opcua.collector import Collector
from presswatch.opcua.control import SimController
from presswatch.sim.scenario import ProductionLine

from .conftest import make_settings

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _settings(tmp_path):
    api_port, ua_port = _free_port(), _free_port()
    return make_settings(
        database_url=f"sqlite:///{tmp_path / 'it.db'}",
        doe_size=40,
        api_url=f"http://127.0.0.1:{api_port}",
        opcua_endpoint=f"opc.tcp://127.0.0.1:{ua_port}/presswatch/",
        opcua_listen=f"opc.tcp://127.0.0.1:{ua_port}/presswatch/",
        cycle_period_s=0.05,
        sample_every=2,
        measurement_delay_cycles=1,
        sim_control=True,
        startup_delay_s=0.5,
    ), api_port


async def _wait_for(predicate, timeout=20.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.1)
    return False


async def _run_pipeline(settings, api_port, scenario):
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=api_port, log_level="warning")
    )
    api_task = asyncio.create_task(server.serve())
    cell, collector = ProductionCell(settings), Collector(settings)
    tasks = [asyncio.create_task(cell.run()), asyncio.create_task(collector.run())]
    try:
        async with httpx.AsyncClient(base_url=settings.api_url, timeout=5) as http:
            return await scenario(http)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.should_exit = True
        await api_task


def test_cycles_and_measurements_flow_from_the_machine_into_the_database(tmp_path):
    settings, api_port = _settings(tmp_path)

    async def scenario(http):
        async def enough():
            try:
                return len((await http.get("/api/cycles")).json()) >= 30
            except httpx.HTTPError:
                return False

        assert await _wait_for(enough), "keine Zyklen angekommen"
        cycles = (await http.get("/api/cycles")).json()
        return cycles

    cycles = asyncio.run(_run_pipeline(settings, api_port, scenario))
    ids = [c["cycle_id"] for c in cycles]
    assert ids == list(range(ids[0], ids[0] + len(ids))), "Zyklen fehlen oder sind doppelt"
    assert all(c["prediction"] is not None for c in cycles)
    assert sum(c["measurement"] is not None for c in cycles) >= 10
    assert cycles[0]["recipe"] in ("standard", "schnell", "schonend")


def test_dashboard_can_switch_faults_on_and_trigger_maintenance(tmp_path):
    settings, api_port = _settings(tmp_path)

    async def scenario(http):
        assert await _wait_for(lambda: _is_up(http))
        await http.post("/api/sim", json={"action": "tool_wear", "value": 0.5})

        async def worn():
            return (await http.get("/api/sim")).json()["tool_wear_um"] > 1.0

        assert await _wait_for(worn)
        await http.post("/api/sim", json={"action": "tool_wear", "value": 0.0})
        await http.post("/api/sim", json={"action": "maintenance"})

        async def repaired():
            return (await http.get("/api/sim")).json()["tool_wear_um"] == 0.0

        assert await _wait_for(repaired)
        await http.post("/api/sim", json={"action": "reset_faults"})
        state = (await http.get("/api/sim")).json()
        audit = [e["action"] for e in (await http.get("/api/audit")).json()]
        return state, audit

    state, audit = asyncio.run(_run_pipeline(settings, api_port, scenario))
    assert state["tool_wear_rate_um_per_cycle"] == 0.0
    assert {"sim_tool_wear", "sim_maintenance", "sim_reset_faults"} <= set(audit)


async def _is_up(http):
    try:
        return (await http.get("/api/sim")).status_code == 200
    except httpx.HTTPError:
        return False


def test_cell_continues_counting_after_a_restart():
    line = ProductionLine(seed=0, start_cycle_id=120)
    assert line.next_cycle().cycle_id == 121


def test_sim_controller_rejects_unknown_actions():
    with pytest.raises(ValueError):
        asyncio.run(SimController("opc.tcp://127.0.0.1:1/x/").apply("explode", None))
