from __future__ import annotations

from loguru import logger

from backend.models.campaign import CampaignBundle, CampaignStatus
from backend.supabase import get_supabase_client


def _to_row(bundle: CampaignBundle) -> dict:
    return {
        "id": bundle.id,
        "app_id": bundle.app_id,
        "tenant_id": bundle.tenant_id,
        "platform": bundle.platform,
        "status": bundle.status if isinstance(bundle.status, str) else bundle.status.value,
        "display_name": bundle.display_name,
        "idea": bundle.idea or None,
        "script": bundle.script or None,
        "caption": bundle.caption or None,
        "viral_score": bundle.viral_score,
        "video_url": bundle.video_path,
        "video_metadata": {
            "thumbnail_path": bundle.thumbnail_path,
            "scheduled_for": bundle.scheduled_for.isoformat() if bundle.scheduled_for else None,
            "rejection_reason": bundle.rejection_reason,
        },
        "total_cost_usd": bundle.total_cost_usd,
        "cost_breakdown": [],
        "approved_by": bundle.approved_by,
        "approval_notes": bundle.approval_notes,
        "approved_at": bundle.approved_at.isoformat() if bundle.approved_at else None,
        "post_id": bundle.post_id,
        "published_at": bundle.published_at.isoformat() if bundle.published_at else None,
        "experiment_id": bundle.experiment_id,
        "created_at": bundle.created_at.isoformat(),
        "updated_at": bundle.created_at.isoformat(),
    }


def _from_row(row: dict) -> CampaignBundle:
    metadata = row.get("video_metadata") or {}
    return CampaignBundle(
        id=row["id"],
        app_id=row["app_id"],
        tenant_id=row.get("tenant_id") or "default",
        platform=row.get("platform") or "tiktok",
        status=row.get("status") or CampaignStatus.DRAFT,
        idea=row.get("idea") or {},
        script=row.get("script") or {},
        caption=row.get("caption") or {},
        video_path=row.get("video_url"),
        thumbnail_path=metadata.get("thumbnail_path"),
        viral_score=row.get("viral_score"),
        post_id=row.get("post_id"),
        experiment_id=row.get("experiment_id"),
        total_cost_usd=float(row.get("total_cost_usd") or 0),
        created_at=row.get("created_at"),
        display_name=row.get("display_name"),
        approved_at=row.get("approved_at"),
        published_at=row.get("published_at"),
        approved_by=row.get("approved_by"),
        approval_notes=row.get("approval_notes"),
        rejection_reason=metadata.get("rejection_reason"),
        scheduled_for=metadata.get("scheduled_for"),
    )


class SupabaseCampaignRepository:
    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._client = get_supabase_client()
        if not self._client:
            raise RuntimeError("Supabase campaign repository vereist geldige SUPABASE env vars.")

    def save(self, bundle: CampaignBundle) -> None:
        if not bundle.tenant_id or bundle.tenant_id == "default":
            bundle = bundle.model_copy(update={"tenant_id": self._tenant_id})

        result = self._client.table("campaigns").upsert(_to_row(bundle)).execute()
        if getattr(result, "data", None) is None:
            raise RuntimeError("Supabase upsert voor campaign gaf geen data terug.")

    def get(self, campaign_id: str, tenant_id: str = "default") -> CampaignBundle | None:
        result = (
            self._client.table("campaigns")
            .select("*")
            .eq("tenant_id", tenant_id)
            .eq("id", campaign_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return _from_row(rows[0])

    def list(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
        status: str | None = None,
    ) -> list[CampaignBundle]:
        query = self._client.table("campaigns").select("*").eq("tenant_id", tenant_id).order("created_at", desc=True)
        if app_id:
            query = query.eq("app_id", app_id)
        if status:
            query = query.eq("status", status if isinstance(status, str) else status.value)

        result = query.execute()
        bundles: list[CampaignBundle] = []
        for row in result.data or []:
            try:
                bundles.append(_from_row(row))
            except Exception as exc:
                logger.warning(f"[SupabaseCampaignRepo] Overgeslagen row: {exc}")
        return bundles

    def list_pending(self, tenant_id: str = "default", app_id: str | None = None) -> list[CampaignBundle]:
        return self.list(tenant_id=tenant_id, app_id=app_id, status=CampaignStatus.PENDING_APPROVAL)

    def delete(self, campaign_id: str, tenant_id: str = "default") -> bool:
        result = (
            self._client.table("campaigns")
            .delete()
            .eq("tenant_id", tenant_id)
            .eq("id", campaign_id)
            .execute()
        )
        return bool(result.data)
