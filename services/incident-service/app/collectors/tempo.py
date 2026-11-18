from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from ..models.evidence import ErrorSpan, TraceFinding
from .base import CollectorError


@dataclass
class SpanRec:
    span_id: str
    parent_id: str
    service: str
    name: str
    start_ns: int
    end_ns: int
    status: str  # OK | ERROR | UNSET
    attrs: dict = field(default_factory=dict)
    exc_messages: list = field(default_factory=list)
    status_message: str = ""

    @property
    def duration_ms(self) -> float:
        return max(0, self.end_ns - self.start_ns) / 1e6


def _val(v: dict):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return v[k]
    return None


def _attrs(items) -> dict:
    return {a["key"]: _val(a.get("value", {})) for a in (items or [])}


def _status(s: dict | None) -> tuple[str, str]:
    code = (s or {}).get("code", 0)
    msg = (s or {}).get("message", "")
    if code in (2, "2", "STATUS_CODE_ERROR"):
        return "ERROR", msg
    if code in (1, "1", "STATUS_CODE_OK"):
        return "OK", msg
    return "UNSET", msg


def parse_trace(doc: dict) -> list[SpanRec]:
    """Parse Tempo's OTLP-JSON trace (supports `batches` and `trace.resourceSpans` shapes)."""
    batches = doc.get("batches") or doc.get("resourceSpans") or (doc.get("trace") or {}).get("resourceSpans") or []
    spans: list[SpanRec] = []
    for b in batches:
        service = _attrs((b.get("resource") or {}).get("attributes")).get("service.name", "unknown")
        for scope in b.get("scopeSpans") or b.get("instrumentationLibrarySpans") or []:
            for s in scope.get("spans", []):
                status, smsg = _status(s.get("status"))
                msgs = [_attrs(e.get("attributes")).get("exception.message", "")
                        for e in s.get("events", []) if e.get("name") == "exception"]
                spans.append(SpanRec(
                    span_id=str(s.get("spanId", "")), parent_id=str(s.get("parentSpanId", "") or ""),
                    service=service, name=s.get("name", ""),
                    start_ns=int(s.get("startTimeUnixNano", 0)), end_ns=int(s.get("endTimeUnixNano", 0)),
                    status=status, attrs=_attrs(s.get("attributes")), exc_messages=[m for m in msgs if m],
                    status_message=smsg))
    return spans


def _peer(span: SpanRec, known: list[str]) -> str | None:
    for key in ("server.address", "net.peer.name", "http.host", "url.full", "http.url", "peer.service"):
        v = str(span.attrs.get(key) or "")
        for svc in known:
            if svc and svc in v:
                return svc
    return None


def _covered(span: SpanRec, kids: list[SpanRec]) -> float:
    ivs = sorted((max(k.start_ns, span.start_ns), min(k.end_ns, span.end_ns)) for k in kids)
    total, cur_s, cur_e = 0, None, None
    for s, e in ivs:
        if e <= s:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total / 1e6


def analyze_trace(trace_id: str, spans: list[SpanRec], known_services: list[str]) -> TraceFinding | None:
    if not spans:
        return None
    by_id = {s.span_id for s in spans}
    kids: dict[str, list[SpanRec]] = defaultdict(list)
    roots: list[SpanRec] = []
    for s in spans:
        (kids[s.parent_id] if s.parent_id in by_id else roots).append(s)
    root = min(roots or spans, key=lambda s: s.start_ns)
    f = TraceFinding(trace_id=trace_id, root_duration_ms=round(root.duration_ms, 1), root_service=root.service)
    for s in spans:
        self_ms = max(0.0, s.duration_ms - _covered(s, kids[s.span_id]))
        bucket = f.db_self_time_ms if "db.system" in s.attrs else f.self_time_ms
        bucket[s.service] = round(bucket.get(s.service, 0.0) + self_ms, 1)
        if s.status == "ERROR":
            msg = (s.exc_messages or [s.status_message or ""])[0] or \
                  str(s.attrs.get("http.status_code") or s.attrs.get("http.response.status_code") or "")
            f.error_spans.append(ErrorSpan(service=s.service, name=s.name, duration_ms=round(s.duration_ms, 1),
                                           message=msg[:200], peer=_peer(s, known_services),
                                           is_db="db.system" in s.attrs))
    return f


class TempoCollector:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=15)

    async def search(self, traceql: str, start: float, end: float, limit: int = 8) -> list[dict]:
        try:
            r = await self.client.get(f"{self.base_url}/api/search",
                                      params={"q": traceql, "start": int(start), "end": int(end), "limit": limit})
            r.raise_for_status()
            return r.json().get("traces", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError(f"Tempo search failed: {type(exc).__name__}: {exc}") from exc

    async def get_trace(self, trace_id: str) -> list[SpanRec]:
        try:
            r = await self.client.get(f"{self.base_url}/api/traces/{trace_id}", headers={"Accept": "application/json"})
            if r.status_code == 404:
                return []
            r.raise_for_status()
            return parse_trace(r.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError(f"Tempo trace fetch failed: {type(exc).__name__}: {exc}") from exc
