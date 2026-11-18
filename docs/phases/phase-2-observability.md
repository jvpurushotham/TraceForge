# Phase 2 - Observability stack

**Goal:** follow one request across services and see metrics, traces and logs together.

* OpenTelemetry SDK in every service -> OTel Collector (`observability/otel-collector`) -> **Tempo** (traces) and **Loki** (logs via OTLP). Logs carry the real `trace_id`.
* **Prometheus** scrapes `/metrics` every 5 s; `alert-rules.yml`: HighErrorRate, HighLatencyP95, ServiceDown, ServiceRestarting, HighMemoryUsage, plus meta-monitoring (TelemetryPipelineDown - missing telemetry must not look healthy).
* **Alertmanager** groups alerts; in phase 2 they are visible in its UI (webhook arrives in phase 3).
* **Grafana** (provisioned): A application health, B infrastructure, C distributed tracing, D incident intelligence. Datasources are linked: trace -> logs, log `trace_id` -> trace. Regenerate dashboards with `python scripts/gen_dashboards.py`.

**Run:** `docker compose -f docker-compose.yml -f docker-compose.phase2.yml up --build -d`
**Done when:** one order shows a trace with spans from order, product, inventory and payment; clicking a log line's trace id opens that trace; injecting a payment delay raises an alert in Alertmanager.
