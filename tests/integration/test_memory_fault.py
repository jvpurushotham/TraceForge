import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def payment_app(monkeypatch, cap):
    monkeypatch.setenv("ENABLE_FAULT_INJECTION", "true")
    monkeypatch.setenv("MAX_LEAK_MB", str(cap))
    spec = importlib.util.spec_from_file_location("payment_mem_main", ROOT / "services" / "payment-service" / "app" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app


def test_leak_is_capped_and_freed(monkeypatch):
    c = TestClient(payment_app(monkeypatch, 8))
    r = c.post("/admin/leak", json={"mb": 500}).json()
    assert r["capped_to_mb"] == 8 and r["allocated_mb_total"] == 8
    assert c.delete("/admin/leak").json()["allocated_mb_total"] == 0


def test_leak_disabled_by_default(monkeypatch):
    c = TestClient(payment_app(monkeypatch, 0))
    assert c.post("/admin/leak", json={"mb": 5}).status_code == 403
