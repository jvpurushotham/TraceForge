"""Payment Service - simulated payment provider (no real credentials or money)."""
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from common import faults, telemetry

SERVICE = "payment-service"
log = logging.getLogger(SERVICE)
app = FastAPI(title="TraceForge Payment Service")
telemetry.setup(app, SERVICE)


class PaymentRequest(BaseModel):
    order_ref: str
    amount_cents: int = Field(gt=0)
    token: str = "tok_visa"


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE}


@app.get("/ready")
def ready():
    return {"status": "ready"}


@app.post("/payments/process")
async def process(body: PaymentRequest, request: Request):
    # The simulated provider is where latency / errors / timeouts are injected.
    await faults.simulate_provider(request, "payment.provider.call", provider="simulated-psp")
    if body.token == "tok_decline":
        log.info("payment declined by provider", extra={"status_code": 402})
        raise HTTPException(402, "payment declined")
    return {"payment_id": f"pay-{uuid.uuid4().hex[:10]}", "status": "approved", "amount_cents": body.amount_cents}
