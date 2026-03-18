"""
FileMaturityRepository — file-based implementatie van IMaturityRepository.

Datalocatie:
  default tenant : data/maturity/{app_id}/
  overige tenants: data/tenants/{tenant_id}/maturity/{app_id}/

Bestanden per app:
  latest.json   — meest recente scorecard (atomisch overschreven)
  history.jsonl — append-only historiek

Swappen naar DB:
  Vervang door PgMaturityRepository en update factory in backend/api/maturity.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from maturity.models import MaturityScorecard, MaturitySnapshot
from utils.file_io import atomic_write_text

ROOT = Path(__file__).parent.parent.parent  # content-automation/


def _maturity_dir(tenant_id: str) -> Path:
    """
    'default' → data/maturity/                   (backward compat)
    overig    → data/tenants/{tenant_id}/maturity/
    """
    if tenant_id == "default":
        return ROOT / "data" / "maturity"
    return ROOT / "data" / "tenants" / tenant_id / "maturity"


class FileMaturityRepository:
    """
    File-based opslag voor MaturityScorecards, tenant + app geïsoleerd.

    Gebruik:
        repo = FileMaturityRepository(tenant_id="acme")
        repo.save_scorecard(scorecard)
        latest = repo.get_latest("app_tiktok")
    """

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id  = tenant_id
        self._maturity_dir = _maturity_dir(tenant_id)

    # ── Schrijven ──────────────────────────────────────────────────────

    def save_scorecard(self, scorecard: MaturityScorecard) -> None:
        """
        Sla scorecard op als latest (atomisch) + append snapshot aan history.jsonl.
        Stelt tenant_id in op scorecard als nog niet ingevuld.
        """
        if not scorecard.tenant_id or scorecard.tenant_id == "default":
            scorecard = scorecard.model_copy(update={"tenant_id": self._tenant_id})

        app_dir = self._maturity_dir / scorecard.app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        # latest.json — atomisch
        atomic_write_text(
            app_dir / "latest.json",
            scorecard.model_dump_json(indent=2),
        )

        # history.jsonl — append (veilig voor concurrente readers)
        snapshot = MaturitySnapshot(
            app_id         = scorecard.app_id,
            maturity_score = scorecard.maturity_score,
            status         = scorecard.status,
            scorecard      = scorecard,
        )
        with open(app_dir / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write(snapshot.model_dump_json() + "\n")

    # ── Lezen ──────────────────────────────────────────────────────────

    def get_latest(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> MaturityScorecard | None:
        path = _maturity_dir(tenant_id) / app_id / "latest.json"
        if not path.exists():
            return None
        try:
            return MaturityScorecard(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning(f"[FileMaturityRepo] Kan latest.json niet laden ({app_id}@{tenant_id}): {exc}")
            return None

    def get_history(
        self,
        app_id: str,
        tenant_id: str = "default",
        limit: int = 50,
    ) -> list[MaturitySnapshot]:
        path = _maturity_dir(tenant_id) / app_id / "history.jsonl"
        if not path.exists():
            return []

        snapshots: list[MaturitySnapshot] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    snapshots.append(MaturitySnapshot(**json.loads(line)))
                except Exception as exc:
                    logger.warning(f"[FileMaturityRepo] Snapshot parse fout ({app_id}): {exc}")

        snapshots.sort(key=lambda s: s.saved_at, reverse=True)
        return snapshots[:limit]
