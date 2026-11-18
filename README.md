# TraceForge - AI-assisted distributed incident diagnosis

TraceForge follows a request across microservices, correlates **metrics + traces + logs (+ Kubernetes state)**, ranks the
most likely causes with a deterministic rule engine, and (optionally) asks an LLM to explain the evidence. It never claims a
proven root cause: every finding is a **hypothesis with evidence references**, missing telemetry is reported, and remediation
is only ever *recommended* (a human decides).

```
 load / users -> order-service -> product-service
                      |-------> inventory-service  (simulated DB spans)
                      '-------> payment-service    (simulated provider)
        FastAPI + OpenTelemetry (traces, JSON logs with real trace_id) + Prometheus /metrics
                      |
        OTel Collector -> Tempo (traces) | Loki (logs) ;  Prometheus -> Alertmanager --webhook-->
                      |                                                     |
                  Grafana (4 dashboards)                       incident-service (PostgreSQL)
                                                     collect evidence -> rules -> ranked causes -> optional LLM report
```

## The three phases

| Phase | What you get | How to run | Verify |
|---|---|---|---|
| **1 - Microservices + fault injection** | Product, Inventory, Payment, Order (FastAPI) + PostgreSQL, controlled faults via `/admin/fault` | `docker compose up --build -d` **or** `python scripts/run_local.py` (no Docker) | Swagger at `localhost:8000/docs`, `python scripts/load_generator.py` |
| **2 - Observability** | OTel Collector, Tempo, Loki, Prometheus + alert rules, Alertmanager, Grafana with 4 dashboards | add `-f docker-compose.phase2.yml` | Grafana `localhost:3000`, one trace spans all services, logs link to traces |
| **3 - Incident engine + AI + K8s** | Alertmanager webhook -> incident -> evidence -> ranked causes -> report; incident UI + API; Helm charts, CI, evaluation | add `-f docker-compose.phase3.yml` | `localhost:8010/` UI, `localhost:8010/docs` API |

Details per phase: [`docs/phases/`](docs/phases). **Step-by-step VS Code guide: [`docs/setup-vscode.md`](docs/setup-vscode.md).**

> **Evaluation (INT334) is Kubernetes-centred: follow [`docs/RUNBOOK.md`](docs/RUNBOOK.md) (Minikube + demos) and [`docs/evaluation-prep.md`](docs/evaluation-prep.md) (report, viva).**

## Quick start (Docker Desktop + VS Code)

```bash
python -m venv .venv            # then activate it (see docs/setup-vscode.md)
pip install -r requirements-dev.txt
python -m pytest -q             # 24 tests, no Docker needed
python tests/evaluation/evaluate.py

# whole platform (first build takes a few minutes):
docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml up --build -d
python scripts/load_generator.py --rps 3 --duration 60
```

| URL | What |
|---|---|
| http://localhost:8000/docs | Order service (try `POST /orders` with `{"product_id":"p-1001"}`) |
| http://localhost:8003/docs | Payment service - `POST /admin/fault` injects delay/errors |
| http://localhost:3000 | Grafana (anonymous admin, demo only) - dashboards A-D, Explore -> Tempo / Loki |
| http://localhost:9090 / :9093 | Prometheus / Alertmanager |
| http://localhost:8010/ | **TraceForge incident UI** (API docs at `/docs`) |

## The 5-minute demo

1. Run load: `python scripts/load_generator.py --rps 3 --duration 240`
2. Inject: open http://localhost:8003/docs -> `POST /admin/fault` -> body `{"delay_ms": 3000}` (or run
   `python tests/fault-injection/run_experiment.py --scenario slow_payment` to automate everything and measure it).
3. Watch p95 latency in Grafana dashboard A; the alert appears in Alertmanager within ~30 s.
4. ~20 s after the alert TraceForge opens an incident at http://localhost:8010/ - ranked cause
   `payment-service / slow_downstream_dependency`, evidence refs (trace id, p95 metric, logs), missing evidence, timeline.
5. Click the trace id in Grafana -> Tempo to see payment consuming most of the request; jump to its logs.
6. Remove the fault: `DELETE /admin/fault`. Alerts resolve and the incident closes.

Other scenarios: `python tests/fault-injection/run_experiment.py --list`.

## Optional AI explanation

Default is `LLM_PROVIDER=none` (deterministic template report - the rule engine does the diagnosis either way).
To enable: `cp .env.example .env`, set `LLM_PROVIDER=anthropic` (or `openai` for any OpenAI-compatible API such as Ollama),
`LLM_API_KEY`, `LLM_MODEL`, then recreate `incident-service`. Safeguards: the LLM sees only a compact, redacted evidence
package; output must pass a JSON schema; citations that do not exist in the evidence are dropped; it cannot introduce a
cause the rules did not produce; logs are treated as untrusted text; failures fall back to the template report.

## Repository map

```
services/            product, inventory, payment, order, incident services + common/ (telemetry, faults); one Dockerfile
observability/       otel-collector, prometheus (alert rules), alertmanager, tempo, loki, grafana (provisioning + dashboards)
helm/                demo-app and traceforge charts (Kubernetes)
infrastructure/      kubernetes/ (Minikube installer, observability values), terraform/ (optional EKS)
tests/               integration (services), evaluation (labeled fixtures), fault-injection (live experiments)
scripts/             run_local.py, load_generator.py, gen_dashboards.py
docs/                phases, VS Code guide, architecture, troubleshooting, results
```

## What has and has not been verified

* **Verified in the authoring environment:** unit/integration tests (24 pass), the offline evaluation on 11 labeled
  fixtures (`docs/evaluation-results.md`), all five Python services start and serve under `scripts/run_local.py`,
  fault injection changes behaviour as designed, Docker Compose files parse.
* **Not run there (no Docker daemon / Helm / cloud access) - this includes the new HPA, Ingress, ConfigMap, backup/restore Helm templates:** the container images, the Collector->Tempo/Loki pipeline,
  Prometheus alert firing, Grafana provisioning, Helm charts, Terraform. They follow documented configuration for the pinned
  versions, but expect to fix small things on first run - see `docs/troubleshooting.md`.
* The numbers in `docs/evaluation-results.md` come from hand-built fixtures, **not** from a live system. Run
  `tests/fault-injection/run_experiment.py` to produce real measurements (`docs/live-results.md`).
* Fault injection is for local / isolated test use only. Do not expose these services to the internet.
