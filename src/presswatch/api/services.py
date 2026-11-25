"""Business logic of the API, independent of HTTP (easy to test and reuse)."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from presswatch.api.schemas import CycleIn, MeasurementIn
from presswatch.config import Settings
from presswatch.db.models import AuditEntry, Cycle, Measurement, ModelVersion, Prediction
from presswatch.features import extract_features
from presswatch.models import TARGETS, deserialize, out_of_range, serialize, train_model
from presswatch.monitoring.decision import recommend
from presswatch.monitoring.drift import data_drift
from presswatch.monitoring.performance import performance_drift
from presswatch.payloads import cycle_payload
from presswatch.quality import check_cycle, has_errors
from presswatch.sim.doe import doe_cycles
from presswatch.sim.process import SPEC, Quality, measure

MIN_SIGMA = {"center_thickness_mm": 0.002, "pv_um": 0.15}  # gauge resolution
RECENT_ERRORS_WINDOW = 20
RECENT_PREDICTIONS_WINDOW = 50
MIN_RETRAIN_SAMPLES = 20


class ServiceError(Exception):
    status_code = 400


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(ts: datetime | None) -> datetime:
    if ts is None:
        return utcnow()
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat(timespec="milliseconds") + "Z"


def input_hash(features: dict) -> str:
    return hashlib.sha256(json.dumps(features, sort_keys=True).encode()).hexdigest()


def audit(session: Session, actor: str, action: str, reason: str = "", **details) -> None:
    session.add(AuditEntry(ts=utcnow(), actor=actor, action=action, reason=reason, details=details))


# --- model loading ------------------------------------------------------------

_MODEL_CACHE: dict[str, object] = {}


def load_model(row: ModelVersion):
    if row.artifact_sha256 not in _MODEL_CACHE:
        _MODEL_CACHE[row.artifact_sha256] = deserialize(row.artifact)
    return _MODEL_CACHE[row.artifact_sha256]


def active_version(session: Session) -> ModelVersion | None:
    return session.scalar(select(ModelVersion).where(ModelVersion.status == "active"))


def _store_version(session, version, trained, status, parent=None, extra_metrics=None):
    artifact = serialize(trained.model)
    metrics = trained.metrics | (extra_metrics or {})
    row = ModelVersion(
        version=version,
        kind=trained.kind,
        status=status,
        created_at=utcnow(),
        parent_version=parent,
        metrics=metrics,
        card=trained.card | ({"comparison": extra_metrics["comparison"]} if extra_metrics else {}),
        artifact=artifact,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    session.add(row)
    session.flush()
    return row


def _score(metrics: dict, y: dict) -> float:
    """Relative error summed over targets (lower is better)."""
    return sum(metrics[t]["rmse"] / (float(np.std(y[t])) or 1.0) for t in TARGETS)


# --- start-up -----------------------------------------------------------------


def bootstrap(session: Session, settings: Settings) -> None:
    """First start: run the DoE, train both model types, activate the better one."""
    if session.scalar(select(func.count(ModelVersion.id))):
        return
    gauge = np.random.default_rng(settings.seed + 7)
    rows, y = [], {t: [] for t in TARGETS}
    now = utcnow()
    for record in doe_cycles(settings.doe_size, seed=settings.seed):
        payload = cycle_payload(record, machine_id=settings.machine_id)
        features = extract_features(payload["params"], payload["gob_mass_mg"], payload["curves"])
        cycle = Cycle(
            machine_id=settings.machine_id,
            machine_cycle_id=record.cycle_id,
            source="doe",
            recipe="doe",
            produced_at=now,
            received_at=now,
            params=payload["params"],
            gob_mass_mg=payload["gob_mass_mg"],
            curves=payload["curves"],
            features=features,
            issues=[],
            status="ok",
        )
        measured = measure(record.truth, gauge)
        cycle.measurement = Measurement(
            center_thickness_mm=measured.center_thickness_mm,
            pv_um=measured.pv_um,
            source="doe",
            measured_at=now,
        )
        session.add(cycle)
        rows.append(features)
        for t in TARGETS:
            y[t].append(getattr(measured, t))
    audit(
        session,
        "system",
        "doe_imported",
        reason="Einfahrversuche (Latin Hypercube) für das erste Training",
        n=len(rows),
        seed=settings.seed,
    )

    y_arr = {t: np.array(v) for t, v in y.items()}
    trained = {
        kind: train_model(kind, rows, y_arr, seed=settings.seed, sources={"doe": len(rows)})
        for kind in ("baseline", "hybrid")
    }
    for kind, model in trained.items():
        _store_version(session, f"{kind}-v1", model, status="archived")
        audit(
            session,
            "system",
            "model_trained",
            reason="Erstes Training auf den Einfahrversuchen",
            version=f"{kind}-v1",
            holdout={t: round(model.metrics[t]["rmse"], 5) for t in TARGETS},
        )
    best = min(trained, key=lambda k: _score(trained[k].metrics, y_arr))
    activate(
        session,
        f"{best}-v1",
        actor="system",
        reason="Erstinbetriebnahme: kleinster Fehler auf den zurückgehaltenen Versuchen",
    )


# --- cycles and measurements --------------------------------------------------


def _prediction_dict(p: Prediction | None) -> dict | None:
    if p is None:
        return None
    return {
        "model_version": p.model_version,
        "center_thickness_mm": p.center_thickness_mm,
        "pv_um": p.pv_um,
        "in_spec": p.in_spec,
        "out_of_range": p.out_of_range,
        "input_hash": p.input_hash,
        "created_at": _iso(p.created_at),
    }


def _measurement_dict(m: Measurement | None) -> dict | None:
    if m is None:
        return None
    return {
        "center_thickness_mm": m.center_thickness_mm,
        "pv_um": m.pv_um,
        "in_spec": SPEC.is_ok(Quality(m.center_thickness_mm, m.pv_um)),
        "source": m.source,
        "measured_at": _iso(m.measured_at),
    }


def cycle_summary(cycle: Cycle) -> dict:
    return {
        "id": cycle.id,
        "cycle_id": cycle.machine_cycle_id,
        "machine_id": cycle.machine_id,
        "recipe": cycle.recipe,
        "produced_at": _iso(cycle.produced_at),
        "status": cycle.status,
        "issues": cycle.issues,
        "prediction": _prediction_dict(cycle.predictions[-1] if cycle.predictions else None),
        "measurement": _measurement_dict(cycle.measurement),
    }


def _find_cycle(session, machine_id, cycle_id, source="production") -> Cycle | None:
    return session.scalar(
        select(Cycle)
        .where(
            Cycle.machine_id == machine_id,
            Cycle.source == source,
            Cycle.machine_cycle_id == cycle_id,
        )
        .options(selectinload(Cycle.predictions), selectinload(Cycle.measurement))
    )


def _finite_features(params, gob_mass, curves) -> dict | None:
    try:
        features = extract_features(params, gob_mass, curves)
    except (ValueError, ZeroDivisionError):
        return None
    return features if all(math.isfinite(v) for v in features.values()) else None


def ingest_cycle(session: Session, payload: CycleIn) -> tuple[dict, bool]:
    """Store a cycle, check its data quality and predict its quality. Idempotent."""
    existing = _find_cycle(session, payload.machine_id, payload.cycle_id)
    if existing is not None:
        return cycle_summary(existing), False

    params, curves = payload.params.model_dump(), payload.curves.model_dump()
    issues = check_cycle(params, payload.gob_mass_mg, curves)
    status = "quarantined" if has_errors(issues) else ("warning" if issues else "ok")
    features = (
        None if status == "quarantined" else _finite_features(params, payload.gob_mass_mg, curves)
    )
    if features is None:
        status = "quarantined"

    cycle = Cycle(
        machine_id=payload.machine_id,
        machine_cycle_id=payload.cycle_id,
        source="production",
        recipe=payload.recipe,
        produced_at=_naive_utc(payload.produced_at),
        received_at=utcnow(),
        params=params,
        gob_mass_mg=payload.gob_mass_mg,
        curves=_json_safe(curves),
        features=features,
        issues=[issue.as_dict() for issue in issues],
        status=status,
    )
    session.add(cycle)

    version = active_version(session)
    if status != "quarantined" and version is not None:
        predicted = {t: float(v[0]) for t, v in load_model(version).predict([features]).items()}
        cycle.predictions.append(
            Prediction(
                model_version=version.version,
                center_thickness_mm=predicted["center_thickness_mm"],
                pv_um=predicted["pv_um"],
                in_spec=SPEC.is_ok(Quality(predicted["center_thickness_mm"], predicted["pv_um"])),
                out_of_range=out_of_range(version.card, features),
                input_hash=input_hash(features),
                created_at=utcnow(),
            )
        )
    session.commit()
    return cycle_summary(cycle), True


def _json_safe(curves: dict) -> dict:
    """PostgreSQL's JSONB cannot hold NaN/inf; keep them as null."""
    return {
        key: [v if isinstance(v, float) and math.isfinite(v) else None for v in value]
        if isinstance(value, list)
        else value
        for key, value in curves.items()
    }


