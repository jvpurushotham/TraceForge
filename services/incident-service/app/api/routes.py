from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

router = APIRouter()


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    affected_service: str = Field(min_length=1, max_length=80)
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    symptom: str = ""
    trace_id: str | None = None


class FeedbackIn(BaseModel):
    verdict: Literal["correct", "partially_correct", "incorrect"]
    comment: str = Field("", max_length=2000)
    diagnosis_id: int | None = None


class DeploymentIn(BaseModel):
    service: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=80)
    note: str = ""


class AMAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None
    fingerprint: str = ""


class AMPayload(BaseModel):
    version: str = "4"
    status: str = "firing"
    alerts: list[AMAlert] = Field(min_length=1, max_length=200)


def svc(request: Request):
    return request.app.state.service


def check_webhook_token(request: Request, authorization: str | None = Header(None)):
    token = request.app.state.settings.webhook_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "invalid or missing webhook token")


@router.get("/health")
def health():
    return {"status": "ok", "service": "incident-service"}


@router.get("/ready")
def ready(request: Request):
    try:
        with request.app.state.db.session() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, f"database not ready: {type(exc).__name__}")
    return {"status": "ready"}


@router.post("/api/v1/incidents", status_code=201)
def create_incident(body: IncidentCreate, request: Request):
    return svc(request).create_incident(**body.model_dump())


@router.post("/api/v1/webhooks/alertmanager", status_code=202, dependencies=[Depends(check_webhook_token)])
def alertmanager_webhook(body: AMPayload, request: Request):
    return {"accepted": len(body.alerts), "incidents": svc(request).handle_alertmanager(body.model_dump())}


@router.post("/api/v1/events/deployment", status_code=201)
def deployment_event(body: DeploymentIn, request: Request):
    return svc(request).record_deployment(**body.model_dump())


@router.get("/api/v1/incidents")
def list_incidents(request: Request, status: str | None = None, service: str | None = None,
                   severity: str | None = None, limit: int = 50):
    return svc(request).list_incidents(status, service, severity, limit)


@router.get("/api/v1/incidents/{incident_id}")
def get_incident(incident_id: str, request: Request):
    r = svc(request).get_incident(incident_id)
    if r is None:
        raise HTTPException(404, "incident not found")
    return r


@router.post("/api/v1/incidents/{incident_id}/diagnose")
async def diagnose(incident_id: str, request: Request, use_ai: bool = True):
    try:
        return await svc(request).diagnose(incident_id, use_ai=use_ai)
    except KeyError:
        raise HTTPException(404, "incident not found")


@router.get("/api/v1/incidents/{incident_id}/timeline")
def timeline(incident_id: str, request: Request):
    r = svc(request).timeline(incident_id)
    if r is None:
        raise HTTPException(404, "incident not found")
    return r


@router.get("/api/v1/incidents/{incident_id}/evidence")
def evidence(incident_id: str, request: Request):
    r = svc(request).evidence(incident_id)
    if r is None:
        raise HTTPException(404, "incident not found")
    return r


@router.post("/api/v1/incidents/{incident_id}/feedback", status_code=201)
def feedback(incident_id: str, body: FeedbackIn, request: Request):
    r = svc(request).add_feedback(incident_id, body.verdict, body.comment, body.diagnosis_id)
    if r is None:
        raise HTTPException(404, "incident not found")
    return r
