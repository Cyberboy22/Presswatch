"""Tests for the REST API: ingest, predictions, monitoring, model lifecycle, audit."""

import pytest

from presswatch.payloads import cycle_payload, measurement_payload
from presswatch.sim.process import Quality
from presswatch.sim.scenario import ProductionLine


def _versions(client):
    return {m["version"]: m for m in client.get("/api/models").json()}


def _audit_actions(client):
    return [entry["action"] for entry in client.get("/api/audit").json()]


def test_startup_trains_initial_models_and_activates_the_hybrid(client):
    versions = _versions(client)
    assert set(versions) == {"baseline-v1", "hybrid-v1"}
    assert versions["hybrid-v1"]["status"] == "active"
    assert versions["baseline-v1"]["status"] == "archived"
    assert versions["hybrid-v1"]["card"]["training"]["n_samples"] == 60
    assert "model_activated" in _audit_actions(client)


def test_ingested_cycle_gets_a_traceable_prediction(client):
    cycle = ProductionLine(seed=1).next_cycle()
    body = client.post("/api/cycles", json=cycle_payload(cycle)).json()
    assert body["status"] == "ok"
    prediction = body["prediction"]
    assert prediction["model_version"] == "hybrid-v1"
    assert len(prediction["input_hash"]) == 64
    assert prediction["center_thickness_mm"] == pytest.approx(2.0, abs=0.08)
    assert prediction["in_spec"] in (True, False)


def test_sending_the_same_cycle_twice_stores_it_once(client):
    payload = cycle_payload(ProductionLine(seed=1).next_cycle())
    first = client.post("/api/cycles", json=payload)
    second = client.post("/api/cycles", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/api/cycles").json()) == 1


def test_cycle_with_a_data_error_is_quarantined_without_prediction(client):
    payload = cycle_payload(ProductionLine(seed=1).next_cycle())
    payload["params"]["press_force_n"] = 5000.0
    body = client.post("/api/cycles", json=payload).json()
    assert body["status"] == "quarantined"
    assert body["prediction"] is None
    assert any(issue["code"] == "PARAM_OUT_OF_RANGE" for issue in body["issues"])


def test_cycle_with_a_missing_sensor_value_is_quarantined(client):
    payload = cycle_payload(ProductionLine(seed=1).next_cycle())
    payload["curves"]["force_n"][3] = None
    body = client.post("/api/cycles", json=payload).json()
    assert body["status"] == "quarantined"
    assert [issue["code"] for issue in body["issues"]] == ["NAN_VALUES"]


def test_malformed_payload_is_rejected(client):
    assert client.post("/api/cycles", json={"cycle_id": "abc"}).status_code == 422


def test_measurement_for_an_unknown_cycle_is_not_found(client):
    payload = measurement_payload(999, Quality(center_thickness_mm=2.0, pv_um=2.0))
    assert client.post("/api/measurements", json=payload).status_code == 404


def test_measurement_appears_next_to_the_prediction(client, feeder):
    feeder.run(4)
    cycles = {c["cycle_id"]: c for c in client.get("/api/cycles").json()}
    assert cycles[2]["measurement"] is not None
    assert cycles[1]["measurement"] is None
    assert cycles[2]["prediction"]["model_version"] == "hybrid-v1"


def test_cycle_detail_contains_curves_features_and_issues(client, feeder):
    feeder.run(1)
    detail = client.get("/api/cycles/1").json()
    assert len(detail["curves"]["force_n"]) > 10
    assert "dev_tool_upper_c" in detail["features"]
    assert detail["issues"] == []
    assert client.get("/api/cycles/42").status_code == 404


def test_status_is_ok_on_healthy_production(client, feeder):
    feeder.run(140)
    status = client.get("/api/status").json()
    assert status["recommendation"]["status"] == "ok", status["recommendation"]
    assert status["kpis"]["cycles"] == 140
    assert status["performance"]["pv_um"]["state"] == "aktiv"


def _wear_until_alarm(client, feeder, rate=0.06):
    feeder.run(70)
    feeder.line.faults.tool_wear_rate_um_per_cycle = rate
    feeder.run(100)
    return client.get("/api/status").json()


def test_tool_wear_is_reported_as_a_process_change(client, feeder):
    status = _wear_until_alarm(client, feeder)
    recommendation = status["recommendation"]
    assert recommendation["status"] == "alarm"
    assert recommendation["cause"] == "prozess"
    assert "propose_retraining" in recommendation["allowed_actions"]


def test_retraining_is_only_a_proposal_until_a_person_approves_it(client, feeder):
    _wear_until_alarm(client, feeder)
    proposal = client.post(
        "/api/models/proposals", json={"actor": "a.alp", "reason": "Verschleiß"}
    ).json()
    assert proposal["version"] == "hybrid-v2"
    assert proposal["status"] == "proposed"
    comparison = proposal["metrics"]["comparison"]
    assert comparison["candidate"]["pv_um"]["rmse"] < comparison["active"]["pv_um"]["rmse"]
    assert _versions(client)["hybrid-v1"]["status"] == "active"

    response = client.post(
        "/api/models/hybrid-v2/activate", json={"actor": "a.alp", "reason": "geprüft"}
    )
    assert response.status_code == 200
    versions = _versions(client)
    assert versions["hybrid-v2"]["status"] == "active"
    assert versions["hybrid-v1"]["status"] == "archived"
    actions = _audit_actions(client)
    assert "retraining_proposed" in actions
    assert actions.count("model_activated") == 2


def test_rollback_reactivates_the_previous_version(client, feeder):
    _wear_until_alarm(client, feeder)
    client.post("/api/models/proposals", json={"actor": "a.alp", "reason": "Test"})
    client.post("/api/models/hybrid-v2/activate", json={"actor": "a.alp", "reason": "Test"})
    response = client.post("/api/models/rollback", json={"actor": "a.alp", "reason": "Wartung"})
    assert response.status_code == 200
    assert response.json()["version"] == "hybrid-v1"
    assert _versions(client)["hybrid-v1"]["status"] == "active"
    assert "model_rollback" in _audit_actions(client)


def test_rejected_proposal_cannot_be_activated(client, feeder):
    _wear_until_alarm(client, feeder)
    client.post("/api/models/proposals", json={"actor": "a.alp", "reason": "Test"})
    client.post("/api/models/hybrid-v2/reject", json={"actor": "a.alp", "reason": "zu riskant"})
    response = client.post("/api/models/hybrid-v2/activate", json={"actor": "a", "reason": "Test"})
    assert response.status_code == 409


def test_activating_an_unknown_version_is_not_found(client):
    response = client.post("/api/models/hybrid-v9/activate", json={"actor": "a", "reason": "Test"})
    assert response.status_code == 404


def test_monitoring_reset_is_audited_and_restarts_the_baseline(client, feeder):
    feeder.run(70)
    assert client.get("/api/status").json()["performance"]["pv_um"]["state"] == "aktiv"
    client.post("/api/monitoring/reset", json={"actor": "a.alp", "reason": "Sensor getauscht"})
    assert client.get("/api/status").json()["performance"]["pv_um"]["state"] == "einlaufen"
    assert "monitoring_reset" in _audit_actions(client)


def test_actions_require_a_reason(client):
    response = client.post("/api/monitoring/reset", json={"actor": "a.alp", "reason": ""})
    assert response.status_code == 422


def test_dashboard_and_operator_view_are_served(client):
    assert "PressWatch" in client.get("/").text
    assert "PressWatch" in client.get("/werker").text
    assert client.get("/api/health").json()["status"] == "ok"