def add_measurement(session: Session, payload: MeasurementIn) -> tuple[dict, bool]:
    cycle = _find_cycle(session, payload.machine_id, payload.cycle_id)
    if cycle is None:
        raise NotFound(f"Zyklus {payload.cycle_id} von {payload.machine_id} ist unbekannt")
    if cycle.measurement is not None:
        return _measurement_dict(cycle.measurement), False
    cycle.measurement = Measurement(
        center_thickness_mm=payload.center_thickness_mm,
        pv_um=payload.pv_um,
        source=payload.source,
        measured_at=_naive_utc(payload.measured_at),
    )
    session.commit()
    return _measurement_dict(cycle.measurement), True


def list_cycles(session: Session, limit: int = 300, machine_id: str | None = None) -> list[dict]:
    query = (
        select(Cycle)
        .where(Cycle.source == "production")
        .options(selectinload(Cycle.predictions), selectinload(Cycle.measurement))
        .order_by(Cycle.id.desc())
        .limit(limit)
    )
    if machine_id:
        query = query.where(Cycle.machine_id == machine_id)
    return [cycle_summary(c) for c in reversed(session.scalars(query).all())]


def cycle_detail(session: Session, cycle_id: int, machine_id: str) -> dict:
    cycle = _find_cycle(session, machine_id, cycle_id)
    if cycle is None:
        raise NotFound(f"Zyklus {cycle_id} ist unbekannt")
    return cycle_summary(cycle) | {
        "params": cycle.params,
        "gob_mass_mg": cycle.gob_mass_mg,
        "curves": cycle.curves,
        "features": cycle.features,
    }


