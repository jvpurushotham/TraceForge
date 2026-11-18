"""Controlled fault injection for the demo microservices.

Safe by default: the /admin routes only exist when ENABLE_FAULT_INJECTION=true.
Only enable this in a local or isolated test environment.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException, Request
from opentelemetry import trace
from pydantic import BaseModel, Field

logger = logging.getLogger("traceforge.faults")


class FaultConfig(BaseModel):
    delay_ms: int = Field(0, ge=0, le=60000, description="Extra latency in the service's main dependency call")
    db_latency_ms: int = Field(0, ge=0, le=60000, description="Extra latency in the (simulated) database query")
    db_errors: bool = Field(False, description="Simulated connection-pool exhaustion")
    error_rate: float = Field(0.0, ge=0.0, le=1.0, description="Fraction of requests that fail")
    error_status: int = Field(500, ge=400, le=599)
    down: bool = Field(False, description="Return 503 for every request (simulated outage)")
    version: str | None = Field(None, description="Pretend a new version was deployed")


class LeakRequest(BaseModel):
    mb: int = Field(ge=1, le=2048)


def fault_injection_enabled() -> bool:
    return os.getenv("ENABLE_FAULT_INJECTION", "false").lower() in ("1", "true", "yes")


def install(app: FastAPI, service: str, initial_version: str, on_version: Callable[[str], None]) -> None:
    app.state.faults = FaultConfig()
    if not fault_injection_enabled():
        return
    router = APIRouter(prefix="/admin", tags=["fault-injection"])

    @router.get("/fault")
    def get_fault():
        return app.state.faults

    @router.post("/fault")
    def set_fault(cfg: FaultConfig):
        app.state.faults = cfg
        if cfg.version:
            on_version(cfg.version)
        logger.warning("fault configuration changed", extra={"fault": cfg.model_dump()})
        return cfg

    @router.delete("/fault")
    def clear_fault():
        app.state.faults = FaultConfig()
        on_version(initial_version)
        logger.warning("fault configuration cleared")
        return app.state.faults

    app.state.leak = []

    @router.post("/leak")
    def leak(req: LeakRequest):
        """Hold real memory (for the OOMKilled demo). Disabled unless MAX_LEAK_MB > 0; capped by MAX_LEAK_MB per call."""
        cap = int(os.getenv("MAX_LEAK_MB", "0"))
        if cap <= 0:
            raise HTTPException(403, "memory fault disabled (set MAX_LEAK_MB to enable in a test cluster)")
        mb = min(req.mb, cap)
        logger.warning("memory fault: allocating memory", extra={"fault": {"mb": mb}})
        for _ in range(mb):
            app.state.leak.append(bytearray(b"x") * (1024 * 1024))  # filled -> really resident
        return {"allocated_mb_total": len(app.state.leak), "capped_to_mb": mb}

    @router.delete("/leak")
    def free_leak():
        app.state.leak.clear()
        return {"allocated_mb_total": 0}

    @router.post("/crash", status_code=202)
    async def crash():
        """Kill the process (a supervisor / Docker / Kubernetes restarts it)."""
        logger.error("crash requested via admin endpoint")
        asyncio.get_running_loop().call_later(0.3, os._exit, 1)
        return {"detail": "process will exit"}

    app.include_router(router)


def _base_latency() -> float:
    return float(os.getenv("BASE_LATENCY_MS", "20"))


async def simulate_provider(request: Request, span_name: str, **attributes) -> None:
    """Simulate a call to an external provider: base latency + injected delay/errors."""
    f: FaultConfig = request.app.state.faults
    tracer = trace.get_tracer("traceforge.simulation")
    with tracer.start_as_current_span(span_name) as span:
        for k, v in attributes.items():
            span.set_attribute(k, v)
        started = time.perf_counter()
        await asyncio.sleep((_base_latency() + f.delay_ms) / 1000)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms > 500:
            logger.warning(f"{span_name} responded slowly", extra={"duration_ms": elapsed_ms})
        if f.error_rate and random.random() < f.error_rate:
            logger.error(f"{span_name} failed", extra={"error_type": "InjectedError", "status_code": f.error_status})
            raise HTTPException(f.error_status, f"injected fault in {span_name}")


async def simulate_db(request: Request, statement: str) -> None:
    """Simulate a database query (its own span, flagged with db.system)."""
    f: FaultConfig = request.app.state.faults
    tracer = trace.get_tracer("traceforge.simulation")
    with tracer.start_as_current_span("db.query") as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.statement", statement)
        started = time.perf_counter()
        await asyncio.sleep((min(_base_latency(), 10) + f.db_latency_ms) / 1000)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if f.db_errors:
            logger.error("database connection pool exhausted", extra={"error_type": "OperationalError"})
            raise HTTPException(503, "database connection pool exhausted")
        if elapsed_ms > 500:
            logger.warning("database query slow", extra={"duration_ms": elapsed_ms})
