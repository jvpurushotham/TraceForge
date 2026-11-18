from __future__ import annotations

import math

import httpx

from ..models.evidence import ServiceMetrics
from .base import CollectorError


class PrometheusCollector:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None, rate_window_s: int = 30):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=10)
        self.w = rate_window_s

    async def instant(self, promql: str, at: float | None = None) -> dict[str, float]:
        params = {"query": promql}
        if at:
            params["time"] = f"{at:.3f}"
        try:
            r = await self.client.get(f"{self.base_url}/api/v1/query", params=params)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError(f"Prometheus query failed: {type(exc).__name__}: {exc}") from exc
        if body.get("status") != "success":
            raise CollectorError(f"Prometheus error: {body.get('error')}")
        out: dict[str, float] = {}
        for item in body["data"]["result"]:
            svc = item["metric"].get("service")
            try:
                value = float(item["value"][1])
            except (TypeError, ValueError):
                continue
            if svc and not math.isnan(value) and not math.isinf(value):
                out[svc] = value
        return out

    def _queries(self) -> dict[str, str]:
        w = f"{self.w}s"
        return {
            "p95": f"histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[{w}])))",
            "err": (f'(sum by (service) (rate(http_requests_total{{status=~"5.."}}[{w}])) / '
                    f'sum by (service) (rate(http_requests_total[{w}])))'),
            "rps": f"sum by (service) (rate(http_requests_total[{w}]))",
            "up": 'max by (service) (up{job="traceforge"})',
            "mem": 'max by (service) (process_resident_memory_bytes{job="traceforge"})',
        }

    async def service_metrics(self, services: list[str], at: float, baseline_at: float,
                              lookback_s: int) -> dict[str, ServiceMetrics]:
        q = self._queries()
        restarts_q = f'max by (service) (changes(process_start_time_seconds{{job="traceforge"}}[{lookback_s + 60}s]))'
        res = {k: await self.instant(v, at) for k, v in q.items()}
        res["restarts"] = await self.instant(restarts_q, at)
        baseline = await self.instant(q["p95"], baseline_at)  # latency just BEFORE the incident
        out: dict[str, ServiceMetrics] = {}
        for svc in services:
            vals = [res[k].get(svc) for k in res]
            if all(v is None for v in vals) and svc not in baseline:
                continue
            err = res["err"].get(svc)
            if err is None and res["rps"].get(svc, 0) > 0:
                err = 0.0  # traffic but no 5xx series
            out[svc] = ServiceMetrics(
                service=svc,
                p95_ms=res["p95"][svc] * 1000 if svc in res["p95"] else None,
                baseline_p95_ms=baseline[svc] * 1000 if svc in baseline else None,
                error_rate_pct=err * 100 if err is not None else None,
                rps=res["rps"].get(svc),
                up=bool(res["up"][svc]) if svc in res["up"] else None,
                restarts=int(res["restarts"][svc]) if svc in res["restarts"] else None,
                memory_mb=res["mem"][svc] / 1048576 if svc in res["mem"] else None,
            )
        return out
