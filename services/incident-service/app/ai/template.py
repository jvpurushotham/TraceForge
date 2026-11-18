"""Deterministic report used when no LLM is configured or the LLM output fails validation."""
from __future__ import annotations

from ..models.evidence import DiagnosisResult, EvidencePackage
from .schema import AIReport, Alternative, MostLikely, Observation

PLAYBOOK = {
    "slow_downstream_dependency": (
        "a slow downstream dependency",
        ["Compare the dependency's latency before vs. during the incident (Grafana dashboard A/C).",
         "Open a slow trace and confirm which span dominates the duration.",
         "Inspect the dependency's connection pools, thread/worker saturation and timeout configuration.",
         "Check whether client retries amplify the load on the slow dependency."],
        ["Reduce load or enable a circuit breaker/timeout on callers.", "Scale the slow dependency or fix its bottleneck.",
         "Review caller timeouts so a slow dependency cannot exhaust upstream workers."]),
    "slow_internal_processing": (
        "slow processing inside the affected service itself",
        ["Check CPU/memory saturation for the service.", "Profile the slowest endpoint.", "Look for recent code or config changes."],
        ["Scale out the service.", "Fix or roll back the slow code path."]),
    "service_unavailable": (
        "a dependency that is unavailable or refusing connections",
        ["Check pod/container status and readiness of the dependency.", "Look at the dependency's own logs for startup or crash errors.",
         "Verify DNS, Service and network policy between caller and dependency."],
        ["Restore the dependency (restart/redeploy after finding the cause).", "Add retries with backoff and a circuit breaker in callers."]),
    "service_errors": (
        "a dependency returning errors while still reachable",
        ["Read the dependency's error logs and exception types.", "Check whether the errors began at a deployment or config change."],
        ["Fix the underlying error or roll back the most recent change."]),
    "database_latency": (
        "database slowness",
        ["Inspect slow queries, locks and missing indexes.", "Compare database span duration before/during the incident.",
         "Check database CPU, I/O and connection counts."],
        ["Optimise the slow query or add an index.", "Scale the database or add caching for hot reads."]),
    "database_connection_pressure": (
        "database connection exhaustion or errors",
        ["Check connection-pool usage and max_connections.", "Look for leaked connections or long-running transactions."],
        ["Tune pool size and timeouts.", "Fix connection leaks; add pooling (e.g. PgBouncer)."]),
    "pod_instability": (
        "workload instability (repeated restarts)",
        ["`kubectl describe pod` / `kubectl logs --previous` for the restarting workload.", "Check liveness/readiness probe settings.",
         "Review recent changes and resource limits."],
        ["Fix the crash cause; adjust probes and resource limits as needed."]),
    "memory_exhaustion": (
        "memory exhaustion (OOMKilled)",
        ["Compare memory usage with the container limit.", "Look for leaks or unbounded caches/queues."],
        ["Raise the memory limit as a stop-gap and fix the leak."]),
    "deployment_regression": (
        "a regression introduced by a recent deployment",
        ["Diff the deployed version against the previous one.", "Check whether the new exception types appear only after the deployment."],
        ["Roll back to the previous version, then fix forward."]),
    "shared_dependency_or_network": (
        "a shared dependency or network problem affecting several services",
        ["Check what the failing services have in common (database, node, network policy, DNS).",
         "Look at node and cluster-level events."],
        ["Address the shared component; avoid restarting services individually until it is understood."]),
}


def build_template_report(pkg: EvidencePackage, diag: DiagnosisResult) -> AIReport:
    if not diag.candidates:
        return AIReport(summary=f"Not enough evidence to propose a cause for '{pkg.symptom}' on {pkg.affected_service}.",
                        missing_evidence=diag.missing_evidence, uncertainty="; ".join(diag.uncertainty))
    top = diag.candidates[0]
    label, checks, fixes = PLAYBOOK.get(top.cause, (top.cause, [], []))
    refs = {r.id: r for r in pkg.refs}
    obs = [Observation(text=refs[r].finding, evidence_refs=[r]) for r in top.evidence_refs if r in refs]
    strength = top.strength if diag.status == "diagnosed" else "insufficient"
    return AIReport(
        summary=(f"{pkg.symptom} on {pkg.affected_service}. The available evidence points to {label} "
                 f"({top.service}); this is a hypothesis with {strength} evidence, not a confirmed root cause."),
        most_likely_cause=MostLikely(service=top.service, cause=top.cause, evidence_strength=strength,
                                     evidence_refs=top.evidence_refs),
        supporting_observations=obs,
        alternative_explanations=[Alternative(cause=f"{c.service}/{c.cause}", why="; ".join(c.rationale[:2]),
                                              evidence_refs=c.evidence_refs) for c in diag.candidates[1:3]],
        suggested_checks=checks, remediation_steps=fixes,
        missing_evidence=diag.missing_evidence, uncertainty="; ".join(diag.uncertainty))
