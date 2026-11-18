"""Offline evaluation of the rule engine (and the template/LLM report) on the labeled fixture set.

    python tests/evaluation/evaluate.py            # prints a table + writes docs/evaluation-results.md
Metrics: top-1 / top-3 accuracy, evidence validity, false-positive handling, insufficient-evidence handling.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "incident-service"))
sys.path.insert(0, str(ROOT / "services"))
from app.diagnosis.engine import diagnose  # noqa: E402
from app.models.evidence import EvidencePackage  # noqa: E402


def load():
    for f in sorted((Path(__file__).parent / "fixtures").glob("*.json")):
        d = json.loads(f.read_text())
        yield d["name"], d["label"], EvidencePackage.model_validate(d["package"])


def evaluate():
    rows, incidents, top1, top3, valid_refs, total_refs, fp, fp_n, insuff, insuff_n = [], 0, 0, 0, 0, 0, 0, 0, 0, 0
    for name, label, pkg in load():
        res = diagnose(pkg)
        ids = pkg.ref_ids()
        for c in res.candidates:
            for r in c.evidence_refs:
                total_refs += 1
                valid_refs += r in ids
        top = res.candidates[0] if res.candidates else None
        if label.get("expect_no_incident"):
            fp_n += 1
            ok = not res.candidates or res.status == "insufficient_evidence"
            fp += (not ok)
            verdict = "OK (no false positive)" if ok else "FALSE POSITIVE"
        elif label.get("expect_insufficient"):
            insuff_n += 1
            ok = res.status == "insufficient_evidence"
            insuff += ok
            verdict = "OK (flagged insufficient)" if ok else "WRONG (over-confident)"
        else:
            incidents += 1
            hit = lambda c: c.service == label["service"] and c.cause in label["cause"]  # noqa: E731
            t1 = bool(top and hit(top) and res.status == "diagnosed")
            t3 = any(hit(c) for c in res.candidates[:3])
            top1 += t1
            top3 += t3
            verdict = "top-1" if t1 else "top-3" if t3 else "MISS"
        rows.append((name, label.get("service") or "-", f"{top.service}/{top.cause} ({top.score})" if top else "-", res.status, verdict))
    summary = {
        "incident_cases": incidents,
        "top1_accuracy": round(top1 / incidents, 2) if incidents else None,
        "top3_accuracy": round(top3 / incidents, 2) if incidents else None,
        "evidence_validity": round(valid_refs / total_refs, 2) if total_refs else None,
        "false_positive_rate": round(fp / fp_n, 2) if fp_n else None,
        "insufficient_evidence_handled": f"{insuff}/{insuff_n}",
    }
    return rows, summary


if __name__ == "__main__":
    rows, summary = evaluate()
    w = max(len(r[0]) for r in rows)
    for r in rows:
        print(f"{r[0]:<{w}}  expected={r[1]:<18} top={r[2]:<62} {r[3]:<22} {r[4]}")
    print(json.dumps(summary, indent=2))
    md = ["# Offline evaluation (fixtures)", "",
          "Computed by `tests/evaluation/evaluate.py` on hand-built fixtures that mimic collector output. "
          "These numbers show the rules behave as designed; they are **not** measurements of a live system. "
          "Measure the live stack with `tests/fault-injection/run_experiment.py`.", "",
          "| case | expected | top candidate | status | result |", "|---|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} | {e} |" for a, b, c, d, e in rows]
    md += ["", "| metric | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in summary.items()]
    out = ROOT / "docs" / "evaluation-results.md"
    out.write_text("\n".join(md) + "\n")
