"""Phase 1 + 2 integration tests: real HTTP between the four services, in one process.

Checks business flow, fault injection, Prometheus metrics, JSON logging and that ONE
distributed trace spans all services (context propagation).
"""
import importlib.util
import json
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load(service: str):
    path = ROOT / "services" / service / "app" / "main.py"
    spec = importlib.util.spec_from_file_location(f"{service.replace('-', '_')}_main", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


@pytest.fixture(scope="module")
def stack(tmp_path_factory, monkeypatch_module):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from common import telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    mp = monkeypatch_module
    mp.setenv("ENABLE_FAULT_INJECTION", "true")
    mp.setenv("BASE_LATENCY_MS", "0")
    mp.setenv("DATABASE_URL", f"sqlite:///{tmp_path_factory.mktemp('db') / 'orders.db'}")
    mp.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")  # dummy: enables tracing path
    mp.setattr(telemetry, "_init_tracing", lambda *a, **k: (provider, None))
    orig = telemetry._configure_logging
    mp.setattr(telemetry, "_configure_logging", lambda svc, ep, res: orig(svc, None, res))  # no OTLP log export in tests

    ports = {n: free_port() for n in ("product", "inventory", "payment", "order")}
    mp.setenv("PRODUCT_SERVICE_URL", f"http://127.0.0.1:{ports['product']}")
    mp.setenv("INVENTORY_SERVICE_URL", f"http://127.0.0.1:{ports['inventory']}")
    mp.setenv("PAYMENT_SERVICE_URL", f"http://127.0.0.1:{ports['payment']}")

    servers = []
    for name in ("product", "inventory", "payment", "order"):
        app = load(f"{name}-service")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=ports[name], log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
    for s in servers:
        for _ in range(100):
            if s.started:
                break
            time.sleep(0.05)
        assert s.started
    base = {n: f"http://127.0.0.1:{p}" for n, p in ports.items()}
    yield base, exporter
    for s in servers:
        s.should_exit = True


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(autouse=True)
def clear_faults(stack):
    base, exporter = stack
    yield
    for n in ("product", "inventory", "payment"):
        httpx.delete(f"{base[n]}/admin/fault")


def order(base, **kw):
    body = {"product_id": "p-1002", "quantity": 1, **kw}
    return httpx.post(f"{base['order']}/orders", json=body, timeout=10)


def test_happy_path_and_persistence(stack):
    base, _ = stack
    r = order(base)
    assert r.status_code == 201 and r.json()["status"] == "CONFIRMED"
    got = httpx.get(f"{base['order']}/orders/{r.json()['order_id']}")
    assert got.status_code == 200 and got.json()["total_cents"] == 2900


def test_health_ready(stack):
    base, _ = stack
    for n in base:
        assert httpx.get(f"{base[n]}/health").status_code == 200
    assert httpx.get(f"{base['order']}/ready").status_code == 200


def test_business_errors_are_not_5xx(stack):
    base, _ = stack
    assert order(base, product_id="nope").status_code == 404
    assert order(base, quantity=99, product_id="p-1001", payment_token="tok_decline").status_code == 402
    stock = httpx.get(f"{base['inventory']}/inventory/p-1001").json()["available"]
    assert stock == 500  # reservation was released after the decline


def test_one_distributed_trace_across_services(stack):
    base, exporter = stack
    exporter.clear()
    assert order(base).status_code == 201
    time.sleep(0.2)
    spans = exporter.get_finished_spans()
    roots = [s for s in spans if s.name == "POST /orders"]
    assert len(roots) == 1
    tid = roots[0].context.trace_id
    names = {s.name for s in spans if s.context.trace_id == tid}
    for expected in ("GET /products/{product_id}", "POST /inventory/reserve", "POST /payments/process",
                     "payment.provider.call", "db.query", "product.catalog.lookup"):
        assert expected in names, f"{expected} missing from trace; got {names}"
    assert len({s.context.trace_id for s in spans if s.name != "unrelated"}) == 1  # no orphan traces


def test_slow_payment_makes_orders_slow(stack):
    base, _ = stack
    httpx.post(f"{base['payment']}/admin/fault", json={"delay_ms": 400})
    t0 = time.perf_counter()
    assert order(base).status_code == 201
    assert time.perf_counter() - t0 >= 0.4


def test_payment_outage_returns_502_and_releases_stock(stack):
    base, _ = stack
    httpx.post(f"{base['payment']}/admin/fault", json={"down": True})
    assert order(base).status_code == 502
    assert httpx.get(f"{base['inventory']}/inventory/p-1002").json()["available"] == 5000 - _sold(base)


def _sold(base):
    return len([o for o in httpx.get(f"{base['order']}/orders?limit=100").json() if o["status"] == "CONFIRMED"])


def test_inventory_db_pool_exhaustion(stack):
    base, _ = stack
    httpx.post(f"{base['inventory']}/admin/fault", json={"db_errors": True})
    assert order(base).status_code == 502


def test_regression_error_rate_and_version(stack):
    base, _ = stack
    httpx.post(f"{base['payment']}/admin/fault", json={"error_rate": 1.0, "version": "v2.0.0-bad"})
    assert order(base).status_code == 502
    metrics = httpx.get(f"{base['payment']}/metrics").text
    assert 'app_info{service="payment-service",version="v2.0.0-bad"} 1.0' in metrics


def test_prometheus_metrics_exposed(stack):
    base, _ = stack
    order(base)
    text = httpx.get(f"{base['order']}/metrics").text
    assert 'http_requests_total{method="POST",route="/orders",service="order-service",status="201"}' in text
    assert "http_request_duration_seconds_bucket" in text
    if sys.platform.startswith("linux"):  # prometheus_client only exposes process_* metrics on Linux (not macOS/Windows)
        assert "process_start_time_seconds" in text


def test_json_log_carries_real_trace_id():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from common.telemetry import JsonFormatter

    tracer = TracerProvider().get_tracer("t")
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, None)
    with tracer.start_as_current_span("s") as span:
        doc = json.loads(JsonFormatter("svc").format(rec))
        assert doc["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert doc["service"] == "svc" and doc["level"] == "ERROR"
