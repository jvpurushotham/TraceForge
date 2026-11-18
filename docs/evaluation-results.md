# Offline evaluation (fixtures)

Computed by `tests/evaluation/evaluate.py` on hand-built fixtures that mimic collector output. These numbers show the rules behave as designed; they are **not** measurements of a live system. Measure the live stack with `tests/fault-injection/run_experiment.py`.

| case | expected | top candidate | status | result |
|---|---|---|---|---|
| 01_slow_payment | payment-service | payment-service/slow_downstream_dependency (0.9) | diagnosed | top-1 |
| 02_inventory_down | inventory-service | inventory-service/service_unavailable (0.65) | diagnosed | top-1 |
| 03_db_latency | inventory-service | inventory-service/database_latency (0.8) | diagnosed | top-1 |
| 04_db_pool_exhausted | inventory-service | inventory-service/database_connection_pressure (0.75) | diagnosed | top-1 |
| 05_pod_crash | payment-service | payment-service/pod_instability (0.9) | diagnosed | top-1 |
| 06_oom | payment-service | payment-service/memory_exhaustion (0.95) | diagnosed | top-1 |
| 07_regression | payment-service | payment-service/deployment_regression (0.8) | diagnosed | top-1 |
| 08_shared_dependency | multiple-services | multiple-services/shared_dependency_or_network (0.7) | diagnosed | top-1 |
| 09_healthy_baseline | - | - | insufficient_evidence | OK (no false positive) |
| 10_missing_telemetry | - | - | insufficient_evidence | OK (flagged insufficient) |
| 11_metrics_only_latency | - | - | insufficient_evidence | OK (flagged insufficient) |

| metric | value |
|---|---|
| incident_cases | 8 |
| top1_accuracy | 1.0 |
| top3_accuracy | 1.0 |
| evidence_validity | 1.0 |
| false_positive_rate | 0.0 |
| insufficient_evidence_handled | 2/2 |
