"""TraceForge Incident Service: alert intake -> evidence -> ranked causes -> (optional) AI explanation."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from common import telemetry

from .api.routes import router
from .collectors.kube import KubeCollector
from .collectors.loki import LokiCollector
from .collectors.prometheus import PrometheusCollector
from .collectors.tempo import TempoCollector
from .config import Settings
from .correlation.evidence import EvidenceBuilder
from .db import Database
from .service import IncidentService

UI = Path(__file__).parent / "ui" / "index.html"


def create_app(settings: Settings | None = None, builder: EvidenceBuilder | None = None, llm=None,
               instrument: bool = True) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="TraceForge Incident Service", version="1.0.0")
    if instrument:
        telemetry.setup(app, "incident-service")
    db = Database(settings.database_url)
    db.init()
    builder = builder or EvidenceBuilder(
        settings, PrometheusCollector(settings.prometheus_url, rate_window_s=settings.rate_window_s),
        TempoCollector(settings.tempo_url), LokiCollector(settings.loki_url),
        KubeCollector(settings.kube_namespace, settings.kube_enabled))
    app.state.settings, app.state.db = settings, db
    app.state.service = IncidentService(settings, db, builder, llm)
    app.include_router(router)

    @app.on_event("startup")
    async def bind_loop():
        app.state.service.bind_loop(asyncio.get_running_loop())

    @app.get("/", include_in_schema=False)
    def ui():
        app.state.service.refresh_gauges()
        return FileResponse(UI)

    @app.middleware("http")
    async def gauges(request, call_next):
        if request.url.path == "/metrics":
            app.state.service.refresh_gauges()
        return await call_next(request)

    return app


app = create_app()
