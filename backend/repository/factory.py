"""
Repository Factory — kies file-based of SQLite op basis van REPO_BACKEND.

Gebruik:
    from backend.repository.factory import get_campaign_repo, get_experiment_repo

    repo = get_campaign_repo(tenant_id="acme")
    repo.save(bundle)

Switchover:
    Zet REPO_BACKEND=sqlite in .env om over te schakelen.
    Default: file (backward compatible).
"""

from __future__ import annotations

import os
from functools import lru_cache
from backend.supabase import has_supabase_env
from utils.runtime_paths import is_vercel_runtime


def _backend() -> str:
    configured = os.getenv("REPO_BACKEND", "").lower().strip()
    if configured:
        return configured
    # Op Vercel is het filesystem read-only; altijd Supabase gebruiken
    if is_vercel_runtime() or has_supabase_env():
        return "supabase"
    return "file"


def get_campaign_repo(tenant_id: str = "default"):
    """Factory voor ICampaignRepository."""
    if _backend() == "supabase":
        from backend.repository.supabase_campaigns import SupabaseCampaignRepository
        return SupabaseCampaignRepository(tenant_id=tenant_id)
    if _backend() == "sqlite":
        from backend.repository.sqlite_campaigns import SqliteCampaignRepository
        return SqliteCampaignRepository(tenant_id=tenant_id)
    from backend.repository.file_campaigns import FileCampaignRepository
    return FileCampaignRepository(tenant_id=tenant_id)


def get_experiment_repo(tenant_id: str = "default"):
    """Factory voor IExperimentRepository."""
    if _backend() == "sqlite":
        from backend.repository.sqlite_experiments import SqliteExperimentRepository
        return SqliteExperimentRepository(tenant_id=tenant_id)
    from backend.repository.file_experiments import FileExperimentRepository
    return FileExperimentRepository(tenant_id=tenant_id)


def get_app_repo(tenant_id: str = "default"):
    """Factory voor app + brand memory opslag."""
    if _backend() == "supabase":
        from backend.repository.supabase_apps import SupabaseAppRepository
        return SupabaseAppRepository(tenant_id=tenant_id)
    from backend.repository.file_apps import FileAppRepository
    return FileAppRepository(tenant_id=tenant_id)


def get_maturity_repo(tenant_id: str = "default"):
    """Factory voor IMaturityRepository."""
    if _backend() == "sqlite":
        from backend.repository.sqlite_maturity import SqliteMaturityRepository
        return SqliteMaturityRepository(tenant_id=tenant_id)
    from backend.repository.file_maturity import FileMaturityRepository
    return FileMaturityRepository(tenant_id=tenant_id)
