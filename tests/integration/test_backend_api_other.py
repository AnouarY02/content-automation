"""
DAG 7 — Integration tests: overige backend API routers

Dekt:
  analytics.py:
  - GET /{app_id}/summary → geen bestand → default response
  - GET /{app_id}/summary → bestand aanwezig → geladen
  - GET /{app_id}/posts → geen bestand → []
  - GET /{app_id}/posts → bestand aanwezig → data
  - GET /{app_id}/posts → limit werkt

  apps.py:
  - GET / → lijst apps uit registry
  - GET /{app_id}/brand-memory → gevonden
  - GET /{app_id}/brand-memory → niet gevonden (lege dict) → 404
  - PATCH /{app_id}/brand-memory → roept apply_updates aan

  approvals.py:
  - GET /pending → lijst terug
  - POST /decide → success → 200 CampaignBundle
  - POST /decide → ValueError → 400
  - POST /decide → PermissionError → 403
  - POST /decide → onverwachte fout → 500

  campaigns.py:
  - GET /pending → lijst
  - GET /{campaign_id} → gevonden
  - GET /{campaign_id} → niet gevonden → 404
  - GET / → lijst alle campagnes
  - GET /?status=pending_approval → gefilterd
  - POST /start → 200 met bundle
"""

import json
import unittest.mock as mock
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.analytics as analytics_module
import backend.api.apps as apps_module
from backend.api import analytics as analytics_router
from backend.api import approvals as approvals_router
from backend.api import apps as apps_router
from backend.api import campaigns as campaigns_router
from backend.models.campaign import (
    ApprovalDecision,
    CampaignBundle,
    CampaignStatus,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_bundle(status: CampaignStatus = CampaignStatus.PENDING_APPROVAL) -> CampaignBundle:
    b = CampaignBundle(app_id="app_test", status=status)
    return b


# ── Analytics router ───────────────────────────────────────────────────

@pytest.fixture()
def analytics_client(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics_module, "DATA_DIR", tmp_path)
    app = FastAPI()
    app.include_router(analytics_router.router)
    return TestClient(app), tmp_path


class TestAnalyticsAPI:
    def test_summary_geen_bestand_geeft_default(self, analytics_client):
        client, _ = analytics_client
        resp = client.get("/app_x/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["app_id"] == "app_x"
        assert "message" in data

    def test_summary_bestand_aanwezig_geladen(self, analytics_client):
        client, tmp_path = analytics_client
        summary = {"app_id": "app_x", "total_posts": 5, "posts": []}
        (tmp_path / "app_x_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        resp = client.get("/app_x/summary")
        assert resp.status_code == 200
        assert resp.json()["total_posts"] == 5

    def test_posts_geen_bestand_geeft_lege_lijst(self, analytics_client):
        client, _ = analytics_client
        resp = client.get("/app_x/posts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_posts_bestand_aanwezig(self, analytics_client):
        client, tmp_path = analytics_client
        posts = [{"post_id": f"p_{i}", "views": i * 100} for i in range(5)]
        (tmp_path / "app_x_posts.json").write_text(json.dumps(posts), encoding="utf-8")
        resp = client.get("/app_x/posts")
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_posts_limit_werkt(self, analytics_client):
        client, tmp_path = analytics_client
        posts = [{"post_id": f"p_{i}"} for i in range(10)]
        (tmp_path / "app_x_posts.json").write_text(json.dumps(posts), encoding="utf-8")
        resp = client.get("/app_x/posts?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3


# ── Apps router ────────────────────────────────────────────────────────

@pytest.fixture()
def apps_client(tmp_path, monkeypatch):
    monkeypatch.setattr(apps_module, "CONFIGS_DIR", tmp_path)
    registry = {"apps": [{"app_id": "app_demo", "name": "Demo App"}]}
    (tmp_path / "app_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    app = FastAPI()
    app.include_router(apps_router.router)
    return TestClient(app)


class TestAppsAPI:
    def test_lijst_apps(self, apps_client):
        resp = apps_client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["app_id"] == "app_demo"

    def test_get_brand_memory_gevonden(self, apps_client):
        with mock.patch("agents.brand_memory.load", return_value={"app_id": "app_demo", "tone": "energiek"}):
            resp = apps_client.get("/app_demo/brand-memory")
        assert resp.status_code == 200
        assert resp.json()["app_id"] == "app_demo"

    def test_get_brand_memory_niet_gevonden_404(self, apps_client):
        with mock.patch("agents.brand_memory.load", return_value={}):
            resp = apps_client.get("/app_leeg/brand-memory")
        assert resp.status_code == 404

    def test_patch_brand_memory_roept_apply_updates(self, apps_client):
        updated = {"app_id": "app_demo", "top_performing_hooks": ["nieuwe hook"]}
        with mock.patch("agents.brand_memory.apply_updates", return_value=updated) as mock_upd:
            resp = apps_client.patch(
                "/app_demo/brand-memory",
                json={"add_to_top_hooks": ["nieuwe hook"]},
            )
        assert resp.status_code == 200
        mock_upd.assert_called_once()


# ── Approvals router ───────────────────────────────────────────────────

@pytest.fixture()
def approvals_client():
    app = FastAPI()
    app.include_router(approvals_router.router)
    return TestClient(app)


class TestApprovalsAPI:
    def test_pending_geeft_lijst(self, approvals_client):
        bundles = [_make_bundle()]
        with mock.patch("backend.api.approvals.get_pending_campaigns", return_value=bundles):
            resp = approvals_client.get("/pending")
        assert resp.status_code == 200

    def test_decide_success_200(self, approvals_client):
        bundle = _make_bundle(status=CampaignStatus.APPROVED)
        bundle.approved_by = "user"
        bundle.approved_at = datetime.utcnow()
        with mock.patch("backend.api.approvals.process_approval", return_value=bundle):
            resp = approvals_client.post(
                "/decide",
                json={
                    "campaign_id": bundle.id,
                    "decision": ApprovalDecision.APPROVE,
                    "notes": "",
                },
            )
        assert resp.status_code == 200

    def test_decide_valueerror_400(self, approvals_client):
        with mock.patch("backend.api.approvals.process_approval", side_effect=ValueError("pending_approval vereist")):
            resp = approvals_client.post(
                "/decide",
                json={"campaign_id": "camp_x", "decision": "approve", "notes": ""},
            )
        assert resp.status_code == 400

    def test_decide_permissionerror_403(self, approvals_client):
        with mock.patch("backend.api.approvals.process_approval", side_effect=PermissionError("VEILIGHEIDSFOUT")):
            resp = approvals_client.post(
                "/decide",
                json={"campaign_id": "camp_x", "decision": "approve", "notes": ""},
            )
        assert resp.status_code == 403

    def test_decide_onverwachte_fout_500(self, approvals_client):
        with mock.patch("backend.api.approvals.process_approval", side_effect=RuntimeError("oeps")):
            resp = approvals_client.post(
                "/decide",
                json={"campaign_id": "camp_x", "decision": "approve", "notes": ""},
            )
        assert resp.status_code == 500


# ── Campaigns router ───────────────────────────────────────────────────

@pytest.fixture()
def campaigns_client(tmp_path, monkeypatch):
    import backend.repository.file_campaigns as fc_module
    monkeypatch.setattr(fc_module, "ROOT", tmp_path)
    (tmp_path / "data" / "campaigns").mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    app.include_router(campaigns_router.router)
    return TestClient(app), tmp_path


class TestCampaignsAPI:
    def test_pending_geeft_lijst(self, campaigns_client):
        client, tmp_path = campaigns_client
        bundle = _make_bundle(status=CampaignStatus.PENDING_APPROVAL)
        (tmp_path / "data" / "campaigns" / f"{bundle.id}.json").write_text(
            bundle.model_dump_json(), encoding="utf-8"
        )
        resp = client.get("/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_campaign_gevonden(self, campaigns_client):
        client, tmp_path = campaigns_client
        bundle = _make_bundle()
        (tmp_path / "data" / "campaigns" / f"{bundle.id}.json").write_text(
            bundle.model_dump_json(), encoding="utf-8"
        )
        resp = client.get(f"/{bundle.id}")
        assert resp.status_code == 200

    def test_get_campaign_niet_gevonden_404(self, campaigns_client):
        client, _ = campaigns_client
        resp = client.get("/bestaat_niet")
        assert resp.status_code == 404

    def test_list_alle_campagnes(self, campaigns_client):
        client, _ = campaigns_client
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_start_campaign_200(self, campaigns_client):
        client, _ = campaigns_client
        with mock.patch("workflows.campaign_pipeline.run_pipeline"):
            resp = client.post(
                "/start",
                json={"app_id": "app_test", "platform": "tiktok", "idea_index": 0},
            )
        assert resp.status_code == 200

    def test_start_campaign_retourneert_bundle_id(self, campaigns_client):
        client, _ = campaigns_client
        with mock.patch("workflows.campaign_pipeline.run_pipeline"):
            resp = client.post(
                "/start",
                json={"app_id": "app_test", "platform": "tiktok", "idea_index": 0},
            )
        assert resp.status_code == 200
        assert "id" in resp.json()
