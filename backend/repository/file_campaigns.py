"""
FileCampaignRepository — file-based implementatie van ICampaignRepository.

Datalocatie:
  default tenant : data/campaigns/
  overige tenants: data/tenants/{tenant_id}/campaigns/

Elk bestand: {campaign_id}.json  (CampaignBundle serialized)

Swappen naar DB:
  Vervang door PgCampaignRepository en update factory in backend/api/campaigns.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from backend.models.campaign import CampaignBundle, CampaignStatus
from utils.runtime_paths import ensure_writable_dir, get_runtime_data_dir
from utils.file_io import atomic_write_json

ROOT = Path(__file__).parent.parent.parent  # content-automation/


def _campaigns_dir(tenant_id: str) -> Path:
    """
    'default' → data/campaigns/        (backward compat — bestaande data onaangetast)
    overig    → data/tenants/{tenant_id}/campaigns/
    """
    if tenant_id == "default":
        return ensure_writable_dir(ROOT / "data" / "campaigns", get_runtime_data_dir("campaigns"))
    return ensure_writable_dir(
        ROOT / "data" / "tenants" / tenant_id / "campaigns",
        get_runtime_data_dir("tenants", tenant_id, "campaigns"),
    )


class FileCampaignRepository:
    """
    File-based opslag voor CampaignBundles, volledig tenant-geïsoleerd.

    Gebruik:
        repo = FileCampaignRepository(tenant_id="acme")
        repo.save(bundle)
        bundle = repo.get("camp_abc123")
    """

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._dir       = _campaigns_dir(tenant_id)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Schrijven ──────────────────────────────────────────────────────

    def save(self, bundle: CampaignBundle) -> None:
        """Sla campagne op (upsert op bundle.id). Stelt tenant_id in als leeg."""
        if not bundle.tenant_id or bundle.tenant_id == "default":
            bundle = bundle.model_copy(update={"tenant_id": self._tenant_id})
        path = self._dir / f"{bundle.id}.json"
        atomic_write_json(path, bundle.model_dump(mode="json"), default=str)

    # ── Lezen ──────────────────────────────────────────────────────────

    def get(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> CampaignBundle | None:
        path = _campaigns_dir(tenant_id) / f"{campaign_id}.json"
        if not path.exists():
            return None
        try:
            return CampaignBundle(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning(f"[FileCampaignRepo] Kan {campaign_id} niet laden: {exc}")
            return None

    def list(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
        status: str | None = None,
    ) -> list[CampaignBundle]:
        """Gefilterde lijst, nieuwste eerst."""
        bundles: list[CampaignBundle] = []
        campaigns_dir = _campaigns_dir(tenant_id)
        if not campaigns_dir.exists():
            return []

        for path in campaigns_dir.glob("*.json"):
            try:
                bundle = CampaignBundle(**json.loads(path.read_text(encoding="utf-8")))
                if app_id and bundle.app_id != app_id:
                    continue
                if status and bundle.status != status:
                    continue
                bundles.append(bundle)
            except Exception as exc:
                logger.warning(f"[FileCampaignRepo] Overgeslagen ({path.name}): {exc}")

        return sorted(bundles, key=lambda b: b.created_at, reverse=True)

    def list_pending(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
    ) -> list[CampaignBundle]:
        return self.list(
            tenant_id=tenant_id,
            app_id=app_id,
            status=CampaignStatus.PENDING_APPROVAL,
        )

    def delete(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> bool:
        path = _campaigns_dir(tenant_id) / f"{campaign_id}.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except OSError as exc:
            logger.error(f"[FileCampaignRepo] Verwijderen mislukt ({campaign_id}): {exc}")
            return False
