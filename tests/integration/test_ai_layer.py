"""AI explanation guardrails, tested with a fake LLM (no network, no key)."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "incident-service"))
sys.path.insert(0, str(ROOT / "services"))
from app.ai.explain import explain  # noqa: E402
from app.ai.llm import LLMUnavailable  # noqa: E402
from app.ai.redact import redact  # noqa: E402
from app.config import Settings  # noqa: E402
from app.diagnosis.engine import diagnose  # noqa: E402
from app.models.evidence import EvidencePackage  # noqa: E402

pkg = EvidencePackage.model_validate(json.loads((ROOT / "tests/evaluation/fixtures/01_slow_payment.json").read_text())["package"])
diag = diagnose(pkg)
real_ref = diag.candidates[0].evidence_refs[0]


class FakeLLM:
    enabled, model = True, "fake"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    async def complete(self, system, user):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


def report(**kw):
    base = {"summary": "Payment dependency is slow.",
            "most_likely_cause": {"service": "payment-service", "cause": "slow_downstream_dependency",
                                  "evidence_strength": "strong", "evidence_refs": [real_ref]},
            "supporting_observations": [{"text": "payment dominates the trace", "evidence_refs": [real_ref]}],
            "suggested_checks": ["inspect provider latency"], "remediation_steps": ["review timeouts"], "uncertainty": "n/a"}
    base.update(kw)
    return base


def run(llm):
    return asyncio.run(explain(Settings(), pkg, diag, llm))


def test_valid_llm_report_is_accepted():
    env = run(FakeLLM(report()))
    assert env.source == "llm" and env.report.supporting_observations[0].evidence_refs == [real_ref]


def test_hallucinated_citation_is_dropped():
    bad = report(supporting_observations=[{"text": "invented", "evidence_refs": ["trace:does-not-exist"]},
                                          {"text": "real", "evidence_refs": [real_ref]}])
    env = run(FakeLLM(bad))
    texts = [o.text for o in env.report.supporting_observations]
    assert "invented" not in texts and "real" in texts and env.warnings


def test_invalid_json_twice_falls_back_to_template():
    llm = FakeLLM("not json", "{still not")
    env = run(llm)
    assert env.source == "template" and llm.calls == 2


def test_provider_outage_falls_back_to_template():
    assert run(FakeLLM(LLMUnavailable("boom"))).source == "template"


def test_disabled_llm_uses_template():
    assert asyncio.run(explain(Settings(llm_provider="none"), pkg, diag)).source == "template"


def test_redaction_removes_secrets_and_card_numbers():
    out = redact({"log": "user a@b.com paid 4111 1111 1111 1111 with Bearer abcdef123456 password=hunter2"})["log"]
    assert "a@b.com" not in out and "4111 1111 1111 1111" not in out and "hunter2" not in out
