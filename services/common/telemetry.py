"""Shared observability bootstrap for every TraceForge service.

* structured JSON logs that carry the *current* OpenTelemetry trace_id
* Prometheus metrics (request count, latency histogram, in-flight) on /metrics
* OpenTelemetry traces + logs exported over OTLP/HTTP when
  OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. http://otel-collector:4318)

Without that variable the service still works (tests, plain local runs).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from . import faults

REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "method", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["service", "method", "route"],
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
IN_FLIGHT = Gauge("http_requests_in_flight", "In-flight HTTP requests", ["service"])
APP_INFO = Gauge("app_info", "Running application version", ["service", "version"])

EXEMPT_PATHS = {"/metrics", "/health", "/ready"}
EXTRA_KEYS = ("error_type", "dependency", "order_id", "status_code", "duration_ms", "fault")
_VERSIONS: dict[str, str] = {}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()  # read the REAL trace context
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "version": _VERSIONS.get(self.service, ""),
            "message": record.getMessage(),
            "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else "",
            "span_id": format(ctx.span_id, "016x") if ctx.is_valid else "",
        }
        if record.exc_info and record.exc_info[0]:
            payload["error_type"] = record.exc_info[0].__name__
            payload["exception"] = self.formatException(record.exc_info)
        for key in EXTRA_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, default=str)


def set_version(service: str, version: str) -> None:
    old = _VERSIONS.get(service)
    if old and old != version:
        try:
            APP_INFO.remove(service, old)
        except KeyError:
            pass
    _VERSIONS[service] = version
    APP_INFO.labels(service, version).set(1)


def _configure_logging(service: str, otlp_endpoint: str | None, resource) -> None:
    formatter = JsonFormatter(service)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(formatter)
    root.addHandler(stdout)
    for noisy in ("uvicorn.access", "httpx", "httpcore", "opentelemetry", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{otlp_endpoint}/v1/logs"))
        )

        class JsonOtlpHandler(LoggingHandler):
            """Ship the same JSON document to Loki that goes to stdout."""

            def emit(self, record: logging.LogRecord) -> None:
                if record.name.startswith(("opentelemetry", "urllib3")):
                    return
                rec = copy.copy(record)
                rec.msg, rec.args, rec.exc_info, rec.exc_text = formatter.format(record), None, None, None
                super().emit(rec)

        root.addHandler(JsonOtlpHandler(level=logging.INFO, logger_provider=provider))


def _init_tracing(service: str, version: str, otlp_endpoint: str):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({
        "service.name": service,
        "service.version": version,
        "deployment.environment": os.getenv("DEPLOY_ENV", "local"),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    return provider, resource


def setup(app: FastAPI, service: str, tracer_provider=None) -> None:
    version = os.getenv("APP_VERSION", "v1.0.0")
    otlp_endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").rstrip("/") or None
    resource = None
    if tracer_provider is None and otlp_endpoint:
        tracer_provider, resource = _init_tracing(service, version, otlp_endpoint)
    _configure_logging(service, otlp_endpoint, resource)
    set_version(service, version)
    faults.install(app, service, version, lambda v: set_version(service, v))

    @app.middleware("http")
    async def observe(request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith("/admin"):
            return await call_next(request)
        started = time.perf_counter()
        IN_FLIGHT.labels(service).inc()
        status = 500
        try:
            if app.state.faults.down:
                logging.getLogger(service).error(
                    "service unavailable (simulated outage)", extra={"error_type": "ServiceUnavailable", "status_code": 503}
                )
                response: Response = JSONResponse({"detail": "service unavailable"}, status_code=503)
            else:
                response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            template = getattr(route, "path", "unmatched")
            REQUESTS.labels(service, request.method, template, str(status)).inc()
            LATENCY.labels(service, request.method, template).observe(time.perf_counter() - started)
            IN_FLIGHT.labels(service).dec()

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    if tracer_provider is not None:  # instrument LAST so the OTel middleware is outermost
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=tracer_provider, excluded_urls="/health,/ready,/metrics,/admin"
        )
        HTTPXClientInstrumentor().instrument(tracer_provider=tracer_provider)
