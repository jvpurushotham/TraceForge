"""Generate the four Grafana dashboards (A-D) as provisioned JSON files.

Run:  python scripts/gen_dashboards.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "observability" / "grafana" / "dashboards"
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}
_id = 0


def _next():
    global _id
    _id += 1
    return _id


def ts(title, exprs, x, y, w=12, h=8, unit="short", ds=PROM):
    return {"id": _next(), "type": "timeseries", "title": title, "datasource": ds,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": l, "datasource": ds}
                        for i, (e, l) in enumerate(exprs)]}


def stat(title, expr, x, y, w=6, h=4, unit="short", ds=PROM):
    return {"id": _next(), "type": "stat", "title": title, "datasource": ds,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "targets": [{"refId": "A", "expr": expr, "datasource": ds}]}


def logs(title, expr, x, y, w=24, h=10):
    return {"id": _next(), "type": "logs", "title": title, "datasource": LOKI,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending"},
            "targets": [{"refId": "A", "expr": expr, "datasource": LOKI}]}


def traces(title, q, x, y, w=12, h=9):
    return {"id": _next(), "type": "table", "title": title, "datasource": TEMPO,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"refId": "A", "datasource": TEMPO, "queryType": "traceql", "query": q,
                         "limit": 20, "tableType": "traces"}]}


def text(title, md, x, y, w=24, h=4):
    return {"id": _next(), "type": "text", "title": title, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": md}}


def dash(uid, title, panels):
    return {"uid": uid, "title": title, "schemaVersion": 39, "version": 1, "refresh": "5s",
            "time": {"from": "now-15m", "to": "now"}, "tags": ["traceforge"], "panels": panels,
            "templating": {"list": []}, "annotations": {"list": []}}


RATE = "sum by (service) (rate(http_requests_total[1m]))"
ERR = ('sum by (service) (rate(http_requests_total{status=~"5.."}[1m])) / '
       'sum by (service) (rate(http_requests_total[1m]))')
P95 = "histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[1m])))"
P50 = "histogram_quantile(0.50, sum by (le, service) (rate(http_request_duration_seconds_bucket[1m])))"
P99 = "histogram_quantile(0.99, sum by (le, service) (rate(http_request_duration_seconds_bucket[1m])))"

A = dash("tf-app-health", "A - Application health", [
    stat("Order error rate (1m)", 'sum(rate(http_requests_total{service="order-service",status=~"5.."}[1m])) / sum(rate(http_requests_total{service="order-service"}[1m]))', 0, 0, unit="percentunit"),
    stat("Order p95 latency", 'histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service="order-service"}[1m])))', 6, 0, unit="s"),
    stat("Order throughput", 'sum(rate(http_requests_total{service="order-service"}[1m]))', 12, 0, unit="reqps"),
    stat("Services up", 'sum(up{job="traceforge"})', 18, 0),
    ts("Request rate", [(RATE, "{{service}}")], 0, 4, unit="reqps"),
    ts("Error rate", [(ERR, "{{service}}")], 12, 4, unit="percentunit"),
    ts("Latency p50 / p95 / p99 (order-service)", [
        ('histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket{service="order-service"}[1m])))', "p50"),
        ('histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service="order-service"}[1m])))', "p95"),
        ('histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{service="order-service"}[1m])))', "p99")], 0, 12, unit="s"),
    ts("p95 latency by service", [(P95, "{{service}}")], 12, 12, unit="s"),
    ts("In-flight requests", [("sum by (service) (http_requests_in_flight)", "{{service}}")], 0, 20),
    ts("Running version", [("app_info", "{{service}} {{version}}")], 12, 20),
])

B = dash("tf-infra", "B - Runtime / Kubernetes infrastructure", [
    ts("Memory (RSS) by service", [("process_resident_memory_bytes{job=\"traceforge\"}", "{{service}}")], 0, 0, unit="bytes"),
    ts("CPU seconds / s by service", [("rate(process_cpu_seconds_total{job=\"traceforge\"}[1m])", "{{service}}")], 12, 0),
    ts("Restarts (changes in process start time, 10m)", [("changes(process_start_time_seconds{job=\"traceforge\"}[10m])", "{{service}}")], 0, 8),
    ts("Scrape target up", [("up{job=\"traceforge\"}", "{{service}}")], 12, 8),
    ts("Open file descriptors", [("process_open_fds{job=\"traceforge\"}", "{{service}}")], 0, 16),
    text("On Kubernetes", "With kube-state-metrics / cAdvisor you can add `kube_pod_container_status_restarts_total` and "
         "`container_memory_working_set_bytes` panels here. The incident engine also reads pod state directly via the "
         "Kubernetes API (`KUBE_ENABLED=true`).", 12, 16, h=8),
])

C = dash("tf-tracing", "C - Distributed tracing", [
    traces("Slow traces (> 1s)", "{ duration > 1s }", 0, 0),
    traces("Failed traces", "{ status = error }", 12, 0),
    ts("Latency by route (p95)", [("histogram_quantile(0.95, sum by (le, service, route) (rate(http_request_duration_seconds_bucket[1m])))", "{{service}} {{route}}")], 0, 9, unit="s"),
    ts("Requests by route and status", [("sum by (service, route, status) (rate(http_requests_total[1m]))", "{{service}} {{route}} {{status}}")], 12, 9, unit="reqps"),
    logs("Errors and warnings (click a trace_id to jump to the trace)", '{service_name=~".+"} | json | level=~"ERROR|WARNING"', 0, 17),
])

D = dash("tf-incidents", "D - Incident intelligence", [
    stat("Open incidents", "sum(traceforge_incidents_open) or vector(0)", 0, 0),
    stat("Incidents created (total)", "sum(traceforge_incidents_created_total) or vector(0)", 6, 0),
    stat("Diagnoses run", "sum(traceforge_diagnoses_total) or vector(0)", 12, 0),
    stat("Median diagnosis time", "histogram_quantile(0.5, sum by (le) (rate(traceforge_diagnosis_duration_seconds_bucket[1h])))", 18, 0, unit="s"),
    ts("Incidents created by service", [("sum by (affected_service) (increase(traceforge_incidents_created_total[5m]))", "{{affected_service}}")], 0, 4),
    ts("Top-ranked causes", [("sum by (service, cause) (traceforge_top_cause_total)", "{{service}} / {{cause}}")], 12, 4),
    ts("Diagnoses by source and status", [("sum by (source, status) (traceforge_diagnoses_total)", "{{source}} {{status}}")], 0, 12),
    ts("Diagnosis duration p95", [("histogram_quantile(0.95, sum by (le) (rate(traceforge_diagnosis_duration_seconds_bucket[5m])))", "p95")], 12, 12, unit="s"),
    text("Incident console", "Evidence-linked incident reports, timeline and engineer feedback: **http://localhost:8010/** "
         "(API docs: http://localhost:8010/docs)", 0, 20, h=3),
])

OUT.mkdir(parents=True, exist_ok=True)
for name, d in {"a-application-health": A, "b-infrastructure": B, "c-distributed-tracing": C, "d-incident-intelligence": D}.items():
    (OUT / f"{name}.json").write_text(json.dumps(d, indent=1))
    print("wrote", name)
