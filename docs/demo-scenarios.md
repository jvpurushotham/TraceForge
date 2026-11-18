# Failure scenarios

Run any of them automatically with `python tests/fault-injection/run_experiment.py --scenario <name>` (needs all three phases),
or by hand through each service's Swagger page (`/docs` -> `POST /admin/fault`). Always clear with `DELETE /admin/fault`.

| Scenario | Inject | Expected top hypothesis |
|---|---|---|
| `slow_payment` | payment `{"delay_ms": 3000}` | payment-service / slow_downstream_dependency |
| `inventory_outage` | `docker compose ... stop inventory-service` | inventory-service / service_unavailable |
| `db_latency` | inventory `{"db_latency_ms": 2500}` | inventory-service / database_latency |
| `db_pool_exhausted` | inventory `{"db_errors": true}` | inventory-service / database_connection_pressure |
| `pod_crash` | `POST /admin/crash` on payment, repeated | payment-service / pod_instability |
| `regression` | payment `{"version":"v2.0.0-bad","error_rate":0.5}` + deployment event | payment-service / deployment_regression |

Memory exhaustion (OOMKilled) evidence only exists on Kubernetes when a pod hits its memory limit; there is no memory-leak injector
by design. It is covered by an evaluation fixture. Missing telemetry: stop `tempo` or `loki` and re-run diagnosis - TraceForge must
list the gap and lower its strength rather than guess.
