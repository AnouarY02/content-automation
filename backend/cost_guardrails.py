"""
Cost Guardrails — budget limieten en circuit breaker voor LLM-kosten.

Per-tenant budget tracking:
    - Dagelijks budget per tenant (default: $5/dag)
    - Per-campagne hard limiet (uit cost_limits.json)
    - Circuit breaker: blokkeert calls wanneer budget overschreden is

Persistentie: data/cost_tracking/{tenant_id}/daily_{YYYY-MM-DD}.json

Gebruik:
    guardrails = CostGuardrails(tenant_id="acme")

    # Check vóór een agent call
    guardrails.check_budget()  # gooit BudgetExceededError

    # Registreer kosten na een agent call
    guardrails.record_cost(0.05, source="ScriptWriterAgent", campaign_id="camp_123")
"""

from __future__ import annotations

import json
from datetime import datetime, date
from pathlib import Path
from threading import Lock
from typing import Optional

from loguru import logger
from pydantic import BaseModel

ROOT = Path(__file__).parent.parent


# ── Configuratie ────────────────────────────────────────────────────────

class TenantBudget(BaseModel):
    """Budget configuratie per tenant. Defaults voor niet-geconfigureerde tenants."""
    daily_limit_usd: float = 5.00
    per_campaign_limit_usd: float = 1.00
    monthly_limit_usd: float = 50.00
    alert_threshold_pct: float = 80.0


_DEFAULT_BUDGET = TenantBudget()

# Laad globale limieten uit cost_limits.json
_COST_LIMITS_PATH = ROOT / "configs" / "cost_limits.json"
if _COST_LIMITS_PATH.exists():
    try:
        _raw = json.loads(_COST_LIMITS_PATH.read_text(encoding="utf-8"))
        _limits = _raw.get("limits", {})
        _DEFAULT_BUDGET = TenantBudget(
            per_campaign_limit_usd=_limits.get("max_cost_per_campaign_usd", 1.00),
            monthly_limit_usd=_limits.get("max_monthly_budget_usd", 50.00),
            alert_threshold_pct=_limits.get("alert_threshold_pct", 80),
        )
    except Exception:
        pass


# ── Exceptions ──────────────────────────────────────────────────────────

class BudgetExceededError(Exception):
    """Gooi wanneer een tenant het budget overschrijdt."""

    def __init__(self, tenant_id: str, limit_type: str, spent: float, limit: float):
        self.tenant_id = tenant_id
        self.limit_type = limit_type
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Budget overschreden voor tenant '{tenant_id}': "
            f"{limit_type} ${spent:.4f} >= ${limit:.2f}"
        )


# ── Cost tracking record ────────────────────────────────────────────────

class CostRecord(BaseModel):
    timestamp: str
    amount_usd: float
    source: str          # agent naam of "video_engine"
    campaign_id: str = ""
    cumulative_usd: float = 0.0


class DailyCostLog(BaseModel):
    tenant_id: str
    date: str
    total_usd: float = 0.0
    records: list[CostRecord] = []


# ── Guardrails ──────────────────────────────────────────────────────────

# Per-tenant locks voor thread safety
# _locks_mutex beschermt de dict zelf tegen race conditions bij gelijktijdige tenant-aanmaak
_locks: dict[str, Lock] = {}
_locks_mutex = Lock()


def _get_lock(tenant_id: str) -> Lock:
    with _locks_mutex:
        if tenant_id not in _locks:
            _locks[tenant_id] = Lock()
        return _locks[tenant_id]


def _resolve_tracking_dir(tenant_id: str) -> Path:
    if tenant_id == "default":
        return ROOT / "data" / "cost_tracking"
    return ROOT / "data" / "tenants" / tenant_id / "cost_tracking"


