"""
Approval API endpoints.

SECURITY:
  - campaign_id wordt gesaniteerd (geen path traversal, max 64 tekens)
  - approved_by afgeleid van X-Approved-By header of geauth. tenant
  - Elke beslissing gelogd met identity, beslissing en IP-adres
"""

import re

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from backend.models.campaign import ApprovalRequest, CampaignBundle
from backend.services.approval_service import process_approval, get_pending_campaigns

router = APIRouter()

_SAFE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')


def _sanitize_id(value: str, field_name: str = "id") -> str:
    """Saniteer een ID — voorkomt path traversal en injectie."""
    if not value or not _SAFE_ID_PATTERN.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Ongeldig {field_name}: alleen letters, cijfers, _ en - toegestaan (max 64).",
        )
    return value


def _extract_identity(request: Request) -> str:
    """
    Haal de identiteit op van de beslisser (voor audit trail).
    Prioriteit: X-Approved-By header > tenant ID > fallback.
    """
    approved_by = request.headers.get("X-Approved-By", "").strip()
    if approved_by:
        safe = re.sub(r'[^\w\s@.\-]', '', approved_by)[:100].strip()
        if safe:
            return safe
    tenant = getattr(request.state, "tenant", None)
    if tenant and hasattr(tenant, "tenant_id"):
        return f"tenant:{tenant.tenant_id}"
    return "dashboard-user"


@router.get("/pending")
def pending_approvals():
    """Haal alle campagnes op die wachten op goedkeuring."""
    return get_pending_campaigns()


@router.post("/decide", response_model=CampaignBundle)
def decide(request: Request, body: ApprovalRequest, tenant_id: str = Query("default", description="Tenant identifier")):
    """
    Verwerk een goedkeuringsbeslissing.
    Beslissingen: approve | reject | request_changes

    Header (optioneel):
      X-Approved-By: <naam>  — Wie de beslissing neemt (audit trail)
    """
    _sanitize_id(body.campaign_id, "campaign_id")

    identity  = _extract_identity(request)
    client_ip = request.client.host if request.client else "unknown"

    logger.info(
        f"[Approval] beslissing={body.decision} | campaign={body.campaign_id} | "
        f"door={identity} | ip={client_ip}"
    )

    try:
        return process_approval(body, approved_by=identity, tenant_id=tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Campagne '{body.campaign_id}' niet gevonden.")
    except Exception as e:
        logger.error(f"[Approval] Onverwachte fout: {e}")
        raise HTTPException(status_code=500, detail="Interne fout bij verwerken beslissing.")