# --- monitoring ---------------------------------------------------------------


def _last_reset(session: Session) -> datetime | None:
    return session.scalar(
        select(func.max(AuditEntry.ts)).where(AuditEntry.action == "monitoring_reset")
    )


def compute_status(session: Session, settings: Settings) -> dict:
    version = active_version(session)
    last_reset = _last_reset(session)
    data_anchor = last_reset or datetime(1970, 1, 1)
    anchors = [ts for ts in (version.activated_at if version else None, last_reset) if ts]
    perf_anchor = max(anchors) if anchors else datetime(1970, 1, 1)

    usable = select(Cycle).where(
        Cycle.source == "production",
        Cycle.status != "quarantined",
        Cycle.received_at >= data_anchor,
    )
    reference = session.scalars(
        usable.order_by(Cycle.id.asc()).limit(settings.reference_cycles)
    ).all()
    last_ref_id = reference[-1].id if len(reference) == settings.reference_cycles else None
    window = []
    if last_ref_id is not None:
        window = list(
            reversed(
                session.scalars(
                    usable.where(Cycle.id > last_ref_id)
                    .order_by(Cycle.id.desc())
                    .limit(settings.window_cycles)
                ).all()
            )
        )
    drift = data_drift(
        [c.features | {"recipe": c.recipe} for c in reference] if last_ref_id else [],
        [c.features | {"recipe": c.recipe} for c in window],
    )

    performance, perf_ids = {}, []
    if version is not None:
        measured = session.execute(
            select(Cycle.machine_cycle_id, Cycle.recipe, Prediction, Measurement)
            .join(Prediction, Prediction.cycle_id == Cycle.id)
            .join(Measurement, Measurement.cycle_id == Cycle.id)
            .where(
                Cycle.source == "production",
                Prediction.model_version == version.version,
                Prediction.created_at >= perf_anchor,
            )
            .order_by(Cycle.id.asc())
        ).all()
        perf_ids = [row[0] for row in measured]
        recipes = [row[1] for row in measured]
        for t in TARGETS:
            residuals = [getattr(m, t) - getattr(p, t) for _, _, p, m in measured]
            performance[t] = performance_drift(
                residuals, recipes, baseline_n=settings.baseline_n, min_sigma=MIN_SIGMA[t]
            )

    recent = session.scalars(
        select(Cycle)
        .where(Cycle.source == "production")
        .options(selectinload(Cycle.predictions))
        .order_by(Cycle.id.desc())
        .limit(RECENT_PREDICTIONS_WINDOW)
    ).all()
    recent_errors = sum(c.status == "quarantined" for c in recent[:RECENT_ERRORS_WINDOW])
    predictions = [c.predictions[-1] for c in recent if c.predictions]
    scrap_share = float(np.mean([not p.in_spec for p in predictions])) if predictions else 0.0
    oor_share = float(np.mean([bool(p.out_of_range) for p in predictions])) if predictions else 0.0

    recommendation = recommend(
        data=drift,
        performance=performance,
        recent_errors=recent_errors,
        predicted_scrap_share=scrap_share,
        out_of_range_share=oor_share,
    )
    return {
        "generated_at": _iso(utcnow()),
        "recommendation": recommendation.as_dict(),
        "kpis": _kpis(session, recent, scrap_share, performance),
        "data_drift": drift.as_dict()
        | {
            "reference_cycles": [reference[0].machine_cycle_id, reference[-1].machine_cycle_id]
            if last_ref_id
            else None,
            "reference_target": settings.reference_cycles,
            "reference_collected": len(reference),
        },
        "performance": {
            t: report.as_dict()
            | {
                "cusum_cycle_ids": perf_ids[report.baseline_n :] if report.state == "aktiv" else [],
                "alarm_cycle_id": perf_ids[report.alarm_index]
                if report.alarm_index is not None
                else None,
            }
            for t, report in performance.items()
        },
        "active_model": _version_summary(version) if version else None,
        "monitoring_since": _iso(perf_anchor if anchors else None),
        "spec": {
            "pv_max_um": SPEC.pv_max_um,
            "ct_nominal_mm": SPEC.ct_nominal_mm,
            "ct_tolerance_mm": SPEC.ct_tolerance_mm,
        },
    }


