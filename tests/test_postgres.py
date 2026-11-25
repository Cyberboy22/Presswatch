"""The same API against a real PostgreSQL (JSONB, timestamps, joins).

Run with:  PRESSWATCH_TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db pytest -m postgres
"""

import pytest
from fastapi.testclient import TestClient

from presswatch.api.app import create_app
from presswatch.db import make_engine
from presswatch.db.models import Base

from .conftest import Feeder, make_settings

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_client(postgres_url):
    engine = make_engine(postgres_url)
    Base.metadata.drop_all(engine)
    engine.dispose()
    with TestClient(create_app(make_settings(postgres_url))) as client:
        yield client


def test_full_cycle_on_postgres(pg_client):
    feeder = Feeder(pg_client)
    feeder.run(70)
    cycles = pg_client.get("/api/cycles").json()
    assert len(cycles) == 70
    assert cycles[-1]["prediction"]["model_version"] == "hybrid-v1"
    assert pg_client.get("/api/cycles/2").json()["measurement"]["source"] == "messstation"
    status = pg_client.get("/api/status").json()
    assert status["performance"]["pv_um"]["state"] == "aktiv"
    assert status["kpis"]["measured"] == 35


def test_model_lifecycle_on_postgres(pg_client):
    feeder = Feeder(pg_client)
    feeder.run(70)
    feeder.line.faults.tool_wear_rate_um_per_cycle = 0.06
    feeder.run(60)
    proposal = pg_client.post("/api/models/proposals", json={"actor": "a", "reason": "Test"})
    assert proposal.status_code == 201, proposal.text
    activated = pg_client.post(
        "/api/models/hybrid-v2/activate", json={"actor": "a", "reason": "Test"}
    )
    assert activated.status_code == 200
    rollback = pg_client.post("/api/models/rollback", json={"actor": "a", "reason": "Test"})
    assert rollback.json()["version"] == "hybrid-v1"


def test_quarantined_cycle_with_missing_value_is_stored_on_postgres(pg_client):
    from presswatch.payloads import cycle_payload
    from presswatch.sim.scenario import ProductionLine

    payload = cycle_payload(ProductionLine(seed=1).next_cycle())
    payload["curves"]["force_n"][3] = None  # JSON has no NaN; a missing value is null
    response = pg_client.post("/api/cycles", json=payload)
    assert response.json()["status"] == "quarantined"
    assert pg_client.get("/api/cycles/1").json()["curves"]["force_n"][3] is None
