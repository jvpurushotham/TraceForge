"""Generate the labeled evaluation set (tests/evaluation/fixtures/*.json).

Each fixture = a hand-built evidence package that mimics what the collectors return for a known
injected fault, plus the ground-truth label.  These are TEST FIXTURES, not measured results.
Run:  python tests/evaluation/build_fixtures.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "incident-service"))
sys.path.insert(0, str(ROOT / "services"))
from app.models.evidence import (DeploymentChange, ErrorSpan, EvidencePackage, EvidenceRef, KubePod,  # noqa: E402
                                 LogFinding, ServiceMetrics, TraceFinding)

T0 = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
SERVICES = ["order-service", "product-service", "inventory-service", "payment-service"]


def metrics(**overrides):
    out = {}
    for s in SERVICES:
        out[s] = ServiceMetrics(service=s, p95_ms=120, baseline_p95_ms=110, error_rate_pct=0, rps=3, up=True, restarts=0, memory_mb=95)
    for s, kw in overrides.items():
        for k, v in kw.items():
            setattr(out[s.replace("_", "-")], k, v)
    return out


def package(name, symptom, metrics_, traces=(), logs=(), pods=(), deploys=(), missing=()):
    p = EvidencePackage(incident_id=f"EVAL-{name}", symptom=symptom, affected_service="order-service", severity="high",
                        window_start=T0, window_end=T0 + timedelta(minutes=3), metrics=metrics_,
                        traces=list(traces), logs=list(logs), kubernetes=list(pods), deployments=list(deploys),
                        missing=list(missing))
    for svc, m in p.metrics.items():
        m.ref = f"metric:{svc}"
        p.refs.append(EvidenceRef(id=m.ref, source_type="metric", resource_reference="prometheus", finding=f"{svc} p95 {m.p95_ms}"))
    for i, t in enumerate(p.traces):
        t.ref = f"trace:{t.trace_id[:8]}"
        p.refs.append(EvidenceRef(id=t.ref, source_type="trace", resource_reference=f"tempo:{t.trace_id}", finding=f"trace {t.trace_id}"))
    for i, lg in enumerate(p.logs):
        lg.ref = f"log:{lg.service}:{i}"
        p.refs.append(EvidenceRef(id=lg.ref, source_type="log", resource_reference="loki", finding=lg.message))
    for k in p.kubernetes:
        k.ref = f"k8s:{k.pod}"
        p.refs.append(EvidenceRef(id=k.ref, source_type="kubernetes", resource_reference=k.pod, finding=k.pod))
    for d in p.deployments:
        d.ref = f"deploy:{d.service}:{d.version}"
        p.refs.append(EvidenceRef(id=d.ref, source_type="deployment", resource_reference="events", finding=d.version))
    return p


def tr(i, root, self_time, db=None, errs=()):
    return TraceFinding(trace_id=f"{i:032x}", root_duration_ms=root, root_service="order-service",
                        self_time_ms=self_time, db_self_time_ms=db or {}, error_spans=list(errs))


L = LogFinding
fixtures = {}

fixtures["01_slow_payment"] = (
    {"service": "payment-service", "cause": ["slow_downstream_dependency"]},
    package("01", "Elevated order latency",
            metrics(order_service=dict(p95_ms=4200, baseline_p95_ms=180, error_rate_pct=2),
                    payment_service=dict(p95_ms=3600, baseline_p95_ms=120)),
            [tr(1, 4100, {"order-service": 150, "payment-service": 3700, "product-service": 30, "inventory-service": 25}),
             tr(2, 4300, {"order-service": 160, "payment-service": 3900, "product-service": 30, "inventory-service": 25})],
            [L(service="payment-service", message="payment.provider.call responded slowly", level="WARNING", count=40)]))

fixtures["02_inventory_down"] = (
    {"service": "inventory-service", "cause": ["service_unavailable"]},
    package("02", "Order failures",
            metrics(order_service=dict(error_rate_pct=95), inventory_service=dict(error_rate_pct=100)),
            [tr(3, 90, {"order-service": 40, "inventory-service": 5},
                errs=[ErrorSpan(service="inventory-service", name="POST /inventory/reserve", duration_ms=3, message="503"),
                      ErrorSpan(service="order-service", name="POST", duration_ms=10, peer="inventory-service", message="503")])],
            [L(service="order-service", message="Inventory service returned 503", error_type="HTTPStatusError",
               dependency="inventory-service", count=60),
             L(service="inventory-service", message="service unavailable (simulated outage)", error_type="ServiceUnavailable", count=60)]))

fixtures["03_db_latency"] = (
    {"service": "inventory-service", "cause": ["database_latency", "database_connection_pressure"]},
    package("03", "Elevated order latency",
            metrics(order_service=dict(p95_ms=3300, baseline_p95_ms=180), inventory_service=dict(p95_ms=3100, baseline_p95_ms=100)),
            [tr(4, 3400, {"order-service": 120, "inventory-service": 40, "payment-service": 40}, db={"inventory-service": 3050}),
             tr(5, 3300, {"order-service": 110, "inventory-service": 35, "payment-service": 40}, db={"inventory-service": 2990})],
            [L(service="inventory-service", message="database query slow", level="WARNING", count=30)]))

fixtures["04_db_pool_exhausted"] = (
    {"service": "inventory-service", "cause": ["database_connection_pressure", "database_latency"]},
    package("04", "Order failures",
            metrics(order_service=dict(error_rate_pct=80), inventory_service=dict(error_rate_pct=100)),
            [tr(6, 80, {"order-service": 40, "inventory-service": 8}, db={"inventory-service": 20},
                errs=[ErrorSpan(service="inventory-service", name="db.query", duration_ms=20, message="database connection pool exhausted", is_db=True),
                      ErrorSpan(service="inventory-service", name="POST /inventory/reserve", duration_ms=30, message="503")])],
            [L(service="inventory-service", message="database connection pool exhausted", error_type="OperationalError", count=50),
             L(service="order-service", message="Inventory service returned 503", error_type="HTTPStatusError",
               dependency="inventory-service", count=50)]))

fixtures["05_pod_crash"] = (
    {"service": "payment-service", "cause": ["pod_instability", "memory_exhaustion"]},
    package("05", "Workload instability (restarts)",
            metrics(order_service=dict(error_rate_pct=40), payment_service=dict(restarts=3, up=False)),
            [], [L(service="order-service", message="Payment service connection failed: connection refused or unreachable",
                   error_type="ConnectError", dependency="payment-service", count=25)]))

fixtures["06_oom"] = (
    {"service": "payment-service", "cause": ["memory_exhaustion"]},
    package("06", "Workload instability (restarts)",
            metrics(payment_service=dict(restarts=2, memory_mb=480)), [], [],
            pods=[KubePod(service="payment-service", pod="payment-service-7d9-abc", restarts=2, last_termination_reason="OOMKilled")]))

fixtures["07_regression"] = (
    {"service": "payment-service", "cause": ["deployment_regression"]},
    package("07", "Elevated error rate",
            metrics(order_service=dict(error_rate_pct=35), payment_service=dict(error_rate_pct=40)),
            [tr(7, 120, {"order-service": 50, "payment-service": 20},
                errs=[ErrorSpan(service="payment-service", name="payment.provider.call", duration_ms=20, message="injected fault in payment.provider.call")])],
            [L(service="payment-service", message="payment.provider.call failed", error_type="InjectedError", count=70),
             L(service="order-service", message="Payment service returned 500", error_type="HTTPStatusError", dependency="payment-service", count=70)],
            deploys=[DeploymentChange(service="payment-service", version="v2.0.0-bad", timestamp=T0 - timedelta(minutes=2))]))

fixtures["08_shared_dependency"] = (
    {"service": "multiple-services", "cause": ["shared_dependency_or_network"]},
    package("08", "Elevated error rate",
            metrics(order_service=dict(error_rate_pct=70), payment_service=dict(error_rate_pct=30, p95_ms=5200),
                    inventory_service=dict(error_rate_pct=30, p95_ms=5100)),
            [], [L(service="order-service", message="Payment service request timed out", error_type="TimeoutError", dependency="payment-service", count=30),
                 L(service="order-service", message="Inventory service request timed out", error_type="TimeoutError", dependency="inventory-service", count=30)]))

fixtures["09_healthy_baseline"] = (
    {"service": None, "cause": [], "expect_no_incident": True},
    package("09", "Normal window (false-positive check)", metrics(), [], [],
            missing=["traces: no slow or failed traces found in the incident window",
                     "logs: no ERROR/WARNING logs found in the incident window"]))

fixtures["10_missing_telemetry"] = (
    {"service": None, "cause": [], "expect_insufficient": True},
    package("10", "Elevated order latency", {}, [], [],
            missing=["metrics: Prometheus query failed", "traces: Tempo search failed", "logs: Loki query failed"]))

fixtures["11_metrics_only_latency"] = (
    {"service": None, "cause": [], "expect_insufficient": True},
    package("11", "Elevated order latency", metrics(order_service=dict(p95_ms=2500, baseline_p95_ms=200)), [], [],
            missing=["traces: Tempo search failed", "logs: Loki query failed"]))

out = Path(__file__).parent / "fixtures"
out.mkdir(exist_ok=True)
for name, (label, pkg) in fixtures.items():
    (out / f"{name}.json").write_text(json.dumps({"name": name, "label": label, "package": json.loads(pkg.model_dump_json())}, indent=1))
print(f"wrote {len(fixtures)} fixtures")