def _kpis(session, recent, scrap_share, performance) -> dict:
    production = Cycle.source == "production"
    total = session.scalar(select(func.count(Cycle.id)).where(production)) or 0
    quarantined = (
        session.scalar(
            select(func.count(Cycle.id)).where(production, Cycle.status == "quarantined")
        )
        or 0
    )
    measured = session.scalar(select(func.count(Measurement.id)).join(Cycle).where(production)) or 0
    recent_measured = session.scalars(
        select(Measurement)
        .join(Cycle)
        .where(production)
        .order_by(Measurement.id.desc())
        .limit(RECENT_PREDICTIONS_WINDOW)
    ).all()
    measured_scrap = (
        float(
            np.mean(
                [not SPEC.is_ok(Quality(m.center_thickness_mm, m.pv_um)) for m in recent_measured]
            )
        )
        if recent_measured
        else None
    )
    last = recent[0] if recent else None
    return {
        "cycles": total,
        "measured": measured,
        "quarantined": quarantined,
        "predicted_scrap_share": scrap_share,
        "measured_scrap_share": measured_scrap,
        "mae": {t: r.recent_mae for t, r in performance.items()},
        "last_cycle": cycle_summary(last) if last else None,
    }


# --- model lifecycle ----------------------------------------------------------


def _version_summary(row: ModelVersion) -> dict:
    return {
        "version": row.version,
        "kind": row.kind,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "activated_at": _iso(row.activated_at),
        "parent_version": row.parent_version,
        "metrics": row.metrics,
        "card": row.card,
        "artifact_sha256": row.artifact_sha256,
    }


def list_models(session: Session) -> list[dict]:
    rows = session.scalars(select(ModelVersion).order_by(ModelVersion.id.asc())).all()
    return [_version_summary(r) for r in rows]


def _get_version(session: Session, version: str) -> ModelVersion:
    row = session.scalar(select(ModelVersion).where(ModelVersion.version == version))
    if row is None:
        raise NotFound(f"Modellversion {version} ist unbekannt")
    return row


def activate(
    session: Session, version: str, actor: str, reason: str, action: str = "model_activated"
) -> dict:
    row = _get_version(session, version)
    if row.status == "active":
        raise Conflict(f"{version} ist bereits aktiv")
    if row.status == "rejected":
        raise Conflict(f"{version} wurde abgelehnt und kann nicht aktiviert werden")
    current = active_version(session)
    if current is not None:
        current.status = "archived"
    row.status, row.activated_at = "active", utcnow()
    audit(
        session,
        actor,
        action,
        reason=reason,
        version=version,
        previous=current.version if current else None,
    )
    session.commit()
    return _version_summary(row)


