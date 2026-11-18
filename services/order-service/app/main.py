"""Order Service - entry point of the demo; coordinates product, inventory and payment."""
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from common import telemetry

SERVICE = "order-service"
log = logging.getLogger(SERVICE)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderRequest(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1, le=100)
    customer_id: str = "demo-customer"
    payment_token: str = "tok_visa"


class DependencyError(Exception):
    def __init__(self, dependency: str, status: int, detail: str):
        self.dependency, self.status, self.detail = dependency, status, detail


def urls() -> dict[str, str]:
    return {
        "product": os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8001"),
        "inventory": os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8002"),
        "payment": os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8003"),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    url = os.getenv("DATABASE_URL", "sqlite:///./orders.db")
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    app.state.engine = create_engine(url, **kwargs)
    Base.metadata.create_all(app.state.engine)
    app.state.client = httpx.AsyncClient(timeout=float(os.getenv("HTTP_TIMEOUT_S", "5")))
    yield
    await app.state.client.aclose()
    app.state.engine.dispose()


app = FastAPI(title="TraceForge Order Service", lifespan=lifespan)
telemetry.setup(app, SERVICE)


async def call(dep: str, method: str, path: str, **kwargs) -> dict:
    """Call a downstream service; translate every failure into a DependencyError + a useful log."""
    client: httpx.AsyncClient = app.state.client
    try:
        resp = await client.request(method, urls()[dep] + path, **kwargs)
    except httpx.TimeoutException:
        log.error(f"{dep.capitalize()} service request timed out",
                  extra={"error_type": "TimeoutError", "dependency": f"{dep}-service"})
        raise DependencyError(dep, 504, f"{dep} service timed out")
    except httpx.TransportError as exc:
        log.error(f"{dep.capitalize()} service connection failed: connection refused or unreachable",
                  extra={"error_type": type(exc).__name__, "dependency": f"{dep}-service"})
        raise DependencyError(dep, 502, f"{dep} service unreachable")
    if resp.status_code >= 500:
        log.error(f"{dep.capitalize()} service returned {resp.status_code}",
                  extra={"error_type": "HTTPStatusError", "dependency": f"{dep}-service", "status_code": resp.status_code})
        raise DependencyError(dep, 502, f"{dep} service error ({resp.status_code})")
    if resp.status_code >= 400:
        raise DependencyError(dep, resp.status_code, resp.json().get("detail", "request rejected"))
    return resp.json()


def _save(engine, order: Order) -> None:
    with Session(engine) as s:
        s.add(order)
        s.commit()


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE}


@app.get("/ready")
def ready():
    try:
        with Session(app.state.engine) as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:  # readiness includes the database
        raise HTTPException(503, f"database not ready: {type(exc).__name__}")
    return {"status": "ready"}


@app.post("/orders", status_code=201)
async def create_order(req: OrderRequest):
    order_id = f"ord-{uuid.uuid4().hex[:10]}"
    reservation_id = None
    try:
        product = await call("product", "GET", f"/products/{req.product_id}")
        reservation = await call("inventory", "POST", "/inventory/reserve",
                                 json={"product_id": req.product_id, "quantity": req.quantity})
        reservation_id = reservation["reservation_id"]
        total = product["price_cents"] * req.quantity
        await call("payment", "POST", "/payments/process",
                   json={"order_ref": order_id, "amount_cents": total, "token": req.payment_token})
    except DependencyError as err:
        if reservation_id:  # compensate: give the stock back (best effort)
            try:
                await call("inventory", "POST", "/inventory/release", json={"reservation_id": reservation_id})
            except DependencyError:
                log.warning("could not release reservation", extra={"order_id": order_id})
        if err.status in (404, 409, 402):  # business rejections are the caller's problem, not an incident
            raise HTTPException(err.status, err.detail)
        log.error(f"order {order_id} failed because {err.dependency}-service failed",
                  extra={"order_id": order_id, "dependency": f"{err.dependency}-service", "error_type": "DependencyError"})
        raise HTTPException(err.status, f"order failed: {err.detail}")

    order = Order(id=order_id, product_id=req.product_id, quantity=req.quantity, total_cents=total,
                  status="CONFIRMED", created_at=datetime.now(timezone.utc))
    await run_in_threadpool(_save, app.state.engine, order)
    log.info("order created", extra={"order_id": order_id})
    return {"order_id": order_id, "status": "CONFIRMED", "total_cents": total}


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    with Session(app.state.engine) as s:
        o = s.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    return {"order_id": o.id, "product_id": o.product_id, "quantity": o.quantity,
            "total_cents": o.total_cents, "status": o.status, "created_at": o.created_at.isoformat()}


@app.get("/orders")
def list_orders(limit: int = 20):
    with Session(app.state.engine) as s:
        rows = s.scalars(select(Order).order_by(Order.created_at.desc()).limit(min(limit, 100))).all()
    return [{"order_id": o.id, "status": o.status, "total_cents": o.total_cents} for o in rows]
