from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class Observation(BaseModel):
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class Alternative(BaseModel):
    cause: str
    why: str
    evidence_refs: list[str] = Field(default_factory=list)


class MostLikely(BaseModel):
    service: str
    cause: str
    evidence_strength: Literal["strong", "moderate", "weak", "insufficient"]
    evidence_refs: list[str] = Field(default_factory=list)


class AIReport(BaseModel):
    summary: str
    most_likely_cause: MostLikely | None = None
    supporting_observations: list[Observation] = Field(default_factory=list)
    alternative_explanations: list[Alternative] = Field(default_factory=list)
    suggested_checks: list[str] = Field(default_factory=list)
    remediation_steps: list[str] = Field(default_factory=list)  # recommendations only - nothing is executed
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainty: str = ""


class ReportEnvelope(BaseModel):
    source: Literal["llm", "template"]
    model: str = ""
    report: AIReport
    warnings: list[str] = Field(default_factory=list)
    disagrees_with_rules: bool = False


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def validate_report(raw: dict, valid_refs: set[str], rules_top: tuple[str, str] | None) -> tuple[AIReport, list[str], bool]:
    """Schema-validate, then drop every citation that does not exist in the evidence package."""
    try:
        report = AIReport.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"schema validation failed: {exc.errors()[:3]}") from exc
    warnings: list[str] = []

    def clean(refs: list[str], where: str) -> list[str]:
        good = [r for r in refs if r in valid_refs]
        if len(good) != len(refs):
            warnings.append(f"{where}: removed {len(refs) - len(good)} citation(s) that do not exist in the evidence")
        return good

    kept = []
    for o in report.supporting_observations:
        o.evidence_refs = clean(o.evidence_refs, "observation")
        if o.evidence_refs:
            kept.append(o)
        else:
            warnings.append(f"dropped unsupported observation: {o.text[:80]!r}")
    report.supporting_observations = kept
    for a in report.alternative_explanations:
        a.evidence_refs = clean(a.evidence_refs, "alternative")
    disagree = False
    if report.most_likely_cause:
        m = report.most_likely_cause
        m.evidence_refs = clean(m.evidence_refs, "most_likely_cause")
        if not m.evidence_refs:
            m.evidence_strength = "insufficient"
            warnings.append("most_likely_cause has no valid evidence citation -> marked insufficient")
        if rules_top and (m.service, m.cause) != rules_top:
            disagree = True
            warnings.append(f"LLM cause {m.service}/{m.cause} differs from the rule engine's {rules_top[0]}/{rules_top[1]}")
    return report, warnings, disagree
