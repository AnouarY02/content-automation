"""
Approval Service — beheert de goedkeuringsflow voor campagnes.

KERNREGEL: Geen enkele campagne wordt gepubliceerd zonder approved=True + approved_by ingevuld.
Dit is een hard requirement en mag nooit worden omzeild.

AUTO-APPROVE LOGICA:
  - Als APPROVAL_REQUIRED=false → altijd auto-approve
  - Als viral score >= AUTO_APPROVE_THRESHOLD (standaard 80) → auto-approve
  - Anders → wacht op handmatige goedkeuring
"""

import os
from datetime import datetime
from pathlib import Path

from loguru import logger

from backend.models.campaign import (
    ApprovalDecision,
    ApprovalRequest,
    CampaignBundle,
    CampaignStatus,
)
from workflows.campaign_pipeline import load_bundle, save_bundle
from channels.tiktok.publisher import TikTokPublisher
from channels.instagram.publisher import InstagramPublisher
from channels.facebook.publisher import FacebookPublisher


def get_pending_campaigns() -> list[CampaignBundle]:
    """Haal alle campagnes op die wachten op goedkeuring."""
    from workflows.campaign_pipeline import list_pending_campaigns
    return list_pending_campaigns()


def process_approval(request: ApprovalRequest, approved_by: str = "user", tenant_id: str = "default") -> CampaignBundle:
    """
    Verwerk een goedkeuringsbeslissing.

    VEILIGHEIDSCHECK: Verificeer altijd dat:
    1. De campagne bestaat
    2. De status PENDING_APPROVAL is
    3. De beslissing expliciet is (nooit impliciet goedgekeurd)
    """
    bundle = load_bundle(request.campaign_id, tenant_id=tenant_id)

    if bundle.status != CampaignStatus.PENDING_APPROVAL:
        raise ValueError(
            f"Campagne {request.campaign_id} heeft status '{bundle.status}' — "
            f"kan alleen campagnes goedkeuren met status 'pending_approval'"
        )

    if request.decision == ApprovalDecision.APPROVE:
        bundle.status = CampaignStatus.APPROVED
        bundle.approved_by = approved_by
        bundle.approved_at = datetime.utcnow()
        bundle.approval_notes = request.notes
        if request.scheduled_for:
            bundle.scheduled_for = request.scheduled_for

        save_bundle(bundle, tenant_id=tenant_id)
        logger.info(f"✓ Campagne {bundle.id} GOEDGEKEURD door {approved_by}")

        # Direct publiceren als geen scheduled_for
        if not bundle.scheduled_for:
            bundle = _publish_now(bundle)

    elif request.decision == ApprovalDecision.REJECT:
        bundle.status = CampaignStatus.REJECTED
        bundle.rejection_reason = request.notes
        save_bundle(bundle, tenant_id=tenant_id)
        logger.info(f"✗ Campagne {bundle.id} AFGEWEZEN: {request.notes}")

    elif request.decision == ApprovalDecision.REQUEST_CHANGES:
        # Status terug naar draft zodat pipeline opnieuw kan draaien
        bundle.status = CampaignStatus.DRAFT
        bundle.approval_notes = f"Gevraagde wijzigingen: {request.notes}"
        save_bundle(bundle, tenant_id=tenant_id)
        logger.info(f"↩ Campagne {bundle.id} teruggestuurd voor wijzigingen")

    return bundle