class CostGuardrails:
    """
    Budget enforcement per tenant.

    Thread-safe: gebruikt per-tenant locks voor concurrent access.
    """

    def __init__(self, tenant_id: str = "default", budget: TenantBudget | None = None):
        self._tenant_id = tenant_id
        self._budget = budget or _DEFAULT_BUDGET
        self._tracking_dir = _resolve_tracking_dir(tenant_id)
        self._tracking_dir.mkdir(parents=True, exist_ok=True)

    # ── Budget checks ───────────────────────────────────────────────

    def check_budget(self, estimated_cost: float = 0.0) -> None:
        """
        Check of er budget beschikbaar is. Gooit BudgetExceededError zo niet.

        Args:
            estimated_cost: Geschatte kosten van de volgende operatie.
        """
        daily = self._load_daily_log()
        projected = daily.total_usd + estimated_cost

        if projected >= self._budget.daily_limit_usd:
            raise BudgetExceededError(
                self._tenant_id, "daily_limit",
                daily.total_usd, self._budget.daily_limit_usd,
            )

        # Check maandlimiet
        monthly_total = self._get_monthly_total()
        if monthly_total + estimated_cost >= self._budget.monthly_limit_usd:
            raise BudgetExceededError(
                self._tenant_id, "monthly_limit",
                monthly_total, self._budget.monthly_limit_usd,
            )

        # Alert bij threshold
        daily_pct = (projected / self._budget.daily_limit_usd) * 100
        if daily_pct >= self._budget.alert_threshold_pct:
            logger.warning(
                f"[CostGuardrails] Tenant '{self._tenant_id}' bij {daily_pct:.0f}% "
                f"van dagelijks budget (${projected:.4f} / ${self._budget.daily_limit_usd:.2f})"
            )

    def check_campaign_budget(self, campaign_id: str, current_cost: float) -> None:
        """Check of een campagne het per-campagne budget niet overschrijdt."""
        if current_cost >= self._budget.per_campaign_limit_usd:
            raise BudgetExceededError(
                self._tenant_id, "per_campaign_limit",
                current_cost, self._budget.per_campaign_limit_usd,
            )

    # ── Cost recording ──────────────────────────────────────────────

    def record_cost(
        self,
        amount_usd: float,
        source: str,
        campaign_id: str = "",
    ) -> float:
        """
        Registreer kosten. Thread-safe.

        Returns:
            Cumulatief dagelijks totaal na registratie.
        """
        lock = _get_lock(self._tenant_id)
        with lock:
            daily = self._load_daily_log()
            daily.total_usd += amount_usd

            record = CostRecord(
                timestamp=datetime.now(tz=__import__("datetime").timezone.utc).isoformat(),
                amount_usd=amount_usd,
                source=source,
                campaign_id=campaign_id,
                cumulative_usd=daily.total_usd,
            )
            daily.records.append(record)
            self._save_daily_log(daily)

            logger.info(
                f"[CostGuardrails] {source}: ${amount_usd:.5f} | "
                f"Dagelijks totaal: ${daily.total_usd:.4f} / ${self._budget.daily_limit_usd:.2f} "
                f"(tenant={self._tenant_id})"
            )
            return daily.total_usd

    # ── Queries ─────────────────────────────────────────────────────

    def get_daily_spend(self) -> float:
        """Huidig dagelijks totaal."""
        return self._load_daily_log().total_usd

    def get_daily_remaining(self) -> float:
        """Resterend dagelijks budget."""
        return max(0.0, self._budget.daily_limit_usd - self.get_daily_spend())

    def get_daily_log(self) -> DailyCostLog:
        """Volledige dagelijkse log met records."""
        return self._load_daily_log()

    # ── Persistentie ────────────────────────────────────────────────

    def _daily_path(self, dt: date | None = None) -> Path:
        dt = dt or date.today()
        return self._tracking_dir / f"daily_{dt.isoformat()}.json"

    def _load_daily_log(self, dt: date | None = None) -> DailyCostLog:
        path = self._daily_path(dt)
        if not path.exists():
            return DailyCostLog(
                tenant_id=self._tenant_id,
                date=(dt or date.today()).isoformat(),
            )
        try:
            return DailyCostLog(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return DailyCostLog(
                tenant_id=self._tenant_id,
                date=(dt or date.today()).isoformat(),
            )

    def _save_daily_log(self, log: DailyCostLog) -> None:
        from utils.file_io import atomic_write_json
        atomic_write_json(self._daily_path(), log.model_dump(mode="json"))

    def _get_monthly_total(self) -> float:
        """Som van alle dagelijkse besteding in de huidige maand."""
        today = date.today()
        total = 0.0
        for day_num in range(1, today.day + 1):
            dt = date(today.year, today.month, day_num)
            log = self._load_daily_log(dt)
            total += log.total_usd
        return total
