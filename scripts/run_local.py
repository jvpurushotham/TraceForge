"""Run the TraceForge demo services directly with Python (no Docker).  Phase 1 (+ optional incident service).

    python scripts/run_local.py                 # product, inventory, payment, order on ports 8001/8002/8003/8000
    python scripts/run_local.py --with-incident # also the incident service on 8010 (no Prometheus/Tempo/Loki -> it will
                                                # report missing telemetry; use Docker for the full stack)
State (SQLite files) goes to ./.local/.  Ctrl+C stops everything.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SERVICES = [("product-service", 8001), ("inventory-service", 8002), ("payment-service", 8003), ("order-service", 8000)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-incident", action="store_true")
    args = ap.parse_args()
    state = ROOT / ".local"
    state.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "services"), "ENABLE_FAULT_INJECTION": "true",
           "PRODUCT_SERVICE_URL": "http://127.0.0.1:8001", "INVENTORY_SERVICE_URL": "http://127.0.0.1:8002",
           "PAYMENT_SERVICE_URL": "http://127.0.0.1:8003", "HTTP_TIMEOUT_S": "5", "PYTHONUNBUFFERED": "1"}
    todo = list(SERVICES) + ([("incident-service", 8010)] if args.with_incident else [])
    procs = []
    for name, port in todo:
        e = dict(env, PORT=str(port))
        e["DATABASE_URL"] = f"sqlite:///{(state / (name + '.db')).as_posix()}"
        procs.append((name, port, subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "services" / name),
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], cwd=state, env=e)))

    def stop(*_):
        for _, _, p in procs:
            p.terminate()
        for _, _, p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    for name, port, p in procs:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1).status_code == 200:
                    print(f"  up   {name:<18} http://127.0.0.1:{port}/docs")
                    break
            except httpx.HTTPError:
                if p.poll() is not None:
                    print(f"  FAILED {name} exited with {p.returncode}")
                    stop()
                time.sleep(0.5)
        else:
            print(f"  FAILED {name} did not become healthy")
            stop()
    print("\nAll services running. Try:\n  python scripts/load_generator.py --rps 3 --duration 15\n"
          "Press Ctrl+C to stop.")
    while True:
        time.sleep(1)
        for name, _, p in procs:
            if p.poll() is not None:
                print(f"{name} exited with {p.returncode}; stopping")
                stop()


if __name__ == "__main__":
    raise SystemExit(main())
