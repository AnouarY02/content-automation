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


def _backend() -> str:
    return os.getenv("REPO_BACKEND", "file").lower()


def get_campaign_repo(tenant_id: str = "default"):
    """Factory voor ICampaignRepository."""
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


def get_maturity_repo(tenant_id: str = "default"):
    """Factory voor IMaturityRepository."""
    if _backend() == "sqlite":
        from backend.repository.sqlite_maturity import SqliteMaturityRepository
        return SqliteMaturityRepository(tenant_id=tenant_id)
    from backend.repository.file_maturity import FileMaturityRepository
    return FileMaturityRepository(tenant_id=tenant_id)
