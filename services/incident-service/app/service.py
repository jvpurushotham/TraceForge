from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select

from .ai.explain import explain
from .config import Settings
from .correlation.evidence import EvidenceBuilder
from .db import (Database, DeploymentEvent, Diagnosis, EngineerFeedback, EvidenceReference, Incident,
                 IncidentEvent, utcnow)
from .diagnosis.engine import ENGINE_VERSION, diagnose
from .models.evidence import DeploymentChange, DiagnosisResult, EvidencePackage

log = logging.getLogger("incident-service")

M_CREATED = Counter("traceforge_incidents_created_total", "Incidents created", ["severity", "affected_service"])
M_OPEN = Gauge("traceforge_incidents_open", "Open incidents", ["severity"])
M_DIAG = Counter("traceforge_diagnoses_total", "Diagnoses run", ["source", "status"])
M_TOP = Counter("traceforge_top_cause_total", "Top-ranked causes", ["service", "cause"])
M_DUR = Histogram("traceforge_diagnosis_duration_seconds", "Diagnosis latency",
                  buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))

SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "warning": 2, "low": 1}


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)  # SQLite drops tz info
    return dt


def parse_ts(s: str | None) -> datetime:
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        return dt if dt.year > 2000 else utcnow()  # Alertmanager uses 0001-01-01 for "unset"
    except ValueError:
        return utcnow()


def inc_dict(i: Incident) -> dict:
    return {"incident_id": i.id, "title": i.title, "severity": i.severity, "status": i.status,
            "affected_service": i.affected_service, "related_services": i.related_services or [],
            "symptom": i.symptom, "trace_id": i.trace_id,
            "first_observed": aware(i.first_observed).isoformat(), "created_at": aware(i.created_at).isoformat(),
            "resolved_at": aware(i.resolved_at).isoformat() if i.resolved_at else None}


