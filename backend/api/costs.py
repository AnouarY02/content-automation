"""
Cost API — budget monitoring endpoints per tenant.

Routes:
  GET /api/costs/daily    — dagelijks verbruik + resterend budget
  GET /api/costs/monthly  — maandelijks overzicht
"""

from fastapi import APIRouter, Query

from backend.cost_guardrails import CostGuardrails

router = APIRouter()


@router.get("/daily")
def get_daily_costs(
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Dagelijks LLM-kostenverbruik voor een tenant."""
    g = CostGuardrails(tenant_id=tenant_id)
    log = g.get_daily_log()
    return {
        "tenant_id": tenant_id,
        "date": log.date,
        "total_usd": round(log.total_usd, 5),
        "remaining_usd": round(g.get_daily_remaining(), 5),
        "records_count": len(log.records),
        "records": [r.model_dump() for r in log.records[-20:]],  # laatste 20
    }


@router.get("/monthly")
def get_monthly_costs(
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Maandelijks LLM-kostenverbruik voor een tenant."""
    g = CostGuardrails(tenant_id=tenant_id)
    monthly_total = g._get_monthly_total()
    return {
        "tenant_id": tenant_id,
        "monthly_total_usd": round(monthly_total, 5),
        "monthly_limit_usd": g._budget.monthly_limit_usd,
        "monthly_remaining_usd": round(g._budget.monthly_limit_usd - monthly_total, 5),
        "daily_limit_usd": g._budget.daily_limit_usd,
        "daily_spent_usd": round(g.get_daily_spend(), 5),
    }
