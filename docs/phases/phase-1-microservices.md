# Phase 1 - Microservices application + controlled failures

**Goal:** a realistic multi-service app you can break on purpose.

* Services (FastAPI): `product-service:8001`, `inventory-service:8002` (simulated DB spans), `payment-service:8003` (simulated provider), `order-service:8000` (orchestrates the others, PostgreSQL).
* Faults per service via `GET/POST/DELETE /admin/fault`: `delay_ms`, `db_latency_ms`, `db_errors`, `error_rate`, `error_status`, `down`, `version`; `POST /admin/crash`. Only available when `ENABLE_FAULT_INJECTION=true`.
* Business rejections (unknown product, out of stock, declined card) return 4xx and are *not* incidents; dependency failures return 502/504 and release reserved stock.
* Every service: `/health`, `/ready`, `/metrics`, JSON logs, one Dockerfile (`services/Dockerfile`).

**Run:** `docker compose up --build -d` or `python scripts/run_local.py`.
**Done when:** `POST /orders` returns 201; with `{"delay_ms":3000}` on payment the order takes >3 s; `python -m pytest -q` passes.