class IncidentService:
    def __init__(self, settings: Settings, db: Database, builder: EvidenceBuilder, llm=None):
        self.s, self.db, self.builder, self.llm = settings, db, builder, llm
        self._tasks: set = set()
        self._loop = None

    # ---------- incidents ----------
    def _event(self, s, incident_id: str, etype: str, desc: str, ts: datetime | None = None) -> None:
        s.add(IncidentEvent(incident_id=incident_id, event_type=etype, description=desc, timestamp=ts or utcnow()))

    def _next_id(self, s) -> str:
        year = utcnow().year
        n = s.scalar(select(func.count()).select_from(Incident).where(Incident.id.like(f"INC-{year}-%"))) or 0
        return f"INC-{year}-{n + 1:03d}"

    def create_incident(self, title: str, affected_service: str, severity: str = "medium", symptom: str = "",
                        first_observed: datetime | None = None, trace_id: str | None = None,
                        fingerprints: list[str] | None = None, related: list[str] | None = None) -> dict:
        with self.db.session() as s:
            inc = Incident(id=self._next_id(s), title=title, severity=severity.lower(), affected_service=affected_service,
                           related_services=sorted(set(related or []) - {affected_service}), symptom=symptom or title,
                           trace_id=trace_id, first_observed=first_observed or utcnow(), created_at=utcnow(),
                           firing_fingerprints=fingerprints or [])
            s.add(inc)
            s.flush()
            self._event(s, inc.id, "incident_created", f"Incident opened for {affected_service}: {inc.symptom}")
            M_CREATED.labels(inc.severity, inc.affected_service).inc()
            return inc_dict(inc)

    def handle_alertmanager(self, payload: dict) -> list[dict]:
        alerts = payload.get("alerts", [])
        firing = [a for a in alerts if a.get("status") == "firing"]
        results: list[dict] = []
        with self.db.session() as s:
            for a in (a for a in alerts if a.get("status") == "resolved"):
                fp = a.get("fingerprint", "")
                for inc in s.scalars(select(Incident).where(Incident.status != "resolved")):
                    if fp in (inc.firing_fingerprints or []):
                        inc.firing_fingerprints = [f for f in inc.firing_fingerprints if f != fp]
                        self._event(s, inc.id, "alert_resolved", f"Alert {a['labels'].get('alertname')} resolved for "
                                    f"{a['labels'].get('service', '?')}")
                        if not inc.firing_fingerprints:
                            inc.status, inc.resolved_at = "resolved", utcnow()
                            self._event(s, inc.id, "status_change", "All alerts resolved -> incident resolved")
                        results.append({"incident_id": inc.id, "action": "resolved_alert"})
        if not firing:
            return results

        def rank(a):
            lab = a.get("labels", {})
            return (lab.get("service") == self.s.entry_service, SEV_RANK.get(lab.get("severity", "low"), 0))

        with self.db.session() as s:
            cutoff = utcnow() - timedelta(seconds=self.s.group_window_s)
            open_inc = s.scalars(select(Incident).where(Incident.status != "resolved")
                                 .order_by(Incident.created_at.desc())).first()
            if open_inc and aware(open_inc.created_at) >= cutoff:
                target_id, created = open_inc.id, False
            else:
                target_id, created = None, True
        if created:
            primary = max(firing, key=rank)
            lab, ann = primary.get("labels", {}), primary.get("annotations", {})
            sev = max((a.get("labels", {}).get("severity", "medium") for a in firing), key=lambda x: SEV_RANK.get(x, 0))
            first = min(parse_ts(a.get("startsAt")) for a in firing)
            inc = self.create_incident(
                title=f"{lab.get('alertname', 'Alert')} on {lab.get('service', 'unknown')}",
                affected_service=lab.get("service", "unknown"), severity=sev,
                symptom=ann.get("symptom") or ann.get("summary") or lab.get("alertname", "alert"),
                first_observed=first, fingerprints=[], related=[a.get("labels", {}).get("service", "") for a in firing])
            target_id = inc["incident_id"]
            results.append({"incident_id": target_id, "action": "created"})
        else:
            results.append({"incident_id": target_id, "action": "attached"})
        with self.db.session() as s:
            inc = s.get(Incident, target_id)
            fps = list(inc.firing_fingerprints or [])
            rel = set(inc.related_services or [])
            for a in firing:
                lab = a.get("labels", {})
                fp = a.get("fingerprint", "")
                if fp and fp not in fps:
                    fps.append(fp)
                    self._event(s, inc.id, "alert_firing", f"{lab.get('alertname')} [{lab.get('severity', '?')}] on "
                                f"{lab.get('service', '?')}: {a.get('annotations', {}).get('summary', '')}",
                                parse_ts(a.get("startsAt")))
                svc = lab.get("service")
                if svc and svc != inc.affected_service:
                    rel.add(svc)
                if SEV_RANK.get(lab.get("severity", ""), 0) > SEV_RANK.get(inc.severity, 0):
                    inc.severity = lab["severity"]
            inc.firing_fingerprints, inc.related_services = fps, sorted(rel)
        if created and self.s.auto_diagnose:
            self._spawn(self._delayed_diagnose(target_id))
        return results

    def bind_loop(self, loop) -> None:
        self._loop = loop

    def _spawn(self, coro) -> None:
        try:
            t = asyncio.get_running_loop().create_task(coro)
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)
        except RuntimeError:  # called from a worker thread
            if self._loop is None:
                coro.close()
                raise
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self._tasks.add(fut)
            fut.add_done_callback(self._tasks.discard)

    async def _delayed_diagnose(self, incident_id: str) -> None:
        await asyncio.sleep(self.s.evidence_delay_s)  # let traces/logs land in Tempo/Loki first
        try:
            await self.diagnose(incident_id, use_ai=True)
        except Exception:
            log.exception("automatic diagnosis failed", extra={"error_type": "DiagnosisError"})

    def record_deployment(self, service: str, version: str, note: str = "", ts: datetime | None = None) -> dict:
        with self.db.session() as s:
            ev = DeploymentEvent(service=service, version=version, note=note, timestamp=ts or utcnow())
            s.add(ev)
            s.flush()
            return {"id": ev.id, "service": service, "version": version, "timestamp": aware(ev.timestamp).isoformat()}

    # ---------- diagnosis ----------
    async def diagnose(self, incident_id: str, use_ai: bool = True) -> dict:
        started = time.perf_counter()
        with self.db.session() as s:
            inc = s.get(Incident, incident_id)
            if not inc:
                raise KeyError(incident_id)
            d = inc_dict(inc)
            first = aware(inc.first_observed)
            now = utcnow()
            window_end = aware(inc.resolved_at) or now
            deps = [DeploymentChange(service=e.service, version=e.version, timestamp=aware(e.timestamp))
                    for e in s.scalars(select(DeploymentEvent).where(DeploymentEvent.timestamp >= first - timedelta(minutes=15))
                                       .where(DeploymentEvent.timestamp <= window_end + timedelta(minutes=1)))]
            self._event(s, incident_id, "diagnosis_started", f"Collecting evidence ({'rules+AI' if use_ai else 'rules only'})")
        d["first_observed"] = first
        d["id"] = incident_id
        pkg: EvidencePackage = await self.builder.build(d, deps, now, window_end)
        result: DiagnosisResult = diagnose(pkg)
        envelope = await explain(self.s, pkg, result, self.llm) if use_ai else None
        engine = ENGINE_VERSION + (f"+llm:{envelope.model}" if envelope and envelope.source == "llm"
                                   else "+template" if envelope else "")
        summary = (envelope.report.summary if envelope else
                   (f"{result.candidates[0].service}/{result.candidates[0].cause} (score {result.candidates[0].score})"
                    if result.candidates else "insufficient evidence"))
        payload = {"result": result.model_dump(mode="json"), "report": envelope.model_dump(mode="json") if envelope else None,
                   "evidence": pkg.model_dump(mode="json")}
        with self.db.session() as s:
            diag = Diagnosis(incident_id=incident_id, engine_version=engine, status=result.status, summary=summary, result=payload)
            s.add(diag)
            s.flush()
            for r in pkg.refs:
                s.add(EvidenceReference(diagnosis_id=diag.id, ref_id=r.id, source_type=r.source_type,
                                        resource_reference=r.resource_reference, finding=r.finding))
            inc = s.get(Incident, incident_id)
            if pkg.traces:
                inc.trace_id = max(pkg.traces, key=lambda t: t.root_duration_ms).trace_id
            for m in pkg.missing:
                self._event(s, incident_id, "evidence_missing", m)
            top = result.candidates[0] if result.candidates else None
            self._event(s, incident_id, "diagnosis_completed",
                        f"[{engine}] {result.status}: " + (f"top hypothesis {top.service}/{top.cause} "
                                                           f"(evidence score {top.score}, {top.strength})" if top else "no candidate cause"))
            out = self._diag_dict(diag)
        M_DIAG.labels(engine.split("+")[-1] if "+" in engine else "rules", result.status).inc()
        if result.candidates and result.status == "diagnosed":
            M_TOP.labels(result.candidates[0].service, result.candidates[0].cause).inc()
        M_DUR.observe(time.perf_counter() - started)
        return out

    def _diag_dict(self, d: Diagnosis) -> dict:
        p = d.result or {}
        return {"diagnosis_id": d.id, "incident_id": d.incident_id, "engine_version": d.engine_version,
                "status": d.status, "summary": d.summary, "created_at": aware(d.created_at).isoformat(),
                "suspected_causes": [{"service": c["service"], "cause": c["cause"], "confidence": c["score"],
                                      "evidence_strength": c["strength"], "hypothesis": c["hypothesis"],
                                      "rationale": c["rationale"], "evidence_refs": c["evidence_refs"]}
                                     for c in p.get("result", {}).get("candidates", [])],
                "confirmed_facts": p.get("result", {}).get("confirmed_facts", []),
                "missing_evidence": p.get("result", {}).get("missing_evidence", []),
                "uncertainty": p.get("result", {}).get("uncertainty", []),
                "score_semantics": p.get("result", {}).get("score_semantics"),
                "report": p.get("report"), "requires_human_review": True}

    # ---------- queries ----------
    def get_incident(self, incident_id: str) -> dict | None:
        with self.db.session() as s:
            inc = s.get(Incident, incident_id)
            if not inc:
                return None
            out = inc_dict(inc)
            last = s.scalars(select(Diagnosis).where(Diagnosis.incident_id == incident_id)
                             .order_by(Diagnosis.id.desc())).first()
            out["status_detail"] = inc.status
            out["latest_diagnosis"] = self._diag_dict(last) if last else None
            out["requires_human_review"] = True
            return out

    def list_incidents(self, status=None, service=None, severity=None, limit=50) -> list[dict]:
        with self.db.session() as s:
            q = select(Incident).order_by(Incident.created_at.desc()).limit(min(limit, 200))
            if status:
                q = q.where(Incident.status == status)
            if service:
                q = q.where(Incident.affected_service == service)
            if severity:
                q = q.where(Incident.severity == severity.lower())
            return [inc_dict(i) for i in s.scalars(q)]

    def timeline(self, incident_id: str) -> list[dict] | None:
        with self.db.session() as s:
            if not s.get(Incident, incident_id):
                return None
            rows = s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                             .order_by(IncidentEvent.timestamp, IncidentEvent.id))
            return [{"timestamp": aware(r.timestamp).isoformat(), "event_type": r.event_type, "description": r.description}
                    for r in rows]

    def evidence(self, incident_id: str) -> dict | None:
        with self.db.session() as s:
            if not s.get(Incident, incident_id):
                return None
            last = s.scalars(select(Diagnosis).where(Diagnosis.incident_id == incident_id).order_by(Diagnosis.id.desc())).first()
            if not last:
                return {"incident_id": incident_id, "diagnosis_id": None, "references": [], "missing": ["no diagnosis has been run yet"]}
            refs = s.scalars(select(EvidenceReference).where(EvidenceReference.diagnosis_id == last.id))
            return {"incident_id": incident_id, "diagnosis_id": last.id,
                    "references": [{"id": r.ref_id, "source_type": r.source_type, "resource_reference": r.resource_reference,
                                    "finding": r.finding} for r in refs],
                    "missing": (last.result or {}).get("result", {}).get("missing_evidence", [])}

    def add_feedback(self, incident_id: str, verdict: str, comment: str, diagnosis_id: int | None) -> dict | None:
        with self.db.session() as s:
            if not s.get(Incident, incident_id):
                return None
            if diagnosis_id is None:
                last = s.scalars(select(Diagnosis).where(Diagnosis.incident_id == incident_id).order_by(Diagnosis.id.desc())).first()
                diagnosis_id = last.id if last else None
            fb = EngineerFeedback(incident_id=incident_id, diagnosis_id=diagnosis_id, verdict=verdict, comment=comment)
            s.add(fb)
            s.flush()
            self._event(s, incident_id, "feedback", f"Engineer verdict: {verdict}. {comment}".strip())
            return {"feedback_id": fb.id, "incident_id": incident_id, "diagnosis_id": diagnosis_id, "verdict": verdict}

    def refresh_gauges(self) -> None:
        with self.db.session() as s:
            counts = dict(s.execute(select(Incident.severity, func.count()).where(Incident.status != "resolved")
                                    .group_by(Incident.severity)).all())
        for sev in ("critical", "high", "medium", "low"):
            M_OPEN.labels(sev).set(counts.get(sev, 0))
