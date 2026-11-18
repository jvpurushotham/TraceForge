"""Live fault-injection experiments against the FULL Docker stack (phases 1+2+3).

For every scenario the script: waits for a quiet system, runs baseline load, injects ONE controlled fault, keeps
sending load, then measures what TraceForge actually did:

  MTTD            injection -> incident created from the Alertmanager webhook
  diagnosis lat.  incident created -> diagnosis stored
  correct@1/@3    injected fault vs. the ranked causes
  evidence valid. fraction of cited refs that exist in the stored evidence
  MTTR            injection -> incident resolved (fault removed after the diagnosis is captured)

    python tests/fault-injection/run_experiment.py --list
    python tests/fault-injection/run_experiment.py --scenario slow_payment
    python tests/fault-injection/run_experiment.py --all          # writes docs/live-results.md

Only run against your LOCAL stack. Numbers are whatever your machine measures - nothing is pre-filled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from load_generator import run as run_load  # noqa: E402

ORDER, INCIDENT = "http://localhost:8000", "http://localhost:8010"
PORTS = {"product-service": 8001, "inventory-service": 8002, "payment-service": 8003, "order-service": 8000}
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.phase2.yml",
           "-f", "docker-compose.phase3.yml"]

# action kinds: fault (POST /admin/fault), stop (docker compose stop), crash (POST /admin/crash x N)
SCENARIOS = {
    "slow_payment": dict(title="Slow payment dependency", service="payment-service",
                         action=("fault", {"delay_ms": 3000}),
                         expect_service="payment-service", expect_causes=["slow_downstream_dependency"]),
    "inventory_outage": dict(title="Inventory service unavailable", service="inventory-service",
                             action=("stop", None),
                             expect_service="inventory-service", expect_causes=["service_unavailable"]),
    "db_latency": dict(title="Inventory database latency", service="inventory-service",
                       action=("fault", {"db_latency_ms": 2500}),
                       expect_service="inventory-service",
                       expect_causes=["database_latency", "database_connection_pressure"]),
    "db_pool_exhausted": dict(title="Inventory DB connection pool exhausted", service="inventory-service",
                              action=("fault", {"db_errors": True}),
                              expect_service="inventory-service",
                              expect_causes=["database_connection_pressure", "database_latency"]),
    "pod_crash": dict(title="Payment service crash loop", service="payment-service",
                      action=("crash", 3),
                      expect_service="payment-service", expect_causes=["pod_instability", "memory_exhaustion"]),
    "regression": dict(title="Faulty release of payment service", service="payment-service",
                       action=("fault", {"version": "v2.0.0-bad", "error_rate": 0.5}),
                       expect_service="payment-service", expect_causes=["deployment_regression"]),
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def open_incidents(c: httpx.Client) -> list[dict]:
    return [i for i in c.get(f"{INCIDENT}/api/v1/incidents", params={"limit": 50}).json() if i["status"] != "resolved"]


def wait_quiet(c: httpx.Client, timeout: float = 360) -> None:
    end = time.time() + timeout
    while open_incidents(c):
        if time.time() > end:
            raise SystemExit("an earlier incident is still open (alerts still firing?) - wait or reset the stack")
        print("  waiting for previous incident to resolve ...")
        asyncio.run(run_load(ORDER, 2, 10, quiet=True))
        time.sleep(5)


def inject(c: httpx.Client, sc: dict) -> None:
    kind, arg = sc["action"]
    svc = sc["service"]
    if kind == "fault":
        c.post(f"http://localhost:{PORTS[svc]}/admin/fault", json=arg).raise_for_status()
        if arg.get("version"):  # tell TraceForge about the (simulated) deployment, like a CD pipeline would
            c.post(f"{INCIDENT}/api/v1/events/deployment",
                   json={"service": svc, "version": arg["version"], "note": "fault-injection experiment"})
    elif kind == "stop":
        subprocess.run(COMPOSE + ["stop", svc], cwd=ROOT, check=True)
    elif kind == "crash":
        for _ in range(arg):
            try:
                c.post(f"http://localhost:{PORTS[svc]}/admin/crash")
            except httpx.HTTPError:
                pass
            time.sleep(12)  # let the container restart


def clear(c: httpx.Client, sc: dict) -> None:
    kind, _ = sc["action"]
    svc = sc["service"]
    if kind == "fault":
        c.delete(f"http://localhost:{PORTS[svc]}/admin/fault")
    elif kind == "stop":
        subprocess.run(COMPOSE + ["start", svc], cwd=ROOT, check=True)


def run_scenario(name: str, sc: dict) -> dict:
    print(f"\n=== {name}: {sc['title']}")
    with httpx.Client(timeout=30) as c:
        wait_quiet(c)
        print("  baseline load (20 s)")
        base = asyncio.run(run_load(ORDER, 3, 20, quiet=True))
        before = {i["incident_id"] for i in c.get(f"{INCIDENT}/api/v1/incidents", params={"limit": 200}).json()}
        t0 = now()
        print(f"  injecting fault: {sc['action']}")
        load_task = None

        async def background():
            await run_load(ORDER, 3, 240, quiet=True)

        loop = asyncio.new_event_loop()
        load_task = loop.create_task(background())
        loop.run_until_complete(loop.run_in_executor(None, inject, c, sc))  # keeps the background load running
        inc = None
        deadline = time.time() + 150
        while time.time() < deadline and inc is None:
            loop.run_until_complete(asyncio.sleep(3))
            for i in c.get(f"{INCIDENT}/api/v1/incidents", params={"limit": 50}).json():
                if i["incident_id"] not in before and ts(i["created_at"]) >= t0:
                    inc = i
        result = {"scenario": name, "title": sc["title"], "baseline": base, "incident_id": None}
        if inc is None:
            print("  NO INCIDENT created within 150 s (alert did not fire / not delivered)")
            clear(c, sc)
            load_task.cancel()
            loop.close()
            return {**result, "detected": False}
        result.update(incident_id=inc["incident_id"], detected=True,
                      mttd_s=round((ts(inc["created_at"]) - t0).total_seconds(), 1))
        print(f"  incident {inc['incident_id']} after {result['mttd_s']} s; waiting for diagnosis ...")
        diag = None
        deadline = time.time() + 180
        while time.time() < deadline and diag is None:
            loop.run_until_complete(asyncio.sleep(3))
            diag = c.get(f"{INCIDENT}/api/v1/incidents/{inc['incident_id']}").json().get("latest_diagnosis")
        if diag:
            ev = c.get(f"{INCIDENT}/api/v1/incidents/{inc['incident_id']}/evidence").json()
            known = {r["id"] for r in ev["references"]}
            cited = [r for cz in diag["suspected_causes"] for r in cz["evidence_refs"]]
            ok = lambda cz: cz["service"] == sc["expect_service"] and cz["cause"] in sc["expect_causes"]  # noqa: E731
            top = diag["suspected_causes"][:3]
            result.update(
                diagnosis_latency_s=round((ts(diag["created_at"]) - ts(inc["created_at"])).total_seconds(), 1),
                status=diag["status"], engine=diag["engine_version"],
                top=[f"{x['service']}/{x['cause']} ({x['confidence']})" for x in top],
                correct_at_1=bool(top and ok(top[0]) and diag["status"] == "diagnosed"),
                correct_at_3=any(ok(x) for x in top),
                evidence_validity=round(sum(r in known for r in cited) / len(cited), 2) if cited else None)
            print(f"  diagnosis: {result['top']} correct@1={result['correct_at_1']}")
        else:
            print("  no diagnosis stored within 180 s")
        print("  removing fault; waiting for recovery ...")
        clear(c, sc)
        deadline = time.time() + 300
        while time.time() < deadline:
            loop.run_until_complete(asyncio.sleep(5))
            cur = c.get(f"{INCIDENT}/api/v1/incidents/{inc['incident_id']}").json()
            if cur["status"] == "resolved":
                result["mttr_s"] = round((ts(cur["resolved_at"]) - t0).total_seconds(), 1)
                break
        load_task.cancel()
        loop.close()
        return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not (a.scenario or a.all):
        for k, v in SCENARIOS.items():
            print(f"{k:<20} {v['title']}")
        return 0
    results = [run_scenario(n, SCENARIOS[n]) for n in (SCENARIOS if a.all else [a.scenario])]
    print(json.dumps(results, indent=2))
    out = ROOT / "docs" / "live-results.md"
    rows = ["| scenario | detected | MTTD s | diagnosis s | top-ranked | @1 | @3 | evidence valid | MTTR s |",
            "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        rows.append(f"| {r['scenario']} | {r.get('detected')} | {r.get('mttd_s', '-')} | {r.get('diagnosis_latency_s', '-')} | "
                    f"{(r.get('top') or ['-'])[0]} | {r.get('correct_at_1', '-')} | {r.get('correct_at_3', '-')} | "
                    f"{r.get('evidence_validity', '-')} | {r.get('mttr_s', '-')} |")
    out.write_text("# Live fault-injection results\n\nMeasured on my machine by `tests/fault-injection/run_experiment.py` "
                   f"({now().isoformat(timespec='seconds')}).\n\n" + "\n".join(rows) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