def reject(session: Session, version: str, actor: str, reason: str) -> dict:
    row = _get_version(session, version)
    if row.status != "proposed":
        raise Conflict(f"Nur Vorschläge können abgelehnt werden ({version} ist {row.status})")
    row.status = "rejected"
    audit(session, actor, "model_rejected", reason=reason, version=version)
    session.commit()
    return _version_summary(row)


def rollback(session: Session, actor: str, reason: str) -> dict:
    current = active_version(session)
    previous = session.scalar(
        select(ModelVersion)
        .where(
            ModelVersion.status == "archived",
            ModelVersion.activated_at.is_not(None),
            ModelVersion.version != (current.version if current else ""),
        )
        .order_by(ModelVersion.activated_at.desc())
        .limit(1)
    )
    if previous is None:
        raise Conflict("Es gibt keine frühere aktive Version für einen Rollback")
    return activate(session, previous.version, actor, reason, action="model_rollback")


def _measured_cycles(session, source, limit=None):
    query = (
        select(Cycle)
        .join(Measurement, Measurement.cycle_id == Cycle.id)
        .where(Cycle.source == source, Cycle.status != "quarantined")
        .options(selectinload(Cycle.measurement))
        .order_by(Cycle.id.desc())
    )
    if limit:
        query = query.limit(limit)
    return list(reversed(session.scalars(query).all()))


def propose_retraining(session: Session, settings: Settings, actor: str, reason: str) -> dict:
    """Train a candidate on DoE + recent measured lenses; compare on the newest lenses."""
    current = active_version(session)
    if current is None:
        raise Conflict("Es ist kein Modell aktiv")
    recent = _measured_cycles(session, "production", settings.retrain_window)
    if len(recent) < MIN_RETRAIN_SAMPLES:
        raise Conflict(
            f"Zu wenige gemessene Produktionslinsen ({len(recent)}); "
            f"mindestens {MIN_RETRAIN_SAMPLES} nötig"
        )
    n_holdout = max(5, int(0.2 * len(recent)))
    train_part, holdout = recent[:-n_holdout], recent[-n_holdout:]
    doe = _measured_cycles(session, "doe")
    training = doe + train_part

    def targets(cycles):
        return {t: np.array([getattr(c.measurement, t) for c in cycles]) for t in TARGETS}

    trained = train_model(
        current.kind,
        [c.features for c in training],
        targets(training),
        seed=settings.seed,
        sources={
            "doe": len(doe),
            "produktion": len(train_part),
            "produktion_zyklen": [train_part[0].machine_cycle_id, train_part[-1].machine_cycle_id]
            if train_part
            else None,
        },
    )
    holdout_rows, holdout_y = [c.features for c in holdout], targets(holdout)
    candidate_pred = trained.model.predict(holdout_rows)
    active_pred = load_model(current).predict(holdout_rows)

    def errors(pred):
        result = {}
        for t in TARGETS:
            error = pred[t] - holdout_y[t]
            result[t] = {
                "rmse": float(np.sqrt(np.mean(error**2))),
                "mae": float(np.mean(np.abs(error))),
                "bias": float(np.mean(error)),
            }
        return result

    comparison = {
        "holdout_cycles": [holdout[0].machine_cycle_id, holdout[-1].machine_cycle_id],
        "n": len(holdout),
        "candidate": errors(candidate_pred),
        "active": errors(active_pred),
        "active_version": current.version,
    }
    existing = session.scalars(
        select(ModelVersion.version).where(ModelVersion.kind == current.kind)
    ).all()
    number = 1 + max(int(v.rsplit("-v", 1)[1]) for v in existing)
    row = _store_version(
        session,
        f"{current.kind}-v{number}",
        trained,
        status="proposed",
        parent=current.version,
        extra_metrics={"comparison": comparison},
    )
    audit(
        session,
        actor,
        "retraining_proposed",
        reason=reason,
        version=row.version,
        rmse_pv={
            "kandidat": round(comparison["candidate"]["pv_um"]["rmse"], 4),
            "aktiv": round(comparison["active"]["pv_um"]["rmse"], 4),
        },
    )
    session.commit()
    return _version_summary(row)


def reset_monitoring(session: Session, actor: str, reason: str) -> dict:
    audit(session, actor, "monitoring_reset", reason=reason)
    session.commit()
    return {"status": "ok"}


def list_audit(session: Session, limit: int = 200) -> list[dict]:
    rows = session.scalars(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)).all()
    return [
        {
            "id": r.id,
            "ts": _iso(r.ts),
            "actor": r.actor,
            "action": r.action,
            "reason": r.reason,
            "details": r.details,
        }
        for r in rows
    ]
