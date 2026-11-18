"""Deterministic diagnostic rules.  Each rule returns hypotheses (never proofs) with the evidence behind them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models.evidence import EvidencePackage, LogFinding, ServiceMetrics, TraceFinding

SLOW_RATIO = 3.0       # p95 must be >= 3x the pre-incident baseline ...
SLOW_MIN_MS = 1000.0   # ... and at least this slow in absolute terms
ERR_PCT = 5.0          # 5xx rate considered abnormal
SHARE = 0.5            # a service owning >=50% of a slow request's time dominates it
DB_SHARE = 0.4

TIMEOUT_RE = re.compile(r"timed out|timeout|responded slowly", re.I)
CONNECT_RE = re.compile(r"connection (failed|refused)|unreachable|ConnectError|ConnectTimeout|"
                        r"service unavailable|returned 50[23]", re.I)
DB_RE = re.compile(r"database|connection pool|OperationalError|db\.", re.I)


@dataclass
class Candidate:
    service: str
    cause: str
    score: float = 0.0
    rationale: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)

    def add(self, pts: float, why: str, *refs: str) -> None:
        self.score += pts
        self.rationale.append(why)
        for r in refs:
            if r and r not in self.refs:
                self.refs.append(r)


def _slow_traces(pkg: EvidencePackage) -> list[TraceFinding]:
    return [t for t in pkg.traces if t.root_duration_ms >= SLOW_MIN_MS]


def _share(t: TraceFinding, svc: str, db: bool = False) -> float:
    ms = (t.db_self_time_ms if db else t.self_time_ms).get(svc, 0.0)
    return min(1.0, ms / t.root_duration_ms) if t.root_duration_ms > 0 else 0.0


def _dominated(pkg, svc, thr, db=False) -> tuple[float, list[TraceFinding]]:
    slow = _slow_traces(pkg)
    hits = [t for t in slow if _share(t, svc, db) >= thr]
    return (len(hits) / len(slow) if slow else 0.0), hits


def _elevated(m: ServiceMetrics | None) -> bool:
    if not m or m.p95_ms is None:
        return False
    floor = SLOW_MIN_MS
    if m.baseline_p95_ms:
        floor = max(floor, SLOW_RATIO * m.baseline_p95_ms)
    return m.p95_ms >= floor


def _erroring(m: ServiceMetrics | None) -> bool:
    return bool(m and m.error_rate_pct is not None and m.error_rate_pct >= ERR_PCT)


def _logs(pkg: EvidencePackage, svc: str, pattern: re.Pattern, exclude_db: bool = False) -> list[LogFinding]:
    out = []
    for lg in pkg.logs:
        if (lg.dependency or lg.service) != svc:  # a caller's log about a failing dependency belongs to the dependency
            continue
        text = f"{lg.message} {lg.error_type}"
        if pattern.search(text) and not (exclude_db and DB_RE.search(text)):
            out.append(lg)
    return out


def _error_spans(pkg: EvidencePackage, svc: str, db: bool | None = None):
    out = []
    for t in pkg.traces:
        for e in t.error_spans:
            if (e.peer or e.service) == svc and (db is None or e.is_db == db):
                out.append((t, e))
    return out


def rule_slow_dependency(pkg: EvidencePackage) -> list[Candidate]:
    out = []
    for svc in pkg.services():
        cause = "slow_internal_processing" if svc == pkg.affected_service else "slow_downstream_dependency"
        c = Candidate(svc, cause)
        sup, hits = _dominated(pkg, svc, SHARE)
        if hits:
            pts = 0.40 if sup >= 0.5 else 0.25
            c.add(pts, f"{svc} owns >=50% of the request time in {len(hits)}/{len(_slow_traces(pkg))} slow trace(s)",
                  *[t.ref for t in hits[:3]])
        m = pkg.metrics.get(svc)
        if _elevated(m):
            base = f" vs baseline {m.baseline_p95_ms:.0f} ms" if m.baseline_p95_ms else ""
            c.add(0.25, f"{svc} p95 latency {m.p95_ms:.0f} ms{base}", m.ref)
        lg = _logs(pkg, svc, TIMEOUT_RE, exclude_db=True)
        if lg:
            c.add(0.20, f"timeout/slow-response logs naming {svc} ({sum(x.count for x in lg)} occurrences)",
                  *[x.ref for x in lg[:2]])
        if c.score and svc != pkg.affected_service and _elevated(pkg.metrics.get(pkg.affected_service)):
            c.add(0.05, f"upstream {pkg.affected_service} is slow too (consistent with a downstream cause)",
                  pkg.metrics[pkg.affected_service].ref)
        if not hits and not (_elevated(m) and lg):
            continue
        if c.score >= 0.3:
            out.append(c)
    return out


def rule_unavailable(pkg: EvidencePackage) -> list[Candidate]:
    out = []
    for svc in pkg.services():
        c = Candidate(svc, "service_errors")
        m = pkg.metrics.get(svc)
        if m and m.up is False:
            c.add(0.35, f"{svc} is not up (scrape target down)", m.ref)
            c.cause = "service_unavailable"
        conn = _logs(pkg, svc, CONNECT_RE)
        if conn:
            c.add(0.25, f"connection/unavailable errors involving {svc} ({sum(x.count for x in conn)} occurrences)",
                  *[x.ref for x in conn[:2]])
            if any(re.search(r"connection|unreachable|Connect|unavailable|50[23]", f"{x.message} {x.error_type}", re.I) for x in conn):
                c.cause = "service_unavailable"
        spans = _error_spans(pkg, svc)
        if spans:
            c.add(0.25, f"{len(spans)} failed span(s) in or towards {svc}", *sorted({t.ref for t, _ in spans})[:3])
        if _erroring(m):
            c.add(0.15, f"{svc} 5xx rate {m.error_rate_pct:.0f}%", m.ref)
        if c.score >= 0.25 and (conn or spans or (m and m.up is False)):
            out.append(c)
    return out


def rule_database(pkg: EvidencePackage) -> list[Candidate]:
    out = []
    for svc in pkg.services():
        c = Candidate(svc, "database_latency")
        sup, hits = _dominated(pkg, svc, DB_SHARE, db=True)
        if hits:
            c.add(0.40 if sup >= 0.5 else 0.25, f"database spans own >=40% of request time in {len(hits)} slow trace(s) of {svc}",
                  *[t.ref for t in hits[:3]])
        dberr = _error_spans(pkg, svc, db=True)
        if dberr:
            c.add(0.35, f"{len(dberr)} failed database span(s) in {svc}", *sorted({t.ref for t, _ in dberr})[:3])
            c.cause = "database_connection_pressure"
        dblogs = _logs(pkg, svc, DB_RE)
        if dblogs:
            c.add(0.25, f"database-related warnings/errors in {svc} logs ({sum(x.count for x in dblogs)} occurrences)",
                  *[x.ref for x in dblogs[:2]])
            if any(re.search(r"pool|OperationalError|connection", f"{x.message} {x.error_type}", re.I) for x in dblogs):
                c.cause = "database_connection_pressure"
        m = pkg.metrics.get(svc)
        if c.score and (_elevated(m) or _erroring(m)):
            c.add(0.15, f"{svc} shows elevated latency/errors at the service level", m.ref)
        if c.score >= 0.3:
            out.append(c)
    return out


def rule_pod_instability(pkg: EvidencePackage) -> list[Candidate]:
    out = []
    for svc in pkg.services():
        c = Candidate(svc, "pod_instability")
        m = pkg.metrics.get(svc)
        if m and m.restarts:
            c.add(0.55 if m.restarts == 1 else 0.65, f"{svc} restarted {m.restarts} time(s) in the window", m.ref)
        pods = [p for p in pkg.kubernetes if p.service == svc and p.restarts > 0]
        if pods:
            c.add(0.30, f"Kubernetes reports restarts for {', '.join(p.pod for p in pods)}", *[p.ref for p in pods])
            if any(p.last_termination_reason == "OOMKilled" for p in pods):
                c.cause = "memory_exhaustion"
                c.add(0.30, "last termination reason was OOMKilled", *[p.ref for p in pods])
        if c.score:
            if m and m.up is False:
                c.add(0.10, f"{svc} is currently down", m.ref)
            if m and m.memory_mb and m.memory_mb >= 400:
                c.add(0.10, f"{svc} memory at {m.memory_mb:.0f} MiB", m.ref)
            conn = _logs(pkg, svc, CONNECT_RE)
            if conn:
                c.add(0.15, f"callers saw connection failures to {svc}", *[x.ref for x in conn[:2]])
            out.append(c)
    return out


def rule_deployment_regression(pkg: EvidencePackage) -> list[Candidate]:
    out = []
    for d in pkg.deployments:
        svc = d.service
        c = Candidate(svc, "deployment_regression")
        errs_m = _erroring(pkg.metrics.get(svc))
        errs_l = _logs(pkg, svc, re.compile(r".+"))
        errs_s = _error_spans(pkg, svc)
        if not (errs_m or errs_l or errs_s):
            continue
        c.add(0.40, f"{svc} version {d.version} was deployed shortly before the incident began", d.ref)
        if errs_m:
            c.add(0.20, f"{svc} 5xx rate {pkg.metrics[svc].error_rate_pct:.0f}% after the deployment", pkg.metrics[svc].ref)
        if errs_l or errs_s:
            c.add(0.20, f"errors originate in {svc} (logs/spans) after the deployment",
                  *([x.ref for x in errs_l[:1]] + sorted({t.ref for t, _ in errs_s})[:2]))
        out.append(c)
    return out


def rule_shared_dependency(pkg: EvidencePackage) -> list[Candidate]:
    deps = {}
    for lg in pkg.logs:
        if lg.dependency and (TIMEOUT_RE.search(lg.message) or CONNECT_RE.search(f"{lg.message} {lg.error_type}")):
            deps.setdefault(lg.dependency, []).append(lg)
    if len(deps) < 2:
        return []
    c = Candidate("multiple-services", "shared_dependency_or_network")
    c.add(0.30 + 0.10 * (len(deps) - 2), f"{len(deps)} different downstream services fail/time out at the same time: {', '.join(sorted(deps))}",
          *[v[0].ref for v in deps.values()])
    if all(_elevated(pkg.metrics.get(d)) or _erroring(pkg.metrics.get(d)) for d in deps):
        c.add(0.15, "all of them also show elevated latency or errors in metrics",
              *[pkg.metrics[d].ref for d in deps if d in pkg.metrics])
    return [c]


ALL_RULES = [rule_slow_dependency, rule_unavailable, rule_database, rule_pod_instability,
             rule_deployment_regression, rule_shared_dependency]
