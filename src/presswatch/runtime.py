"""Run the production cell and the collector next to the API (one-command demo)."""

from __future__ import annotations

import asyncio

from presswatch.config import Settings
from presswatch.opcua.cell import ProductionCell
from presswatch.opcua.collector import Collector


async def start_embedded_simulation(settings: Settings) -> list[asyncio.Task]:
    cell, collector = ProductionCell(settings), Collector(settings)
    return [
        asyncio.create_task(cell.run(), name="presswatch-cell"),
        asyncio.create_task(collector.run(), name="presswatch-collector"),
    ]
