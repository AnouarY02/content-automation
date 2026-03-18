"""
DAG 6 — Unit tests: backend/services/approval_service.py

Dekt de veiligheid-kritieke approval flow:
  - can_publish() True alleen als APPROVED + approved_by + approved_at
  - can_publish() False als status != APPROVED
  - can_publish() False als approved_by is None
  - process_approval() APPROVE → status APPROVED + approved_by
  - process_approval() REJECT → status REJECTED + rejection_reason
  - process_approval() REQUEST_CHANGES → status DRAFT
  - process_approval() guard: verkeerde status → ValueError
  - process_approval() APPROVE met scheduled_for → niet direct gepubliceerd
  - _publish_now() guard: niet-APPROVED status → PermissionError
  - _publish_now() guard: geen approved_by → PermissionError
  - process_approval() APPROVE zonder scheduled_for → publiceert direct (mock)
  - Publisher fout → bundle status FAILED + exception opnieuw gegooid
"""

import unittest.mock as mock
from datetime import datetime, timedelta

import pytest

from backend.models.campaign import (
    ApprovalDecision,
    ApprovalRequest,
    CampaignBundle,
    CampaignStatus,
)
from backend.services.approval_service import (
    _publish_now,
    can_publish,
    process_approval,
)


# ── can_publish ───────────────────────────────────────────────────────

class TestCanPublish:
    def _approved_bundle(self) -> CampaignBundle:
        b = CampaignBundle(app_id="app_test", status=CampaignStatus.APPROVED)
        b.approved_by = "operator"
        b.approved_at = datetime.utcnow()
        return b

    def test_volledig_goedgekeurd_is_publishable(self):
        assert can_publish(self._approved_bundle()) is True

    def test_status_niet_approved_is_niet_publishable(self):
        b = self._approved_bundle()
        b.status = CampaignStatus.PENDING_APPROVAL
        assert can_publish(b) is False

    def test_geen_approved_by_is_niet_publishable(self):
        b = self._approved_bundle()
        b.approved_by = None
        assert can_publish(b) is False

    def test_geen_approved_at_is_niet_publishable(self):
        b = self._approved_bundle()
        b.approved_at = None
        assert can_publish(b) is False

    def test_draft_status_is_niet_publishable(self):
        b = CampaignBundle(app_id="app_test", status=CampaignStatus.DRAFT)
        assert can_publish(b) is False

    def test_failed_status_is_niet_publishable(self):
        b = self._approved_bundle()
        b.status = CampaignStatus.FAILED
        assert can_publish(b) is False


# ── Helpers voor process_approval mocking ────────────────────────────

def _mock_bundle(campaign_id: str = "camp_001", status: CampaignStatus = CampaignStatus.PENDING_APPROVAL) -> CampaignBundle:
    b = CampaignBundle(app_id="app_test", status=status)
    b.id = campaign_id
    return b


def _approval_request(
    campaign_id: str = "camp_001",
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    notes: str = "",
    scheduled_for=None,
) -> ApprovalRequest:
    return ApprovalRequest(
        campaign_id=campaign_id,
        decision=decision,
        notes=notes,
        scheduled_for=scheduled_for,
    )


# ── process_approval: APPROVE ─────────────────────────────────────────

class TestProcessApprovalApprove:
    def test_approve_met_scheduled_for_zet_status_approved(self):
        """Met scheduled_for → status APPROVED, niet direct gepubliceerd."""
        bundle = _mock_bundle()
        scheduled = datetime.utcnow() + timedelta(hours=2)

        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
            mock.patch("backend.services.approval_service._publish_now") as mock_publish,
        ):
            result = process_approval(
                _approval_request(scheduled_for=scheduled),
                approved_by="operator"
            )
        assert result.status == CampaignStatus.APPROVED
        mock_publish.assert_not_called()

    def test_approve_zonder_scheduled_for_publiceert_direct(self):
        """Zonder scheduled_for → _publish_now aangeroepen."""
        bundle = _mock_bundle()

        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
            mock.patch("backend.services.approval_service._publish_now",
                       return_value=bundle) as mock_publish,
        ):
            process_approval(_approval_request(), approved_by="operator")
        mock_publish.assert_called_once_with(bundle)

    def test_approve_zet_approved_by(self):
        bundle = _mock_bundle()
        scheduled = datetime.utcnow() + timedelta(hours=1)

        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
            mock.patch("backend.services.approval_service._publish_now"),
        ):
            result = process_approval(
                _approval_request(scheduled_for=scheduled),
                approved_by="anouar"
            )
        assert result.approved_by == "anouar"

    def test_approve_zet_approved_at(self):
        bundle = _mock_bundle()
        scheduled = datetime.utcnow() + timedelta(hours=1)

        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
            mock.patch("backend.services.approval_service._publish_now"),
        ):
            result = process_approval(
                _approval_request(scheduled_for=scheduled),
                approved_by="operator"
            )
        assert result.approved_at is not None


