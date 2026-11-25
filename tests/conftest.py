"""Shared fixtures: a fresh API with an in-memory database for every test."""

from __future__ import annotations

import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from presswatch.api.app import create_app
from presswatch.config import Settings
from presswatch.payloads import cycle_payload, measurement_payload
from presswatch.sim.process import measure
from presswatch.sim.scenario import ProductionLine

FAST_SETTINGS = dict(
    doe_size=60,
    reference_cycles=30,
    window_cycles=30,
    baseline_n=30,
    sim_control=False,
)


def make_settings(database_url: str = "sqlite+pysqlite:///:memory:", **overrides) -> Settings:
    return Settings(database_url=database_url, **(FAST_SETTINGS | overrides))


@pytest.fixture
def client():
    app = create_app(make_settings())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def postgres_url():
    url = os.environ.get("PRESSWATCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PRESSWATCH_TEST_DATABASE_URL ist nicht gesetzt")
    return url


class Feeder:
    """Sends simulated cycles and measurements to the API, like collector + measuring station."""

    def __init__(self, client: TestClient, seed: int = 3, sample_every: int = 2) -> None:
        self.client = client
        self.line = ProductionLine(seed=seed)
        self.gauge = np.random.default_rng(seed + 1)
        self.sample_every = sample_every

    def run(self, n: int) -> list[dict]:
        responses = []
        for _ in range(n):
            cycle = self.line.next_cycle()
            response = self.client.post("/api/cycles", json=cycle_payload(cycle))
            assert response.status_code in (200, 201), response.text
            responses.append(response.json())
            if cycle.cycle_id % self.sample_every == 0:
                measured = measure(cycle.truth, self.gauge)
                r = self.client.post(
                    "/api/measurements", json=measurement_payload(cycle.cycle_id, measured)
                )
                assert r.status_code == 201, r.text
        return responses


@pytest.fixture
def feeder(client):
    return Feeder(client)
