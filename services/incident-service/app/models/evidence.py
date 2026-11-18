"""Typed evidence package: the ONLY thing the diagnosis rules and the LLM ever see."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    id: str
    source_type: Literal["metric", "trace", "log", "kubernetes", "deployment"]
    resource_reference: str
    finding: str


class ServiceMetrics(BaseModel):
    service: str
    p95_ms: float | None = None
    baseline_p95_ms: float | None = None
    error_rate_pct: float | None = None
    rps: float | None = None
    up: bool | None = None
    restarts: int | None = None
    memory_mb: float | None = None
    ref: str = ""


class ErrorSpan(BaseModel):
    service: str
    name: str
    duration_ms: float
    message: str = ""
    peer: str | None = None  # downstream service a client span was calling
    is_db: bool = False


class TraceFinding(BaseModel):
    trace_id: str
    root_duration_ms: float
    root_service: str = ""
    self_time_ms: dict[str, float] = Field(default_factory=dict)
    db_self_time_ms: dict[str, float] = Field(default_factory=dict)
    error_spans: list[ErrorSpan] = Field(default_factory=list)
    ref: str = ""


class LogFinding(BaseModel):
    service: str
    message: str
    level: str = "ERROR"
    error_type: str = ""
    dependency: str = ""
    count: int = 1
    trace_ids: list[str] = Field(default_factory=list)
    ref: str = ""


class KubePod(BaseModel):
    service: str
    pod: str
    restarts: int = 0
    last_termination_reason: str = ""
    ready: bool = True
    ref: str = ""


class DeploymentChange(BaseModel):
    service: str
    version: str
    timestamp: datetime
    ref: str = ""


class EvidencePackage(BaseModel):
    incident_id: str
    symptom: str
    affected_service: str
    severity: str = ""
    window_start: datetime
    window_end: datetime
    metrics: dict[str, ServiceMetrics] = Field(default_factory=dict)
    traces: list[TraceFinding] = Field(default_factory=list)
    logs: list[LogFinding] = Field(default_factory=list)
    kubernetes: list[KubePod] = Field(default_factory=list)
    deployments: list[DeploymentChange] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    refs: list[EvidenceRef] = Field(default_factory=list)

    def ref_ids(self) -> set[str]:
        return {r.id for r in self.refs}

    def services(self) -> list[str]:
        names = set(self.metrics) | {self.affected_service}
        for t in self.traces:
            names |= set(t.self_time_ms) | set(t.db_self_time_ms)
        for lg in self.logs:
            names.add(lg.service)
            if lg.dependency:
                names.add(lg.dependency)
        return sorted(n for n in names if n)


class Fact(BaseModel):
    text: str
    refs: list[str] = Field(default_factory=list)


class CauseCandidate(BaseModel):
    rank: int = 0
    service: str
    cause: str
    score: float
    strength: Literal["strong", "moderate", "weak"] = "weak"
    hypothesis: bool = True  # a candidate is NEVER a confirmed root cause
    rationale: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class DiagnosisResult(BaseModel):
    incident_id: str
    engine_version: str
    status: Literal["diagnosed", "insufficient_evidence"]
    candidates: list[CauseCandidate] = Field(default_factory=list)
    confirmed_facts: list[Fact] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    score_semantics: str = "rule-based evidence score in [0,1]; NOT a calibrated probability"
