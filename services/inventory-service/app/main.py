"""Inventory Service - stock checks and reservations backed by a (simulated) database."""
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from common import faults, telemetry

SERVICE = "inventory-service"
log = logging.getLogger(SERVICE)
app = FastAPI(title="TraceForge Inventory Service")
telemetry.setup(app, SERVICE)

STOCK = {"p-1001": 500, "p-1002": 5000, "p-1003": 2000, "p-1004": 800}
RESERVATIONS: dict[str, dict] = {}


class ReserveRequest(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1, le=100)


class ReleaseRequest(BaseModel):
    reservation_id: str


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE}


@app.get("/ready")
def ready():
    return {"status": "ready"}


@app.get("/inventory/{product_id}")
async def get_stock(product_id: str, request: Request):
    await faults.simulate_db(request, "SELECT stock FROM inventory WHERE product_id = $1")
    if product_id not in STOCK:
        raise HTTPException(404, "unknown product")
    return {"product_id": product_id, "available": STOCK[product_id]}


@app.post("/inventory/reserve", status_code=201)
async def reserve(body: ReserveRequest, request: Request):
    await faults.simulate_db(request, "UPDATE inventory SET stock = stock - $2 WHERE product_id = $1")
    if body.product_id not in STOCK:
        raise HTTPException(404, "unknown product")
    if STOCK[body.product_id] < body.quantity:
        log.warning("insufficient stock", extra={"status_code": 409})
        raise HTTPException(409, "insufficient stock")
    STOCK[body.product_id] -= body.quantity
    rid = f"res-{uuid.uuid4().hex[:10]}"
    RESERVATIONS[rid] = body.model_dump()
    return {"reservation_id": rid, **body.model_dump()}


@app.post("/inventory/release")
async def release(body: ReleaseRequest, request: Request):
    await faults.simulate_db(request, "UPDATE inventory SET stock = stock + $2 WHERE product_id = $1")
    res = RESERVATIONS.pop(body.reservation_id, None)
    if not res:
        raise HTTPException(404, "unknown reservation")
    STOCK[res["product_id"]] += res["quantity"]
    return {"released": body.reservation_id}
