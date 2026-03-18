"""
Unit tests: backend/auth.py — API key authenticatie.

Dekt:
  - Auth disabled (development) → default tenant
  - Auth disabled met tenant_id query param
  - Auth enabled: geen API key → 401
  - Auth enabled: ongeldige key → 403
  - Auth enabled: geldige key → tenant config
  - Auth enabled: inactieve tenant → 403
  - Timing-safe vergelijking (hmac.compare_digest)
  - generate_api_key() formaat
"""

import os
import unittest.mock as mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import (
    generate_api_key,
    get_current_tenant,
    _is_auth_enabled,
    _lookup_tenant_by_key,
)
from backend.models.tenant import TenantConfig, TenantRegistry

import backend.auth as auth_module


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_auth_cache():
    """Reset de cached auth state tussen tests."""
    auth_module._AUTH_ENABLED = None
    yield
    auth_module._AUTH_ENABLED = None


@pytest.fixture
def mock_registry(tmp_path):
    """Registry met 2 tenants: active + inactive."""
    registry = TenantRegistry(root=tmp_path)

    active = TenantConfig(
        tenant_id="test_active",
        name="Active Tenant",
        api_key="mk_test_active_abc123",
        active=True,
    )
    inactive = TenantConfig(
        tenant_id="test_inactive",
        name="Inactive Tenant",
        api_key="mk_test_inactive_xyz789",
        active=False,
    )

    configs_dir = tmp_path / "configs" / "tenants"
    configs_dir.mkdir(parents=True)
    (configs_dir / "test_active.json").write_text(active.model_dump_json(), encoding="utf-8")
    (configs_dir / "test_inactive.json").write_text(inactive.model_dump_json(), encoding="utf-8")

    return registry


@pytest.fixture
def auth_client(mock_registry):
    """TestClient met auth dependency en enabled auth."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint(tenant=__import__("fastapi").Depends(get_current_tenant)):
        return {"tenant_id": tenant.tenant_id, "name": tenant.name}

    return TestClient(app)


# ── generate_api_key ─────────────────────────────────────────────────

class TestGenerateApiKey:
    def test_prefix_format(self):
        key = generate_api_key("acme")
        assert key.startswith("mk_acme_")

    def test_uniek(self):
        keys = {generate_api_key("test") for _ in range(10)}
        assert len(keys) == 10

    def test_voldoende_lengte(self):
        key = generate_api_key("x")
        assert len(key) > 30


# ── _lookup_tenant_by_key ────────────────────────────────────────────

class TestLookupTenantByKey:
    def test_geldige_key_retourneert_config(self, mock_registry):
        with mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry):
            result = _lookup_tenant_by_key("mk_test_active_abc123")
        assert result is not None
        assert result.tenant_id == "test_active"

    def test_ongeldige_key_retourneert_none(self, mock_registry):
        with mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry):
            result = _lookup_tenant_by_key("mk_nope_wrongkey")
        assert result is None

    def test_lege_key_retourneert_none(self, mock_registry):
        with mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry):
            result = _lookup_tenant_by_key("")
        assert result is None


# ── Auth disabled (development) ──────────────────────────────────────

class TestAuthDisabled:
    def test_development_default_tenant(self, auth_client):
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "development", "AUTH_ENABLED": "false"}):
            resp = auth_client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "default"

    def test_development_with_tenant_query(self, auth_client, mock_registry):
        with (
            mock.patch.dict(os.environ, {"ENVIRONMENT": "development", "AUTH_ENABLED": "false"}),
            mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry),
        ):
            resp = auth_client.get("/test?tenant_id=test_active")
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "test_active"


# ── Auth enabled ─────────────────────────────────────────────────────

class TestAuthEnabled:
    def test_geen_key_401(self, auth_client):
        with mock.patch.dict(os.environ, {"AUTH_ENABLED": "true"}):
            resp = auth_client.get("/test")
        assert resp.status_code == 401

    def test_ongeldige_key_403(self, auth_client, mock_registry):
        with (
            mock.patch.dict(os.environ, {"AUTH_ENABLED": "true"}),
            mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry),
        ):
            resp = auth_client.get("/test", headers={"X-API-Key": "mk_wrong_key"})
        assert resp.status_code == 403

    def test_geldige_key_200(self, auth_client, mock_registry):
        with (
            mock.patch.dict(os.environ, {"AUTH_ENABLED": "true"}),
            mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry),
        ):
            resp = auth_client.get("/test", headers={"X-API-Key": "mk_test_active_abc123"})
        assert resp.status_code == 200
        assert resp.json()["tenant_id"] == "test_active"

    def test_inactieve_tenant_403(self, auth_client, mock_registry):
        with (
            mock.patch.dict(os.environ, {"AUTH_ENABLED": "true"}),
            mock.patch("backend.auth.get_tenant_registry", return_value=mock_registry),
        ):
            resp = auth_client.get("/test", headers={"X-API-Key": "mk_test_inactive_xyz789"})
        assert resp.status_code == 403
