"""Scrub secrets / personal data before anything leaves the cluster for an LLM."""
import re

PATTERNS = [
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED_CARD]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token|authorization)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b(sk|pk|tok)_[A-Za-z0-9_]{6,}\b"), "[REDACTED_TOKEN]"),
]
MAX_LEN = 300


def redact_text(s: str) -> str:
    for rx, rep in PATTERNS:
        s = rx.sub(rep, s)
    return s[:MAX_LEN]


def redact(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
