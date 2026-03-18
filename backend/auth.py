"""
Authenticatie middleware — API key validatie per tenant.

Gebruik:
    from backend.auth import get_current_tenant

    @router.get("/")
    def list_items(tenant: TenantConfig = Depends(get_current_tenant)):
        ...

Configuratie:
    - AUTH_ENABLED env var (default: "true" in production, "false" in development)
    - API keys in configs/tenants/{tenant_id}.json → veld "api_key"
    - Header: X-API-Key: mk_acme_abc123...

Onbeschermde endpoints:
    - GET  /            (root)
    - GET  /health      (liveness)
    - GET  /api/health/* (readiness + component checks)
"""

from __future__ import annotations

import hmac
import os
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from loguru import logger

from backend.models.tenant import TenantConfig, get_tenant_registry

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Auth kan uitgeschakeld worden in development
_AUTH_ENABLED: bool | None = None


def _is_auth_enabled() -> bool:
    global _AUTH_ENABLED
    if _AUTH_ENABLED is None:
        env = os.getenv("ENVIRONMENT", "development")
        _AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true" if env == "production" else "false").lower() == "true"
        logger.info(f"[Auth] Authenticatie {'ingeschakeld' if _AUTH_ENABLED else 'uitgeschakeld'} (ENVIRONMENT={env})")
    return _AUTH_ENABLED


def _lookup_tenant_by_key(api_key: str) -> Optional[TenantConfig]:
    """Zoek tenant op basis van API key. Timing-safe vergelijking."""
    registry = get_tenant_registry()
    for config in registry.list_all():
        if config.api_key and hmac.compare_digest(config.api_key, api_key):
            return config
    return None


async def get_current_tenant(
    request: Request,
    api_key: Optional[str] = Depends(_api_key_header),
) -> TenantConfig:
    """
    FastAPI dependency: valideert API key en retourneert TenantConfig.

    Als auth uitgeschakeld is (development), wordt de "default" tenant teruggegeven
    tenzij een tenant_id query parameter meegegeven wordt.
    """
    if not _is_auth_enabled():
        # Development mode: gebruik query param tenant_id of default
        tenant_id = request.query_params.get("tenant_id", "default")
        return get_tenant_registry().get(tenant_id)

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key vereist. Stuur een X-API-Key header mee.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    tenant = _lookup_tenant_by_key(api_key)
    if tenant is None:
        logger.warning(f"[Auth] Ongeldige API key poging: {api_key[:8]}...")
        raise HTTPException(
            status_code=403,
            detail="Ongeldige API key.",
        )

    if not tenant.active:
        raise HTTPException(
            status_code=403,
            detail=f"Tenant '{tenant.tenant_id}' is gedeactiveerd.",
        )

    return tenant


def generate_api_key(tenant_id: str) -> str:
    """Genereer een nieuwe API key met tenant prefix."""
    random_part = secrets.token_urlsafe(32)
    return f"mk_{tenant_id}_{random_part}"
