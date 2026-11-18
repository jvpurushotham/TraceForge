from __future__ import annotations

from ..models.evidence import CauseCandidate, DiagnosisResult, EvidencePackage, Fact
from . import rules as R

ENGINE_VERSION = "rules-1.0.0"


def _strength(score: float) -> str:
    return "strong" if score >= 0.75 else "moderate" if score >= 0.5 else "weak"


def _facts(pkg: EvidencePackage) -> list[Fact]:
    """Directly observed telemetry facts (as opposed to the hypotheses built on them)."""
    facts = []
    for svc, m in sorted(pkg.metrics.items()):
        if R._elevated(m):
            base = f" (baseline {m.baseline_p95_ms:.0f} ms)" if m.baseline_p95_ms else ""
            facts.append(Fact(text=f"{svc} p95 latency is {m.p95_ms:.0f} ms{base}", refs=[m.ref]))
        if R._erroring(m):
            facts.append(Fact(text=f"{svc} 5xx rate is {m.error_rate_pct:.0f}%", refs=[m.ref]))
        if m.up is False:
            facts.append(Fact(text=f"{svc} is down", refs=[m.ref]))
        if m.restarts:
            facts.append(Fact(text=f"{svc} restarted {m.restarts} time(s)", refs=[m.ref]))
    for t in pkg.traces[:3]:
        top = sorted(t.self_time_ms.items(), key=lambda kv: -kv[1])[:1]
        if top and t.root_duration_ms:
            svc, ms = top[0]
            facts.append(Fact(text=f"in trace {t.trace_id[:8]} ({t.root_duration_ms:.0f} ms) {svc} spent {ms:.0f} ms "
                                   f"({min(100, ms / t.root_duration_ms * 100):.0f}%)", refs=[t.ref]))
    for lg in pkg.logs[:5]:
        facts.append(Fact(text=f"{lg.count}x {lg.level} in {lg.service}: {lg.message}", refs=[lg.ref]))
    for d in pkg.deployments:
        facts.append(Fact(text=f"{d.service} deployed {d.version}", refs=[d.ref]))
    return facts


def diagnose(pkg: EvidencePackage) -> DiagnosisResult:
    cands: list[R.Candidate] = []
    for rule in R.ALL_RULES:
        cands.extend(rule(pkg))

    # a failure explained by a stronger, more specific cause for the same service is discounted
    strong = {(c.service, c.cause) for c in cands if c.score >= 0.5}
    for c in cands:
        if c.cause in ("service_unavailable", "service_errors"):
            if (c.service, "pod_instability") in strong or (c.service, "database_connection_pressure") in strong \
                    or (c.service, "database_latency") in strong or (c.service, "memory_exhaustion") in strong:
                c.score -= 0.15
                c.rationale.append("discounted: a more specific cause explains these failures")
            if c.cause == "service_errors" and (c.service, "deployment_regression") in strong:
                c.score -= 0.15
                c.rationale.append("discounted: a recent deployment explains these errors")

    # several different dependencies equally implicated -> a shared cause is the more parsimonious hypothesis
    singles = [c for c in cands if c.cause in ("slow_downstream_dependency", "service_unavailable", "service_errors")]
    shared = [c for c in cands if c.cause == "shared_dependency_or_network"]
    if shared and singles:
        best = max(c.score for c in singles)
        close = {c.service for c in singles if best - c.score <= 0.10}
        if len(close) >= 2:
            shared[0].add(0.25, f"{len(close)} services ({', '.join(sorted(close))}) are implicated almost equally, "
                                "which a single-service cause does not explain")

    critical_missing = [m for m in pkg.missing if m.startswith(("metrics", "traces", "logs"))]
    cap = 0.95 if not critical_missing else 0.85 if len(critical_missing) == 1 else 0.55
    uncertainty: list[str] = []
    if critical_missing:
        uncertainty.append(f"evidence from {len(critical_missing)} source(s) is missing - scores are capped at {cap}")

    cands = sorted((c for c in cands if c.score >= 0.30), key=lambda c: -c.score)[:5]
    out = []
    for i, c in enumerate(cands, 1):
        score = round(min(c.score, cap), 2)
        out.append(CauseCandidate(rank=i, service=c.service, cause=c.cause, score=score, strength=_strength(score),
                                  rationale=c.rationale, evidence_refs=c.refs))

    status = "diagnosed"
    if not out:
        status = "insufficient_evidence"
        uncertainty.append("no rule produced a candidate with enough supporting evidence")
    else:
        if out[0].score < 0.45:
            status = "insufficient_evidence"
            uncertainty.append(f"best candidate scores only {out[0].score}")
        if len(out) > 1 and out[0].score - out[1].score < 0.05 and out[0].service != out[1].service:
            status = "insufficient_evidence"
            uncertainty.append(f"signals conflict: {out[0].service}/{out[0].cause} and "
                               f"{out[1].service}/{out[1].cause} are almost equally supported")

    return DiagnosisResult(incident_id=pkg.incident_id, engine_version=ENGINE_VERSION, status=status,
                           candidates=out, confirmed_facts=_facts(pkg), missing_evidence=list(pkg.missing),
                           uncertainty=uncertainty)