# ── process_approval: REJECT ──────────────────────────────────────────

class TestProcessApprovalReject:
    def test_reject_zet_status_rejected(self):
        bundle = _mock_bundle()
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
        ):
            result = process_approval(
                _approval_request(decision=ApprovalDecision.REJECT, notes="Te lang"),
            )
        assert result.status == CampaignStatus.REJECTED

    def test_reject_slaat_rejection_reason_op(self):
        bundle = _mock_bundle()
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
        ):
            result = process_approval(
                _approval_request(decision=ApprovalDecision.REJECT, notes="Niet on-brand"),
            )
        assert result.rejection_reason == "Niet on-brand"


# ── process_approval: REQUEST_CHANGES ────────────────────────────────

class TestProcessApprovalRequestChanges:
    def test_request_changes_zet_status_draft(self):
        bundle = _mock_bundle()
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
        ):
            result = process_approval(
                _approval_request(decision=ApprovalDecision.REQUEST_CHANGES, notes="Wijzig hook"),
            )
        assert result.status == CampaignStatus.DRAFT

    def test_request_changes_slaat_notes_op(self):
        bundle = _mock_bundle()
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
            mock.patch("backend.services.approval_service.save_bundle"),
        ):
            result = process_approval(
                _approval_request(decision=ApprovalDecision.REQUEST_CHANGES, notes="Andere CTA"),
            )
        assert "Andere CTA" in result.approval_notes


# ── process_approval: guards ──────────────────────────────────────────

class TestProcessApprovalGuards:
    def test_verkeerde_status_gooit_valueerror(self):
        bundle = _mock_bundle(status=CampaignStatus.DRAFT)
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
        ):
            with pytest.raises(ValueError, match="pending_approval"):
                process_approval(_approval_request())

    def test_approved_status_gooit_valueerror(self):
        """Al goedgekeurde campagne kan niet opnieuw goedgekeurd worden."""
        bundle = _mock_bundle(status=CampaignStatus.APPROVED)
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
        ):
            with pytest.raises(ValueError):
                process_approval(_approval_request())

    def test_published_status_gooit_valueerror(self):
        bundle = _mock_bundle(status=CampaignStatus.PUBLISHED)
        with (
            mock.patch("backend.services.approval_service.load_bundle", return_value=bundle),
        ):
            with pytest.raises(ValueError):
                process_approval(_approval_request())


# ── _publish_now: veiligheids-guards ─────────────────────────────────

class TestPublishNowGuards:
    def test_niet_approved_status_gooit_permission_error(self):
        bundle = CampaignBundle(app_id="app_test", status=CampaignStatus.PENDING_APPROVAL)
        bundle.approved_by = "operator"
        with pytest.raises(PermissionError, match="VEILIGHEIDSFOUT"):
            _publish_now(bundle)

    def test_geen_approved_by_gooit_permission_error(self):
        bundle = CampaignBundle(app_id="app_test", status=CampaignStatus.APPROVED)
        bundle.approved_by = None
        with pytest.raises(PermissionError, match="VEILIGHEIDSFOUT"):
            _publish_now(bundle)

    def test_publisher_fout_zet_status_failed(self):
        bundle = CampaignBundle(app_id="app_test", status=CampaignStatus.APPROVED)
        bundle.approved_by = "operator"
        bundle.approved_at = datetime.utcnow()

        with (
            mock.patch("backend.services.approval_service.save_bundle"),
            mock.patch("backend.services.approval_service.TikTokPublisher") as mock_pub_cls,
        ):
            mock_pub_cls.return_value.publish.side_effect = RuntimeError("TikTok API down")
            with pytest.raises(RuntimeError):
                _publish_now(bundle)
        assert bundle.status == CampaignStatus.FAILED
