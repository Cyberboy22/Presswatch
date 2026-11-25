"""FastAPI application: REST API, dashboard and operator view."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from presswatch import __version__
from presswatch.api import services
from presswatch.api.schemas import ActionIn, CycleIn, MeasurementIn, SimActionIn
from presswatch.config import Settings
from presswatch.db import init_db, make_engine, make_session_factory

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
log = logging.getLogger("presswatch.api")


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        with session_factory() as session:
            services.bootstrap(session, settings)
        tasks = []
        if settings.embedded_sim:
            from presswatch.runtime import start_embedded_simulation

            tasks = await start_embedded_simulation(settings)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            engine.dispose()

    app = FastAPI(
        title="PressWatch API",
        version=__version__,
        description=(
            "Qualitätsvorhersage und Drift-Überwachung für nicht-isothermes Glaspressen. "
            "Alle Daten stammen aus einem Simulator."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory

    def guarded(call, *args):
        try:
            return call(*args)
        except services.ServiceError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    # --- data in --------------------------------------------------------------

    @app.post("/api/cycles", status_code=201, tags=["Daten"])
    def post_cycle(payload: CycleIn, response: Response, session: SessionDep):
        """Neuer Presszyklus vom Collector (OPC UA → REST). Mehrfaches Senden ist erlaubt."""
        body, created = services.ingest_cycle(session, payload)
        if not created:
            response.status_code = 200
        return body

    @app.post("/api/measurements", status_code=201, tags=["Daten"])
    def post_measurement(payload: MeasurementIn, response: Response, session: SessionDep):
        """Messergebnis der Messstation für einen Zyklus."""
        body, created = guarded(services.add_measurement, session, payload)
        if not created:
            response.status_code = 200
        return body

    # --- data out -------------------------------------------------------------

    @app.get("/api/cycles", tags=["Daten"])
    def get_cycles(session: SessionDep, limit: int = 300):
        return services.list_cycles(session, limit=min(max(limit, 1), 5000))

    @app.get("/api/cycles/{cycle_id}", tags=["Daten"])
    def get_cycle(cycle_id: int, session: SessionDep):
        return guarded(services.cycle_detail, session, cycle_id, settings.machine_id)

    @app.get("/api/status", tags=["Überwachung"])
    def get_status(session: SessionDep):
        """Empfehlung, Kennzahlen, Daten-Drift und Fehler-Drift auf einen Blick."""
        return services.compute_status(session, settings)

    @app.post("/api/monitoring/reset", tags=["Überwachung"])
    def post_reset(action: ActionIn, session: SessionDep):
        """Neue Referenz für die Überwachung, z. B. nach einer Reparatur."""
        return services.reset_monitoring(session, action.actor, action.reason)

    # --- models ---------------------------------------------------------------

    @app.get("/api/models", tags=["Modelle"])
    def get_models(session: SessionDep):
        return services.list_models(session)

    @app.post("/api/models/proposals", status_code=201, tags=["Modelle"])
    def post_proposal(action: ActionIn, session: SessionDep):
        """Retraining-Vorschlag erstellen. Er wird erst nach Freigabe aktiv."""
        return guarded(services.propose_retraining, session, settings, action.actor, action.reason)

    @app.post("/api/models/rollback", tags=["Modelle"])
    def post_rollback(action: ActionIn, session: SessionDep):
        return guarded(services.rollback, session, action.actor, action.reason)

    @app.post("/api/models/{version}/activate", tags=["Modelle"])
    def post_activate(version: str, action: ActionIn, session: SessionDep):
        return guarded(services.activate, session, version, action.actor, action.reason)

    @app.post("/api/models/{version}/reject", tags=["Modelle"])
    def post_reject(version: str, action: ActionIn, session: SessionDep):
        return guarded(services.reject, session, version, action.actor, action.reason)

    @app.get("/api/audit", tags=["Protokoll"])
    def get_audit(session: SessionDep, limit: int = 200):
        return services.list_audit(session, limit=min(max(limit, 1), 1000))

    # --- simulator control (demo only) ----------------------------------------

    def sim_controller():
        if not settings.sim_control:
            raise HTTPException(status_code=403, detail="Simulator-Steuerung ist deaktiviert")
        from presswatch.opcua.control import SimController

        return SimController(settings.opcua_endpoint)

    @app.get("/api/sim", tags=["Simulator"])
    async def get_sim():
        try:
            return await sim_controller().state()
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Zelle nicht erreichbar: {exc}") from exc

    @app.post("/api/sim", tags=["Simulator"])
    async def post_sim(action: SimActionIn, session: SessionDep):
        """Fehler in den Simulator einbauen – den Teil gibt es in einer echten Fabrik nicht."""
        try:
            state = await sim_controller().apply(action.action, action.value)
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Zelle nicht erreichbar: {exc}") from exc
        services.audit(session, "demo", f"sim_{action.action}", value=action.value)
        session.commit()
        return state

    # --- health and pages -----------------------------------------------------

    @app.get("/api/health", tags=["System"])
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/werker", include_in_schema=False)
    def operator_view():
        return FileResponse(STATIC_DIR / "werker.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
