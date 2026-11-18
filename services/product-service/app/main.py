"""Product Service - seeded catalogue other services depend on."""
import logging

from fastapi import FastAPI, HTTPException, Request

from common import faults, telemetry

SERVICE = "product-service"
log = logging.getLogger(SERVICE)
app = FastAPI(title="TraceForge Product Service")
telemetry.setup(app, SERVICE)

PRODUCTS = {
    "p-1001": {"id": "p-1001", "name": "Laptop Pro 14", "price_cents": 129900, "category": "computers"},
    "p-1002": {"id": "p-1002", "name": "Wireless Mouse", "price_cents": 2900, "category": "accessories"},
    "p-1003": {"id": "p-1003", "name": "Mechanical Keyboard", "price_cents": 8900, "category": "accessories"},
    "p-1004": {"id": "p-1004", "name": "27in Monitor", "price_cents": 24900, "category": "displays"},
}


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE}


@app.get("/ready")
def ready():
    return {"status": "ready"}


@app.get("/products")
async def list_products(request: Request):
    await faults.simulate_provider(request, "product.catalog.lookup")
    return list(PRODUCTS.values())


@app.get("/products/{product_id}")
async def get_product(product_id: str, request: Request):
    await faults.simulate_provider(request, "product.catalog.lookup", product_id=product_id)
    product = PRODUCTS.get(product_id)
    if not product:
        raise HTTPException(404, "product not found")
    return product
