"""
Repository Protocol interfaces — database-ready abstractions.

Alle concrete implementaties (file-based, PostgreSQL, Redis, etc.)
moeten deze Protocols implementeren. Duck typing via @runtime_checkable
zorgt dat isinstance()-checks werken zonder overerving.

Conventies:
  - tenant_id: str  — altijd aanwezig, default="default"
  - app_id: str     — scope binnen tenant
  - Methoden retourneren None / lege lijst bij niet gevonden (nooit 404)
  - Methoden gooien geen FileNotFoundError — dat is implementatiedetail
  - Alle writes zijn idempotent (save = upsert)

DB-migratie pad:
  1. Implementeer Protocol in pg_experiments.py (asyncpg/SQLAlchemy)
  2. Vervang file_* factories in backend/api/*.py
  3. Voer datamigratie uit (scripts/migrate_to_db.py)
  4. Verwijder file_* bestanden
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.models.campaign import CampaignBundle
from experiments.models import Experiment
from maturity.models import MaturityScorecard, MaturitySnapshot


@runtime_checkable
class IExperimentRepository(Protocol):
    """Read/write interface voor experimenten — tenant + app geïsoleerd."""

    def save(self, experiment: Experiment) -> None:
        """Sla experiment op (upsert op experiment_id)."""
        ...

    def get(
        self,
        experiment_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        """Laad experiment op ID. None als niet gevonden."""
        ...

    def list_by_app(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        """Alle experimenten voor een app, nieuwste eerst."""
        ...

    def list_measuring(
        self,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        """Experimenten in MEASURING/PUBLISHED status — voor comparator."""
        ...

    def get_by_campaign(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        """Experiment gekoppeld aan campagne_id."""
        ...

    def get_concluded_dimensions(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> dict[str, int]:
        """Dimensie → aantal geconcludeerde experimenten."""
        ...


@runtime_checkable
class ICampaignRepository(Protocol):
    """Read/write interface voor campagne bundles — tenant + app geïsoleerd."""

    def save(self, bundle: CampaignBundle) -> None:
        """Sla campagne op (upsert op bundle.id)."""
        ...

    def get(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> CampaignBundle | None:
        """Laad campagne op ID. None als niet gevonden."""
        ...

    def list(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
        status: str | None = None,
    ) -> list[CampaignBundle]:
        """Gefilterde lijst van campagnes, nieuwste eerst."""
        ...

    def list_pending(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
    ) -> list[CampaignBundle]:
        """Campagnes met status PENDING_APPROVAL."""
        ...

    def delete(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> bool:
        """Verwijder campagne. True als verwijderd, False als niet gevonden."""
        ...


@runtime_checkable
class IMaturityRepository(Protocol):
    """Read/write interface voor maturity scorecards — tenant + app geïsoleerd."""

    def save_scorecard(self, scorecard: MaturityScorecard) -> None:
        """Sla scorecard op als latest + append aan history."""
        ...

    def get_latest(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> MaturityScorecard | None:
        """Meest recente scorecard. None als nog geen bestaat."""
        ...

    def get_history(
        self,
        app_id: str,
        tenant_id: str = "default",
        limit: int = 50,
    ) -> list[MaturitySnapshot]:
        """Historische snapshots, nieuwste eerst."""
        ...
