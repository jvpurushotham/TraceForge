# Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `port is already allocated` | Another program uses 8000-8003, 8010, 3000, 3100, 3200, 9090 or 9093. Stop it or edit the left side of the `ports:` mapping. |
| Containers restart / exit with 137 | Docker is out of memory. Docker Desktop > Settings > Resources > 6 GB+. |
| `docker compose` can't find phase2 services | You forgot `-f docker-compose.phase2.yml` (always pass the same set of `-f` files to `up` and `down`). |
| Grafana dashboards are empty | Run load first. Phase 3 dashboard (D) only has data when the incident service runs. Time range top-right: *Last 15 minutes*. |
| No traces in Tempo | `docker compose logs otel-collector tempo`. Traces appear ~10-15 s after the request. Check services have `OTEL_EXPORTER_OTLP_ENDPOINT` (phase 2 file sets it). |
| No logs in Loki | `docker compose logs otel-collector loki`. The collector forwards OTLP logs to `http://loki:3100/otlp`; Loki >= 3 is required. |
| Tempo/Loki "permission denied" on a volume | `docker compose down -v` and start again (stale volumes from another version). |
| No alert / no incident | Prometheus UI (localhost:9090/alerts) shows rule state. Rules need >0.2 req/s of load and `for: 10s`. Alertmanager UI shows grouped alerts; in phase 3 check `docker compose logs incident-service`. |
| Incident says "insufficient evidence" | Intended when telemetry is missing. Read the *Missing evidence* list - e.g. traces not yet in Tempo (retry *Re-run diagnosis* after ~30 s) or running without Docker. |
| Incident created but never diagnosed | Diagnosis starts `EVIDENCE_DELAY_S` (20 s) after the alert. Use *Re-run diagnosis* in the UI or `POST /api/v1/incidents/{id}/diagnose`. |
| A second fault doesn't create a new incident | Alerts within `GROUP_WINDOW_S` (10 min) attach to the open incident by design. Wait for alerts to resolve, or reset: `docker compose ... down -v`. |
| LLM report shows `template` | `LLM_PROVIDER=none`, a missing key, or the model output failed validation -> safe fallback. See `warnings` in the report / incident-service logs. |
| Windows: `.venv\Scripts\Activate.ps1` blocked | `Set-ExecutionPolicy -Scope Process RemoteSigned` in that terminal. |
| `ModuleNotFoundError: common` when running a service by hand | Set `PYTHONPATH=services` (or use `scripts/run_local.py`). |
| Helm / Minikube step fails | Chart value keys drift between versions; `helm show values <chart>` and adjust `infrastructure/kubernetes/observability/*.yaml`. The Kubernetes part was not executed by the generator. |
