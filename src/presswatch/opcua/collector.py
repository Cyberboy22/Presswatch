"""Collector: reads finished cycles from the machine via OPC UA and sends them to the API.

* subscribes to the cycle counter (no polling of every variable)
* checks that the data belong to the announced cycle
* notices missed cycles
* buffers payloads while the API is down and reconnects to the machine on its own
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque

import httpx
from asyncua import Client, ua

from presswatch.config import Settings
from presswatch.opcua.nodes import CURVE_NODES, PARAM_NODES, PRESS_VARIABLES, resolve

log = logging.getLogger("presswatch.collector")
BUFFER_LIMIT = 10_000


class _CounterHandler:
    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue

    def datachange_notification(self, node, value, data) -> None:
        self.queue.put_nowait(value)


class Collector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.buffer: deque[dict] = deque(maxlen=BUFFER_LIMIT)
        self.last_cycle_id: int | None = None
        self.missed_cycles = 0
        self._new_data = asyncio.Event()
        self._retry_delay = 0.5

    async def run(self) -> None:
        async with httpx.AsyncClient(base_url=self.settings.api_url, timeout=5) as http:
            sender = asyncio.create_task(self._send_forever(http))
            try:
                while True:
                    try:
                        await self._read_forever()
                    except (OSError, asyncio.TimeoutError, ua.UaError, ConnectionError) as exc:
                        delay = self._retry_delay
                        log.warning(
                            "OPC-UA-Verbindung getrennt (%s) – neuer Versuch in %.1f s", exc, delay
                        )
                        await asyncio.sleep(delay)
                        self._retry_delay = min(delay * 2, 5.0)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender

    async def _read_forever(self) -> None:
        async with Client(url=self.settings.opcua_endpoint, timeout=4) as client:
            nodes = await resolve(client, "NGMPress", PRESS_VARIABLES)
            queue: asyncio.Queue = asyncio.Queue()
            subscription = await client.create_subscription(50, _CounterHandler(queue))
            await subscription.subscribe_data_change(nodes["CycleCounter"])
            log.info("Verbunden mit %s", self.settings.opcua_endpoint)
            self._retry_delay = 0.5
            while True:
                try:
                    counter = await asyncio.wait_for(queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    await nodes["CycleCounter"].read_value()  # raises if the machine is gone
                    continue
                await self._handle(nodes, int(counter))

    async def _handle(self, nodes: dict, counter: int) -> None:
        if counter <= 0 or (self.last_cycle_id is not None and counter <= self.last_cycle_id):
            return
        names = list(nodes)
        values = dict(zip(names, [await nodes[n].read_value() for n in names], strict=True))
        if values["DataCycleId"] != counter:
            log.warning("Zyklus %s wurde überschrieben, bevor er gelesen war", counter)
            self.missed_cycles += 1
            return
        if self.last_cycle_id is not None and counter > self.last_cycle_id + 1:
            gap = counter - self.last_cycle_id - 1
            self.missed_cycles += gap
            log.warning("%s Zyklen verpasst (vor Zyklus %s)", gap, counter)
        self.last_cycle_id = counter
        self.buffer.append(self._payload(values))
        self._new_data.set()

    @staticmethod
    def _payload(values: dict) -> dict:
        produced_at = values["ProducedAt"]
        return {
            "machine_id": values["MachineId"],
            "cycle_id": int(values["DataCycleId"]),
            "recipe": values["Recipe"],
            "produced_at": produced_at.isoformat() if produced_at else None,
            "params": {field: float(values[node]) for node, field in PARAM_NODES.items()},
            "gob_mass_mg": float(values["GobMass"]),
            "curves": {
                field: [float(v) for v in values[node]]
                if isinstance(values[node], list)
                else float(values[node])
                for node, field in CURVE_NODES.items()
            },
        }

    async def _send_forever(self, http: httpx.AsyncClient) -> None:
        backoff = 0.5
        while True:
            if not self.buffer:
                self._new_data.clear()
                await self._new_data.wait()
                continue
            payload = self.buffer[0]
            try:
                response = await http.post("/api/cycles", json=payload)
            except httpx.HTTPError as exc:
                log.warning(
                    "API nicht erreichbar (%s) – %s Zyklen gepuffert", exc, len(self.buffer)
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            if response.status_code in (200, 201):
                self.buffer.popleft()
                backoff = 0.5
            elif 400 <= response.status_code < 500:
                log.error("API lehnt Zyklus %s ab: %s", payload["cycle_id"], response.text[:200])
                self.buffer.popleft()
            else:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
