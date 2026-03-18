"""
Integration tests: backend/api/costs.py — Cost monitoring endpoints.

Gebruikt FastAPI TestClient met geïsoleerde CostGuardrails (tmp_path).

Dekt:
  GET /api/costs/daily    — dagelijks verbruik
  GET /api/costs/monthly  — maandelijks overzicht
  Beide met tenant_id parameter
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.cost_guardrails as cg_module
from backend.cost_guardrails import CostGuardrails, TenantBudget


@pytest.fixture(autouse=True)
def isolated_costs(tmp_path, monkeypatch):
    """Isoleer CostGuardrails naar tmp_path."""
    monkeypatch.setattr(cg_module, "ROOT", tmp_path)
    # Clear locks cache
    cg_module._locks.clear()
    return tmp_path


@pytest.fixture
def client():
    from backend.api.costs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/costs")
    return TestClient(app)


class TestDailyCosts:
    def test_200_zonder_data(self, client):
        r = client.get("/api/costs/daily", params={"tenant_id": "test"})
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "test"
        assert data["total_usd"] == 0
        assert data["records_count"] == 0

    def test_200_met_records(self, client):
        g = CostGuardrails(tenant_id="test")
        g.record_cost(0.05, "TestAgent", "camp_1")
        r = client.get("/api/costs/daily", params={"tenant_id": "test"})
        data = r.json()
        assert data["total_usd"] == pytest.approx(0.05, abs=0.001)
        assert data["records_count"] == 1

    def test_remaining_berekend(self, client):
        g = CostGuardrails(tenant_id="test")
        g.record_cost(1.00, "TestAgent")
        r = client.get("/api/costs/daily", params={"tenant_id": "test"})
        data = r.json()
        # Default budget is $5, dus $4 remaining
        assert data["remaining_usd"] == pytest.approx(4.0, abs=0.01)

    def test_default_tenant(self, client):
        r = client.get("/api/costs/daily")
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "default"


class TestMonthlyCosts:
    def test_200_zonder_data(self, client):
        r = client.get("/api/costs/monthly", params={"tenant_id": "test"})
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "test"
        assert data["monthly_total_usd"] == 0

    def test_bevat_limieten(self, client):
        r = client.get("/api/costs/monthly", params={"tenant_id": "test"})
        data = r.json()
        assert "monthly_limit_usd" in data
        assert "daily_limit_usd" in data
        assert "monthly_remaining_usd" in data

    def test_default_tenant(self, client):
        r = client.get("/api/costs/monthly")
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "default"
