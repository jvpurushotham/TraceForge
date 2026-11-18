from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./incidents.db"))
    prometheus_url: str = field(default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    tempo_url: str = field(default_factory=lambda: os.getenv("TEMPO_URL", "http://localhost:3200"))
    loki_url: str = field(default_factory=lambda: os.getenv("LOKI_URL", "http://localhost:3100"))
    known_services: list[str] = field(default_factory=lambda: [
        s.strip() for s in os.getenv(
            "KNOWN_SERVICES", "order-service,product-service,inventory-service,payment-service").split(",") if s.strip()])
    entry_service: str = field(default_factory=lambda: os.getenv("ENTRY_SERVICE", "order-service"))
    evidence_delay_s: float = field(default_factory=lambda: float(os.getenv("EVIDENCE_DELAY_S", "20")))
    lookback_s: int = field(default_factory=lambda: int(os.getenv("LOOKBACK_S", "120")))
    rate_window_s: int = field(default_factory=lambda: int(os.getenv("RATE_WINDOW_S", "30")))
    slow_trace_ms: int = field(default_factory=lambda: int(os.getenv("SLOW_TRACE_MS", "1000")))
    group_window_s: int = field(default_factory=lambda: int(os.getenv("GROUP_WINDOW_S", "600")))
    webhook_token: str = field(default_factory=lambda: os.getenv("WEBHOOK_TOKEN", ""))
    auto_diagnose: bool = field(default_factory=lambda: _bool("AUTO_DIAGNOSE", True))
    kube_enabled: bool = field(default_factory=lambda: _bool("KUBE_ENABLED", False))
    kube_namespace: str = field(default_factory=lambda: os.getenv("KUBE_NAMESPACE", "traceforge-app"))
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "none").lower())
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_S", "45")))
