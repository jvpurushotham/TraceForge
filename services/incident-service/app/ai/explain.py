from __future__ import annotations

import logging

from ..config import Settings
from ..models.evidence import DiagnosisResult, EvidencePackage
from .llm import LLMClient, LLMUnavailable
from .prompt import SYSTEM, user_prompt
from .redact import redact
from .schema import ReportEnvelope, parse_json, validate_report
from .template import build_template_report

log = logging.getLogger("incident-service.ai")


def compact_evidence(pkg: EvidencePackage) -> dict:
    d = pkg.model_dump(mode="json")
    for key in ("window_start", "window_end"):
        d[key] = str(d[key])
    return redact(d)


async def explain(settings: Settings, pkg: EvidencePackage, diag: DiagnosisResult,
                  llm: LLMClient | None = None) -> ReportEnvelope:
    """LLM report when configured and valid; otherwise the deterministic template (never an unvalidated LLM answer)."""
    llm = llm or LLMClient(settings)
    template = lambda warn: ReportEnvelope(source="template", report=build_template_report(pkg, diag), warnings=warn)  # noqa: E731
    if not llm.enabled:
        return template(["LLM not configured: deterministic template report used"])
    top = (diag.candidates[0].service, diag.candidates[0].cause) if diag.candidates else None
    prompt = user_prompt(compact_evidence(pkg), redact(diag.model_dump(mode="json")))
    last_err = ""
    for attempt in (1, 2):
        try:
            text = await llm.complete(SYSTEM, prompt + (f"\nYour previous answer was rejected: {last_err}. Return valid JSON only."
                                                          if last_err else ""))
            report, warnings, disagree = validate_report(parse_json(text), pkg.ref_ids(), top)
            return ReportEnvelope(source="llm", model=llm.model, report=report, warnings=warnings, disagrees_with_rules=disagree)
        except LLMUnavailable as exc:
            log.warning("LLM unavailable: %s", exc)
            return template([f"{exc}: deterministic template report used"])
        except ValueError as exc:  # includes JSON + schema errors
            last_err = str(exc)[:300]
            log.warning("LLM output rejected (attempt %d): %s", attempt, last_err)
    return template([f"LLM output failed validation twice ({last_err}): deterministic template report used"])
