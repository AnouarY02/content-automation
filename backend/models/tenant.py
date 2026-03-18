"""
TenantConfig — per-tenant configuratie en drempelwaarden.

Elke tenant heeft:
  - Een eigen lijst van toegestane app_ids
  - Optionele drempelwaarden die de globale defaults overschrijven
  - Isolated dataopslag onder data/tenants/{tenant_id}/

De "default" tenant gebruikt de bestaande data/-paden voor backward compat.

Opslaglocatie: configs/tenants/{tenant_id}.json

Gebruik:
    registry = get_tenant_registry()
    config   = registry.get("tenant_abc")
    resolved = config.resolve_thresholds()
    # resolved.iv_composite → 75.0 (of tenant-override)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ── Drempelwaarden ────────────────────────────────────────────────────

class TenantThresholds(BaseModel):
    """
    Per-tenant override van MaturityThresholds en kwaliteitsdrempels.
    Velden die None zijn vallen terug op de globale defaults.

    Gebruik: early-stage tenants kunnen lagere drempels krijgen.
    """
    # INTERN_VOLWASSEN drempels
    iv_composite:            Optional[float] = None   # default: 75.0
    iv_replication:          Optional[float] = None   # default: 70.0
    iv_stability:            Optional[float] = None   # default: 90.0
    iv_adoption:             Optional[float] = None   # default: 70.0

    # VALIDATED drempels
    v_composite:             Optional[float] = None   # default: 50.0
    v_replication:           Optional[float] = None   # default: 40.0

    # Quality scorer drempels
    quality_block:           Optional[float] = None   # default: 40.0
    quality_warn:            Optional[float] = None   # default: 65.0
    quality_composite_block: Optional[float] = None   # default: 55.0

    def resolve(self) -> "ResolvedThresholds":
        """Combineer met globale defaults tot volledig ingevuld object."""
        from maturity.models import MaturityThresholds
        return ResolvedThresholds(
            iv_composite            = self.iv_composite   or MaturityThresholds.INTERN_VOLWASSEN_COMPOSITE,
            iv_replication          = self.iv_replication or MaturityThresholds.INTERN_VOLWASSEN_REPLICATION,
            iv_stability            = self.iv_stability   or MaturityThresholds.INTERN_VOLWASSEN_STABILITY,
            iv_adoption             = self.iv_adoption    or MaturityThresholds.INTERN_VOLWASSEN_ADOPTION,
            v_composite             = self.v_composite    or MaturityThresholds.VALIDATED_COMPOSITE,
            v_replication           = self.v_replication  or MaturityThresholds.VALIDATED_REPLICATION,
            quality_block           = self.quality_block           or 40.0,
            quality_warn            = self.quality_warn            or 65.0,
            quality_composite_block = self.quality_composite_block or 55.0,
        )


class ResolvedThresholds(BaseModel):
    """Volledig ingevulde drempelwaarden na merge met globale defaults."""
    iv_composite:            float
    iv_replication:          float
    iv_stability:            float
    iv_adoption:             float
    v_composite:             float
    v_replication:           float
    quality_block:           float
    quality_warn:            float
    quality_composite_block: float


# ── Tenant configuratie ────────────────────────────────────────────────

class TenantConfig(BaseModel):
    """
    Configuratie voor één tenant.

    Opslaglocatie: configs/tenants/{tenant_id}.json

    Voorbeeld JSON:
    {
      "tenant_id": "acme_corp",
      "name": "ACME Corp",
      "api_key": "mk_acme_abc123...",
      "app_ids": ["app_acme_tiktok", "app_acme_ig"],
      "thresholds": {
        "iv_composite": 70.0
      },
      "active": true,
      "created_at": "2026-03-10"
    }
    """
    tenant_id:  str
    name:       str
    api_key:    str = ""          # API key voor authenticatie (prefix: mk_)
    app_ids:    list[str] = []    # Lege lijst = alle app_ids toegestaan
    thresholds: TenantThresholds = Field(default_factory=TenantThresholds)
    active:     bool = True
    created_at: str = ""

    def resolve_thresholds(self) -> ResolvedThresholds:
        return self.thresholds.resolve()

    def allows_app(self, app_id: str) -> bool:
        """True als tenant toegang heeft tot app_id (lege lijst = alles toegestaan)."""
        return not self.app_ids or app_id in self.app_ids


# ── Singleton builtin tenant ───────────────────────────────────────────

_DEFAULT_TENANT = TenantConfig(
    tenant_id  = "default",
    name       = "Default Tenant",
    app_ids    = [],   # Alle apps toegestaan
    active     = True,
)


# ── Registry ──────────────────────────────────────────────────────────

class TenantRegistry:
    """
    Laadt en cacht TenantConfig-objecten uit configs/tenants/.

    Gebruik:
        registry = get_tenant_registry()
        config   = registry.get("acme_corp")
    """

    _CONFIGS_SUBDIR = "tenants"

    def __init__(self, root: Path | None = None) -> None:
        self._root        = root or Path(__file__).parent.parent.parent
        self._configs_dir = self._root / "configs" / self._CONFIGS_SUBDIR
        self._cache: dict[str, TenantConfig] = {}

    def get(self, tenant_id: str) -> TenantConfig:
        """
        Laad tenant config op ID.
        - Geeft _DEFAULT_TENANT voor tenant_id="default"
        - Geeft TenantConfig met defaults voor onbekende tenant (+ log warning)
        """
        if tenant_id == "default":
            return _DEFAULT_TENANT

        if tenant_id in self._cache:
            return self._cache[tenant_id]

        path = self._configs_dir / f"{tenant_id}.json"
        if not path.exists():
            from loguru import logger
            logger.warning(
                f"[TenantRegistry] Onbekende tenant_id={tenant_id!r} — "
                "defaults gebruikt. Maak configs/tenants/{tenant_id}.json aan."
            )
            return TenantConfig(tenant_id=tenant_id, name=tenant_id)

        try:
            config = TenantConfig(**json.loads(path.read_text(encoding="utf-8")))
            self._cache[tenant_id] = config
            return config
        except Exception as exc:
            from loguru import logger
            logger.error(f"[TenantRegistry] Kan {path.name} niet parsen: {exc}")
            return TenantConfig(tenant_id=tenant_id, name=tenant_id)

    def list_all(self) -> list[TenantConfig]:
        """Alle geconfigureerde tenants (inclusief default)."""
        if not self._configs_dir.exists():
            return [_DEFAULT_TENANT]

        configs: list[TenantConfig] = []
        for path in sorted(self._configs_dir.glob("*.json")):
            try:
                configs.append(TenantConfig(**json.loads(path.read_text(encoding="utf-8"))))
            except Exception as exc:
                from loguru import logger
                logger.warning(f"[TenantRegistry] Overgeslagen ({path.name}): {exc}")

        return configs or [_DEFAULT_TENANT]

    def save(self, config: TenantConfig) -> None:
        """Persist een tenant config naar schijf."""
        from utils.file_io import atomic_write_json
        self._configs_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._configs_dir / f"{config.tenant_id}.json",
            config.model_dump(),
        )
        self._cache[config.tenant_id] = config

    def invalidate(self, tenant_id: str) -> None:
        """Verwijder cache-entry (bijv. na config-update)."""
        self._cache.pop(tenant_id, None)


# ── Module-level singleton ─────────────────────────────────────────────

_registry: TenantRegistry | None = None


def get_tenant_registry() -> TenantRegistry:
    global _registry
    if _registry is None:
        _registry = TenantRegistry()
    return _registry
