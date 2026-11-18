from __future__ import annotations

import json
from collections import OrderedDict

import httpx

from ..models.evidence import LogFinding
from .base import CollectorError

QUERY = '{service_name=~".+"} | json | level=~"ERROR|WARNING"'


def parse_streams(body: dict) -> list[LogFinding]:
    """Collapse repeated log lines into one finding with a count (+ a few example trace ids)."""
    groups: "OrderedDict[tuple, LogFinding]" = OrderedDict()
    for stream in body.get("data", {}).get("result", []):
        svc_label = stream.get("stream", {}).get("service_name", "")
        for value in stream.get("values", []):
            line = value[1]
            try:
                doc = json.loads(line)
            except ValueError:
                doc = {"message": line, "level": "ERROR"}
            service = doc.get("service") or svc_label
            msg = str(doc.get("message", ""))[:300]
            key = (service, msg, doc.get("error_type", ""))
            f = groups.get(key)
            if f is None:
                f = groups[key] = LogFinding(service=service, message=msg, level=doc.get("level", "ERROR"),
                                             error_type=doc.get("error_type", ""), dependency=doc.get("dependency", ""),
                                             count=0)
            f.count += 1
            tid = doc.get("trace_id")
            if tid and tid not in f.trace_ids and len(f.trace_ids) < 3:
                f.trace_ids.append(tid)
    return sorted(groups.values(), key=lambda f: -f.count)


class LokiCollector:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=15)

    async def errors(self, start: float, end: float, limit: int = 500) -> list[LogFinding]:
        try:
            r = await self.client.get(f"{self.base_url}/loki/api/v1/query_range", params={
                "query": QUERY, "start": int(start * 1e9), "end": int(end * 1e9),
                "limit": limit, "direction": "backward"})
            r.raise_for_status()
            return parse_streams(r.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError(f"Loki query failed: {type(exc).__name__}: {exc}") from exc
