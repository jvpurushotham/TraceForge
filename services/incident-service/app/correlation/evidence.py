"""Collect + correlate telemetry for an incident window into one EvidencePackage.

Rule: a source that cannot be queried, or returns nothing, is reported in `missing`.
Nothing is ever invented to fill a gap.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from ..collectors.base import CollectorError
from ..collectors.tempo import analyze_trace
from ..config import Settings
from ..models.evidence import DeploymentChange, EvidencePackage, EvidenceRef


class EvidenceBuilder:
    def __init__(self, settings: Settings, prom, tempo, loki, kube):
        self.s, self.prom, self.tempo, self.loki, self.kube = settings, prom, tempo, loki, kube

    async def _traces(self, start: float, end: float, affected: str):
        slow = await self.tempo.search(f"{{ duration > {self.s.slow_trace_ms}ms }}", start, end)
        err = await self.tempo.search("{ status = error }", start, end)
        seen, picked = set(), []
        for t in sorted(slow + err, key=lambda t: (t.get("rootServiceName") != affected, -(t.get("durationMs") or 0))):
            tid = t.get("traceID")
            if tid and tid not in seen:
                seen.add(tid)
                picked.append(tid)
        findings = []
        for tid in picked[:6]:
            f = analyze_trace(tid, await self.tempo.get_trace(tid), self.s.known_services)
            if f:
                findings.append(f)
        return findings

    async def build(self, incident: dict, deployments: list[DeploymentChange],
                    now: datetime, window_end: datetime) -> EvidencePackage:
        first = incident["first_observed"]
        start_ts = first.timestamp() - self.s.lookback_s
        end_ts = window_end.timestamp()
        at = min(now.timestamp(), end_ts)
        pkg = EvidencePackage(incident_id=incident["id"], symptom=incident["symptom"],
                              affected_service=incident["affected_service"], severity=incident["severity"],
                              window_start=datetime.fromtimestamp(start_ts, first.tzinfo),
                              window_end=window_end)
        services = self.s.known_services
        results = await asyncio.gather(
            self.prom.service_metrics(services, at, max(first.timestamp() - 45, 1), self.s.lookback_s),
            self._traces(start_ts, end_ts, incident["affected_service"]),
            self.loki.errors(start_ts, end_ts),
            self.kube.state(services),
            return_exceptions=True)
        metrics, traces, logs, pods = results

        if isinstance(metrics, Exception):
            pkg.missing.append(f"metrics: {_why(metrics)}")
        elif not metrics:
            pkg.missing.append("metrics: Prometheus returned no samples for the known services")
        else:
            for svc, m in metrics.items():
                m.ref = f"metric:{svc}"
                pkg.metrics[svc] = m
                bits = []
                if m.p95_ms is not None:
                    base = f" (baseline {m.baseline_p95_ms:.0f} ms)" if m.baseline_p95_ms is not None else ""
                    bits.append(f"p95 {m.p95_ms:.0f} ms{base}")
                if m.error_rate_pct is not None:
                    bits.append(f"5xx rate {m.error_rate_pct:.1f}%")
                if m.rps is not None:
                    bits.append(f"{m.rps:.2f} req/s")
                if m.up is not None:
                    bits.append("up" if m.up else "DOWN")
                if m.restarts:
                    bits.append(f"{m.restarts} restart(s) in window")
                pkg.refs.append(EvidenceRef(id=m.ref, source_type="metric",
                                            resource_reference=f'prometheus: service="{svc}" (p95, 5xx rate, up, restarts)',
                                            finding=f"{svc}: " + ", ".join(bits)))

        if isinstance(traces, Exception):
            pkg.missing.append(f"traces: {_why(traces)}")
        elif not traces:
            pkg.missing.append("traces: no slow or failed traces found in the incident window")
        else:
            pkg.traces = traces
            for t in traces:
                t.ref = f"trace:{t.trace_id[:8]}"
                top = sorted(t.self_time_ms.items(), key=lambda kv: -kv[1])[:3]
                dbs = sorted(t.db_self_time_ms.items(), key=lambda kv: -kv[1])[:2]
                txt = f"trace {t.trace_id} took {t.root_duration_ms:.0f} ms; self-time " + \
                      ", ".join(f"{k} {v:.0f} ms" for k, v in top)
                if dbs:
                    txt += "; database " + ", ".join(f"{k} {v:.0f} ms" for k, v in dbs)
                if t.error_spans:
                    txt += f"; {len(t.error_spans)} error span(s): " + \
                           "; ".join(f"{e.service}/{e.name} {e.message}".strip() for e in t.error_spans[:3])
                pkg.refs.append(EvidenceRef(id=t.ref, source_type="trace",
                                            resource_reference=f"tempo: /api/traces/{t.trace_id}", finding=txt))

        if isinstance(logs, Exception):
            pkg.missing.append(f"logs: {_why(logs)}")
        elif not logs:
            pkg.missing.append("logs: no ERROR/WARNING logs found in the incident window")
        else:
            pkg.logs = logs[:25]
            for i, lg in enumerate(pkg.logs):
                lg.ref = f"log:{lg.service}:{i}"
                pkg.refs.append(EvidenceRef(
                    id=lg.ref, source_type="log", resource_reference=f"loki: {{service_name=\"{lg.service}\"}} | json | level=~\"ERROR|WARNING\"",
                    finding=f"{lg.count}x {lg.level} in {lg.service}: {lg.message}" +
                            (f" [{lg.error_type}]" if lg.error_type else "") +
                            (f" (dependency {lg.dependency})" if lg.dependency else "")))

        if isinstance(pods, Exception):
            pkg.missing.append(f"kubernetes: {_why(pods)}")
        else:
            pkg.kubernetes = pods
            for p in pods:
                p.ref = f"k8s:{p.pod}"
                pkg.refs.append(EvidenceRef(id=p.ref, source_type="kubernetes", resource_reference=f"pod/{p.pod}",
                                            finding=f"{p.pod}: {p.restarts} restart(s), last termination '{p.last_termination_reason or 'n/a'}', ready={p.ready}"))

        for d in deployments:
            d.ref = f"deploy:{d.service}:{d.version}"
            pkg.deployments.append(d)
            pkg.refs.append(EvidenceRef(id=d.ref, source_type="deployment", resource_reference="incident-service deployment events",
                                        finding=f"{d.service} deployed version {d.version} at {d.timestamp.isoformat()}"))
        if not deployments:
            pkg.missing.append("deployments: no deployment event recorded in the 15 min before the incident "
                               "(this is NOT proof that nothing changed)")
        return pkg


def _why(exc: Exception) -> str:
    return str(exc) if isinstance(exc, CollectorError) else f"{type(exc).__name__}: {exc}"
