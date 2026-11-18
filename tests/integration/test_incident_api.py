"""Incident service API: webhook -> incident -> diagnosis (fake evidence) -> timeline/evidence/feedback -> UI."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "incident-service"))
sys.path.insert(0, str(ROOT / "services"))
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.evidence import EvidencePackage  # noqa: E402

FIX = ROOT / "tests" / "evaluation" / "fixtures"


class FakeBuilder:
    """Stands in for Prometheus/Tempo/Loki: returns a stored evidence package."""

    def __init__(self, fixture: str):
        self.pkg = EvidencePackage.model_validate(json.loads((FIX / fixture).read_text())["package"])

    async def build(self, incident, deployments, now, window_end):
        return self.pkg


def alert(status="firing", name="HighLatencyP95", service="order-service", severity="high", fp="fp1"):
    return {"status": status, "fingerprint": fp, "labels": {"alertname": name, "service": service, "severity": severity},
            "annotations": {"summary": f"{service} slow", "symptom": "Elevated latency"}, "startsAt": "2026-11-08T10:00:00Z"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'i.db'}", auto_diagnose=False, webhook_token="")
    app = create_app(settings, builder=FakeBuilder("01_slow_payment.json"), instrument=False)
    with TestClient(app) as c:
        yield c


def test_webhook_creates_one_incident_and_attaches_related_alerts(client):
    r = client.post("/api/v1/webhooks/alertmanager", json={"alerts": [alert(), alert(name="HighErrorRate", service="payment-service", fp="fp2")]})
    assert r.status_code == 202
    incidents = client.get("/api/v1/incidents").json()
    assert len(incidents) == 1 and incidents[0]["affected_service"] == "order-service"
    r2 = client.post("/api/v1/webhooks/alertmanager", json={"alerts": [alert(name="ServiceDown", service="inventory-service", fp="fp3")]})
    assert r2.json()["incidents"][0]["action"] == "attached"
    assert len(client.get("/api/v1/incidents").json()) == 1


def test_diagnose_ranks_payment_and_stores_evidence(client):
    inc = client.post("/api/v1/incidents", json={"title": "slow orders", "affected_service": "order-service", "severity": "high"}).json()
    d = client.post(f"/api/v1/incidents/{inc['incident_id']}/diagnose").json()
    assert d["requires_human_review"] is True and d["status"] == "diagnosed"
    top = d["suspected_causes"][0]
    assert (top["service"], top["cause"]) == ("payment-service", "slow_downstream_dependency")
    ev = client.get(f"/api/v1/incidents/{inc['incident_id']}/evidence").json()
    known = {r["id"] for r in ev["references"]}
    assert top["evidence_refs"] and set(top["evidence_refs"]) <= known       # every cited ref is verifiable
    types = [e["event_type"] for e in client.get(f"/api/v1/incidents/{inc['incident_id']}/timeline").json()]
    assert "incident_created" in types and "diagnosis_completed" in types
    fb = client.post(f"/api/v1/incidents/{inc['incident_id']}/feedback", json={"verdict": "correct", "comment": "matches"})
    assert fb.status_code == 201
    assert client.get(f"/api/v1/incidents/{inc['incident_id']}").json()["latest_diagnosis"]["engine_version"].startswith("rules")


def test_resolved_alert_resolves_incident(client):
    client.post("/api/v1/webhooks/alertmanager", json={"alerts": [alert()]})
    client.post("/api/v1/webhooks/alertmanager", json={"alerts": [alert(status="resolved")]})
    assert client.get("/api/v1/incidents").json()[0]["status"] == "resolved"


def test_unknown_incident_404_and_validation(client):
    assert client.get("/api/v1/incidents/INC-0-000").status_code == 404
    assert client.post("/api/v1/webhooks/alertmanager", json={"alerts": []}).status_code == 422
    assert client.post("/api/v1/incidents", json={"title": "x", "affected_service": "a", "severity": "bogus"}).status_code == 422


def test_webhook_token_enforced(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 't.db'}", auto_diagnose=False, webhook_token="s3cret"),
                     builder=FakeBuilder("01_slow_payment.json"), instrument=False)
    with TestClient(app) as c:
        body = {"alerts": [alert()]}
        assert c.post("/api/v1/webhooks/alertmanager", json=body).status_code == 401
        assert c.post("/api/v1/webhooks/alertmanager", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 202


def test_ui_and_health(client):
    assert client.get("/").status_code == 200 and "TraceForge" in client.get("/").text
    assert client.get("/health").json()["status"] == "ok" and client.get("/ready").status_code == 200
