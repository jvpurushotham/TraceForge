# Architecture

**Separation of concerns.** Telemetry collection (OTel SDK -> Collector -> Tempo/Loki, Prometheus scraping) is independent of
incident analysis. If `incident-service` is down, telemetry is still collected; if the LLM is down, rule diagnosis still works.

## Request path
`POST /orders` (order) -> `GET /products/{id}` (product) -> `POST /inventory/reserve` (inventory, `db.query` span) ->
`POST /payments/process` (payment, `payment.provider.call` span) -> save order (PostgreSQL). On payment failure the reservation is
released. One trace id spans every service; JSON logs carry the *real* current trace id (read from the OTel context).

## Incident pipeline (incident-service)
1. **Intake** `POST /api/v1/webhooks/alertmanager` (optional Bearer token). Alerts within `GROUP_WINDOW_S` attach to the open incident; resolved alerts resolve it.
2. **Evidence** (after `EVIDENCE_DELAY_S`): Prometheus (p95 vs baseline, error rate, up, restarts, memory), Tempo (slow/failed traces, per-service self time, error leaf spans, DB spans), Loki (error/warn log groups), Kubernetes pods (restarts, OOMKilled; only when `KUBE_ENABLED`), deployment events. Any unavailable source is listed as **missing evidence**.
3. **Rules** (`diagnosis/rules.py`): slow dependency, service unavailable, database latency/connection pressure, pod instability / memory exhaustion, deployment regression, shared dependency/network. Output = ranked hypotheses with a rule-based **evidence score** (not a calibrated probability), strength label, evidence refs; `insufficient_evidence` when signals are weak/conflicting.
4. **Explanation** (`ai/`): redacted compact package -> LLM (optional) -> schema + citation validation -> else template. Always `requires_human_review: true`.
5. **Storage**: PostgreSQL tables `incidents`, `incident_events`, `diagnoses` (with engine version), `evidence_references`, `engineer_feedback`, `deployment_events`.

## API
`GET /health` `GET /ready` `POST|GET /api/v1/incidents` `GET /api/v1/incidents/{id}` `POST .../diagnose[?use_ai=false]`
`GET .../timeline` `GET .../evidence` `POST .../feedback` `POST /api/v1/events/deployment` - interactive docs at `/docs`.

## Security notes
Fault injection (`ENABLE_FAULT_INJECTION`) is demo-only. Secrets via env / Kubernetes Secrets; `.env` is git-ignored. The incident
service has read-only access to telemetry and (in Kubernetes) `get/list pods` in one namespace; it never executes remediation.
Logs are untrusted input to the LLM and are redacted/truncated. Grafana runs with anonymous admin in the demo compose file - do
not expose it.
