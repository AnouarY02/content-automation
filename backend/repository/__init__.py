"""
Repository layer — database-ready storage abstractions.

Structuur:
  base.py               — Protocol interfaces (IExperimentRepository, etc.)
  file_experiments.py   — File-based implementatie
  file_campaigns.py     — File-based implementatie
  file_maturity.py      — File-based implementatie
  sqlite_experiments.py — SQLite implementatie
  sqlite_campaigns.py   — SQLite implementatie
  sqlite_maturity.py    — SQLite implementatie
  factory.py            — Factory functies (REPO_BACKEND=file|sqlite)

Switchover:
  Zet REPO_BACKEND=sqlite in .env om van file → SQLite te wisselen.
"""

from backend.repository.base import (
    ICampaignRepository,
    IExperimentRepository,
    IMaturityRepository,
)
from backend.repository.file_campaigns import FileCampaignRepository
from backend.repository.file_experiments import FileExperimentRepository
from backend.repository.file_maturity import FileMaturityRepository
from backend.repository.factory import (
    get_campaign_repo,
    get_experiment_repo,
    get_maturity_repo,
)

__all__ = [
    "ICampaignRepository",
    "IExperimentRepository",
    "IMaturityRepository",
    "FileCampaignRepository",
    "FileExperimentRepository",
    "FileMaturityRepository",
    "get_campaign_repo",
    "get_experiment_repo",
    "get_maturity_repo",
]
