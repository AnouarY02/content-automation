"""
Unit tests: backend/repository/file_campaigns.py — file-based campaign repository.

Dekt:
  - FileCampaignRepository.save(): opslaan + upsert
  - FileCampaignRepository.get(): laden + None bij niet-gevonden
  - FileCampaignRepository.list(): filteren op app_id en status
  - FileCampaignRepository.list_pending(): shortcut
  - FileCampaignRepository.delete(): verwijderen + False bij niet-gevonden
  - Tenant isolatie: gescheiden directories
"""

import pytest

import backend.repository.file_campaigns as fc_module
from backend.repository.file_campaigns import FileCampaignRepository
from backend.models.campaign import CampaignBundle, CampaignStatus


@pytest.fixture(autouse=True)
def isolated_repo(tmp_path, monkeypatch):
    """Isoleer FileCampaignRepository naar tmp_path."""
    monkeypatch.setattr(fc_module, "ROOT", tmp_path)
    return tmp_path


def _make_bundle(
    app_id: str = "app_test",
    status: CampaignStatus = CampaignStatus.PENDING_APPROVAL,
    tenant_id: str = "default",
) -> CampaignBundle:
    return CampaignBundle(
        app_id=app_id,
        tenant_id=tenant_id,
        platform="tiktok",
        status=status,
    )


class TestSave:
    def test_save_en_get(self):
        repo = FileCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        loaded = repo.get(bundle.id)
        assert loaded is not None
        assert loaded.id == bundle.id
        assert loaded.app_id == "app_test"

    def test_upsert_overschrijft(self):
        repo = FileCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        updated = bundle.model_copy(update={"platform": "instagram"})
        repo.save(updated)
        loaded = repo.get(bundle.id)
        assert loaded.platform == "instagram"

    def test_stelt_tenant_id_in(self):
        repo = FileCampaignRepository(tenant_id="acme")
        bundle = _make_bundle(tenant_id="default")
        repo.save(bundle)
        loaded = repo.get(bundle.id, tenant_id="acme")
        assert loaded.tenant_id == "acme"


class TestGet:
    def test_none_bij_niet_gevonden(self):
        repo = FileCampaignRepository()
        assert repo.get("niet_bestaand") is None


class TestList:
    def test_alle_campagnes(self):
        repo = FileCampaignRepository()
        repo.save(_make_bundle())
        repo.save(_make_bundle())
        assert len(repo.list()) == 2

    def test_filter_op_app_id(self):
        repo = FileCampaignRepository()
        repo.save(_make_bundle(app_id="app_a"))
        repo.save(_make_bundle(app_id="app_b"))
        result = repo.list(app_id="app_a")
        assert len(result) == 1
        assert result[0].app_id == "app_a"

    def test_filter_op_status(self):
        repo = FileCampaignRepository()
        repo.save(_make_bundle(status=CampaignStatus.PENDING_APPROVAL))
        repo.save(_make_bundle(status=CampaignStatus.APPROVED))
        result = repo.list(status=CampaignStatus.PENDING_APPROVAL)
        assert len(result) == 1

    def test_lege_directory(self):
        repo = FileCampaignRepository(tenant_id="leeg")
        assert repo.list(tenant_id="leeg") == []


class TestListPending:
    def test_alleen_pending(self):
        repo = FileCampaignRepository()
        repo.save(_make_bundle(status=CampaignStatus.PENDING_APPROVAL))
        repo.save(_make_bundle(status=CampaignStatus.APPROVED))
        repo.save(_make_bundle(status=CampaignStatus.FAILED))
        pending = repo.list_pending()
        assert len(pending) == 1
        assert pending[0].status == CampaignStatus.PENDING_APPROVAL


class TestDelete:
    def test_verwijder_bestaand(self):
        repo = FileCampaignRepository()
        bundle = _make_bundle()
        repo.save(bundle)
        assert repo.delete(bundle.id) is True
        assert repo.get(bundle.id) is None

    def test_verwijder_niet_bestaand(self):
        repo = FileCampaignRepository()
        assert repo.delete("niet_bestaand") is False


class TestTenantIsolatie:
    def test_gescheiden_data(self):
        repo_a = FileCampaignRepository(tenant_id="tenant_a")
        repo_b = FileCampaignRepository(tenant_id="tenant_b")

        bundle_a = _make_bundle()
        bundle_b = _make_bundle()

        repo_a.save(bundle_a)
        repo_b.save(bundle_b)

        assert len(repo_a.list(tenant_id="tenant_a")) == 1
        assert len(repo_b.list(tenant_id="tenant_b")) == 1
        assert repo_a.get(bundle_b.id, tenant_id="tenant_a") is None
