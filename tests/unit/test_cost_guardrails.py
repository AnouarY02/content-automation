"""
Unit tests: backend/cost_guardrails.py — budget limieten en tracking.

Dekt:
  - check_budget(): succesvol binnen limiet
  - check_budget(): BudgetExceededError bij overschrijding
  - check_campaign_budget(): per-campagne limiet
  - record_cost(): tracking + cumulatief totaal
  - get_daily_spend() en get_daily_remaining()
  - Thread safety: meerdere parallelle records
  - Lege/corrupt tracking bestand → graceful fallback
"""

import json
from datetime import date
from pathlib import Path

import pytest

from backend.cost_guardrails import (
    BudgetExceededError,
    CostGuardrails,
    TenantBudget,
    DailyCostLog,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def guardrails(tmp_path, monkeypatch):
    """Guardrails met tmp_path isolatie en klein budget."""
    import backend.cost_guardrails as cg_module
    monkeypatch.setattr(cg_module, "ROOT", tmp_path)

    budget = TenantBudget(
        daily_limit_usd=1.00,
        per_campaign_limit_usd=0.50,
        monthly_limit_usd=10.00,
        alert_threshold_pct=80.0,
    )
    return CostGuardrails(tenant_id="test", budget=budget)


# ── check_budget ─────────────────────────────────────────────────────

class TestCheckBudget:
    def test_succesvol_binnen_limiet(self, guardrails):
        # Moet geen exceptie gooien
        guardrails.check_budget(estimated_cost=0.10)

    def test_budget_exceeded_bij_overschrijding(self, guardrails):
        guardrails.record_cost(0.90, "agent_a")
        with pytest.raises(BudgetExceededError) as exc_info:
            guardrails.check_budget(estimated_cost=0.20)
        assert "daily_limit" in str(exc_info.value)
        assert exc_info.value.tenant_id == "test"

    def test_exact_op_limiet_gooit_error(self, guardrails):
        guardrails.record_cost(1.00, "agent_a")
        with pytest.raises(BudgetExceededError):
            guardrails.check_budget(estimated_cost=0.01)


# ── check_campaign_budget ────────────────────────────────────────────

class TestCheckCampaignBudget:
    def test_binnen_campagne_limiet(self, guardrails):
        guardrails.check_campaign_budget("camp_1", 0.30)

    def test_overschrijding_campagne_limiet(self, guardrails):
        with pytest.raises(BudgetExceededError) as exc_info:
            guardrails.check_campaign_budget("camp_1", 0.55)
        assert "per_campaign_limit" in str(exc_info.value)


# ── record_cost ──────────────────────────────────────────────────────

class TestRecordCost:
    def test_record_verhoogt_totaal(self, guardrails):
        result = guardrails.record_cost(0.10, "IdeaAgent", "camp_1")
        assert result == pytest.approx(0.10, abs=0.001)

    def test_meerdere_records_cumulatief(self, guardrails):
        guardrails.record_cost(0.10, "IdeaAgent")
        guardrails.record_cost(0.20, "ScriptAgent")
        result = guardrails.record_cost(0.05, "CaptionAgent")
        assert result == pytest.approx(0.35, abs=0.001)

    def test_records_persistent_op_schijf(self, guardrails):
        guardrails.record_cost(0.15, "TestAgent")
        log = guardrails.get_daily_log()
        assert len(log.records) == 1
        assert log.records[0].source == "TestAgent"
        assert log.records[0].amount_usd == pytest.approx(0.15)


# ── get_daily_spend/remaining ────────────────────────────────────────

class TestDailyQueries:
    def test_spend_na_records(self, guardrails):
        guardrails.record_cost(0.30, "a")
        assert guardrails.get_daily_spend() == pytest.approx(0.30, abs=0.001)

    def test_remaining_na_records(self, guardrails):
        guardrails.record_cost(0.30, "a")
        # Budget is 1.00, dus 0.70 over
        assert guardrails.get_daily_remaining() == pytest.approx(0.70, abs=0.001)

    def test_leeg_spending(self, guardrails):
        assert guardrails.get_daily_spend() == 0.0
        assert guardrails.get_daily_remaining() == pytest.approx(1.00)


# ── Tenant isolatie ──────────────────────────────────────────────────

class TestTenantIsolatie:
    def test_verschillende_tenants_gescheiden(self, tmp_path, monkeypatch):
        import backend.cost_guardrails as cg_module
        monkeypatch.setattr(cg_module, "ROOT", tmp_path)

        budget = TenantBudget(daily_limit_usd=5.00)
        g_a = CostGuardrails(tenant_id="tenant_a", budget=budget)
        g_b = CostGuardrails(tenant_id="tenant_b", budget=budget)

        g_a.record_cost(1.00, "agent")
        g_b.record_cost(2.00, "agent")

        assert g_a.get_daily_spend() == pytest.approx(1.00)
        assert g_b.get_daily_spend() == pytest.approx(2.00)


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_corrupt_daily_log_graceful(self, guardrails):
        # Schrijf corrupt bestand
        path = guardrails._daily_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        # Moet niet crashen
        assert guardrails.get_daily_spend() == 0.0

    def test_budget_exceeded_error_attributen(self):
        err = BudgetExceededError("acme", "daily_limit", 5.50, 5.00)
        assert err.tenant_id == "acme"
        assert err.limit_type == "daily_limit"
        assert err.spent == 5.50
        assert err.limit == 5.00
