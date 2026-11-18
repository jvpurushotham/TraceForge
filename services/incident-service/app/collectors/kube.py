from __future__ import annotations

import asyncio

from ..models.evidence import KubePod
from .base import CollectorError


class KubeCollector:
    """Read-only pod state via the Kubernetes API (RBAC: get/list pods). Disabled unless KUBE_ENABLED=true."""

    def __init__(self, namespace: str, enabled: bool):
        self.namespace, self.enabled = namespace, enabled

    def _read(self, services: list[str]) -> list[KubePod]:
        try:
            from kubernetes import client, config
        except ImportError as exc:
            raise CollectorError("kubernetes client library not installed") from exc
        try:
            try:
                config.load_incluster_config()
            except Exception:
                config.load_kube_config()
            pods = client.CoreV1Api().list_namespaced_pod(self.namespace, _request_timeout=8).items
        except Exception as exc:
            raise CollectorError(f"Kubernetes API unavailable: {type(exc).__name__}") from exc
        out = []
        for p in pods:
            svc = (p.metadata.labels or {}).get("app", "")
            if svc not in services:
                continue
            for cs in p.status.container_statuses or []:
                term = cs.last_state.terminated if cs.last_state else None
                out.append(KubePod(service=svc, pod=p.metadata.name, restarts=cs.restart_count or 0,
                                   last_termination_reason=(term.reason if term else "") or "", ready=bool(cs.ready)))
        return out

    async def state(self, services: list[str]) -> list[KubePod]:
        if not self.enabled:
            raise CollectorError("Kubernetes collector disabled (KUBE_ENABLED=false)")
        return await asyncio.to_thread(self._read, services)
