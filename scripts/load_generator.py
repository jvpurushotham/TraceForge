"""Simple order load generator.   python scripts/load_generator.py --rps 3 --duration 60"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx

PRODUCTS = ["p-1001", "p-1002", "p-1003", "p-1004"]


async def run(url: str, rps: float, duration: float, quiet: bool = False) -> dict:
    lat: list[float] = []
    codes: dict[int, int] = {}
    stop = time.monotonic() + duration

    async with httpx.AsyncClient(timeout=30) as client:
        async def one():
            t = time.perf_counter()
            try:
                r = await client.post(f"{url}/orders", json={"product_id": random.choice(PRODUCTS), "quantity": 1})
                code = r.status_code
            except httpx.HTTPError:
                code = 0
            lat.append((time.perf_counter() - t) * 1000)
            codes[code] = codes.get(code, 0) + 1

        tasks = []
        while time.monotonic() < stop:
            tasks.append(asyncio.create_task(one()))
            await asyncio.sleep(1 / rps)
        await asyncio.gather(*tasks)
    lat.sort()
    out = {"requests": len(lat), "status_codes": codes,
           "p50_ms": round(statistics.median(lat), 1) if lat else None,
           "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 1) if lat else None}
    if not quiet:
        print(out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--rps", type=float, default=3)
    ap.add_argument("--duration", type=float, default=30)
    a = ap.parse_args()
    asyncio.run(run(a.url, a.rps, a.duration))
