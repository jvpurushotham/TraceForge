# Phase 3 - Incident engine, AI explanation, Kubernetes and CI

**Goal:** turn an alert into an evidence-backed investigation.

* `incident-service` (FastAPI + PostgreSQL): webhook -> incident + timeline -> evidence collectors -> rule-based ranking -> optional LLM explanation -> UI (`/`) and REST API.
* Honest by design: hypotheses vs confirmed facts, `insufficient_evidence`, missing-evidence list, evidence score is not a probability, human review required, no automatic remediation.
* AI layer is optional and guarded (schema validation, citation checking, redaction, untrusted logs, template fallback).
* Evaluation: `python tests/evaluation/evaluate.py` (11 labeled fixtures: top-1/top-3 accuracy, evidence validity, false positives, insufficient-evidence handling) and live `tests/fault-injection/run_experiment.py` (MTTD, diagnosis latency, accuracy, MTTR).
* Deployment: `helm/demo-app`, `helm/traceforge`, `infrastructure/kubernetes` (Minikube installer), optional `infrastructure/terraform` (EKS), `.github/workflows/ci.yml`.

**Run:** `docker compose -f docker-compose.yml -f docker-compose.phase2.yml -f docker-compose.phase3.yml up --build -d`
**Done when:** the slow-payment fault creates an incident whose top hypothesis is `payment-service / slow_downstream_dependency` with verifiable evidence refs.