def _publish_now(bundle: CampaignBundle) -> CampaignBundle:
    """
    Publiceert een goedgekeurde campagne direct.
    Wordt alleen aangeroepen NADAT goedkeuring is bevestigd.
    """
    if bundle.status != CampaignStatus.APPROVED:
        raise PermissionError(
            f"VEILIGHEIDSFOUT: Poging om niet-goedgekeurde campagne te publiceren! "
            f"Status was: {bundle.status}"
        )
    if not bundle.approved_by:
        raise PermissionError(
            "VEILIGHEIDSFOUT: Campagne heeft geen approved_by — publicatie geweigerd"
        )

    bundle.status = CampaignStatus.PUBLISHING
    save_bundle(bundle)

    try:
        platform = (bundle.platform or "tiktok").lower()
        if platform == "instagram":
            publisher = InstagramPublisher()
            platform_label = "Instagram"
        elif platform == "facebook":
            publisher = FacebookPublisher()
            platform_label = "Facebook"
        else:
            publisher = TikTokPublisher()
            platform_label = "TikTok"

        post_id = publisher.publish(bundle)

        bundle.status = CampaignStatus.PUBLISHED
        bundle.published_at = datetime.utcnow()
        bundle.post_id = post_id  # opgeslagen via het model

        # Registreer voor automatische analytics checks (24h, 48h, 7d)
        try:
            from workflows.feedback_loop import schedule_post_check
            schedule_post_check(
                post_id=post_id,
                campaign_id=bundle.id,
                app_id=bundle.app_id,
                published_at=bundle.published_at,
            )
        except Exception as fb_err:
            logger.warning(f"Feedback registratie mislukt (niet kritiek): {fb_err}")

        logger.success(f"✓ Gepubliceerd op {platform_label}! Post ID: {post_id}")

    except Exception as e:
        bundle.status = CampaignStatus.FAILED
        logger.error(f"Publicatie mislukt voor {bundle.id}: {e}")
        raise

    save_bundle(bundle)
    return bundle


def can_publish(bundle: CampaignBundle) -> bool:
    """
    Harde veiligheidscheck — moet True zijn voordat ENIGE publicatie plaatsvindt.
    """
    return (
        bundle.status == CampaignStatus.APPROVED
        and bundle.approved_by is not None
        and bundle.approved_at is not None
    )


def try_auto_approve(bundle: CampaignBundle, tenant_id: str = "default") -> bool:
    """
    Probeer een campagne automatisch goed te keuren en te publiceren.

    Auto-keurt als:
      - APPROVAL_REQUIRED=false, OF
      - viral score >= AUTO_APPROVE_THRESHOLD (standaard 80)

    Returns:
        True als auto-goedkeuring plaatsvond, anders False.
    """
    if bundle.status != CampaignStatus.PENDING_APPROVAL:
        return False

    approval_required = os.getenv("APPROVAL_REQUIRED", "true").lower() == "true"
    auto_threshold = int(os.getenv("AUTO_APPROVE_THRESHOLD", "70"))
    viral_score = (bundle.viral_score or {}).get("composite_score", 0)

    should_auto_approve = (not approval_required) or (viral_score >= auto_threshold)

    if not should_auto_approve:
        logger.info(
            f"[AutoApprove] Campagne {bundle.id} wacht op handmatige goedkeuring "
            f"(viral score={viral_score} < drempel={auto_threshold}, APPROVAL_REQUIRED={approval_required})"
        )
        return False

    reason = (
        "APPROVAL_REQUIRED=false"
        if not approval_required
        else f"viral score {viral_score}/100 ≥ drempel {auto_threshold}"
    )

    bundle.status = CampaignStatus.APPROVED
    bundle.approved_by = f"auto ({reason})"
    bundle.approved_at = datetime.utcnow()
    bundle.approval_notes = f"Automatisch goedgekeurd — {reason}"
    save_bundle(bundle, tenant_id=tenant_id)

    logger.info(f"[AutoApprove] ✓ Campagne {bundle.id} auto-goedgekeurd ({reason})")

    try:
        _publish_now(bundle)
    except Exception as pub_err:
        logger.error(f"[AutoApprove] Publicatie mislukt voor {bundle.id}: {pub_err}")
        # Herstel naar PENDING_APPROVAL zodat de campagne handmatig goedgekeurd kan worden
        # (niet FAILED — de video is gewoon aanwezig, alleen upload mislukte)
        bundle.status = CampaignStatus.PENDING_APPROVAL
        save_bundle(bundle, tenant_id=tenant_id)
        logger.info(f"[AutoApprove] Campagne {bundle.id} teruggezet naar PENDING_APPROVAL voor handmatige publicatie")
        return False

    return True
