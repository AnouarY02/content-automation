"""
FileExperimentRepository — file-based implementatie van IExperimentRepository.

Datalocatie:
  default tenant : data/experiments/
  overige tenants: data/tenants/{tenant_id}/experiments/

Index per tenant: {store_dir}/_index.json  (app_id → [experiment_ids])

Swappen naar DB:
  Vervang deze klasse door PgExperimentRepository(IExperimentRepository)
  en update de factory in backend/api/experiments.py.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from loguru import logger

from experiments.models import Experiment, ExperimentStatus
from utils.file_io import atomic_write_json, atomic_write_text

ROOT = Path(__file__).parent.parent.parent  # content-automation/


def _store_dir(tenant_id: str) -> Path:
    """
    Backward-compatible pad-resolutie.
    'default' → data/experiments/  (bestaande bestanden onaangetast)
    overig    → data/tenants/{tenant_id}/experiments/
    """
    if tenant_id == "default":
        return ROOT / "data" / "experiments"
    return ROOT / "data" / "tenants" / tenant_id / "experiments"


class FileExperimentRepository:
    """
    File-based opslag voor experimenten, volledig tenant-geïsoleerd.

    Gebruik:
        repo = FileExperimentRepository(tenant_id="acme")
        repo.save(experiment)
        exp = repo.get("exp_abc123")
    """

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._dir       = _store_dir(tenant_id)
        self._idx       = self._dir / "_index.json"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Schrijven ──────────────────────────────────────────────────────

    def save(self, experiment: Experiment) -> None:
        """Sla experiment op (upsert). Stelt tenant_id in als leeg."""
        if not experiment.tenant_id or experiment.tenant_id == "default":
            experiment = experiment.model_copy(update={"tenant_id": self._tenant_id})
        for v in experiment.variants:
            if not v.experiment_id:
                v.experiment_id = experiment.experiment_id

        path = self._dir / f"{experiment.experiment_id}.json"
        atomic_write_text(path, experiment.model_dump_json(indent=2))
        self._update_index(experiment)

    # ── Lezen ──────────────────────────────────────────────────────────

    def get(
        self,
        experiment_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        path = _store_dir(tenant_id) / f"{experiment_id}.json"
        if not path.exists():
            return None
        try:
            return Experiment(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.warning(f"[FileExperimentRepo] Kan {experiment_id} niet laden: {exc}")
            return None

    def list_by_app(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        repo = FileExperimentRepository(tenant_id)
        index = repo._load_index()
        ids = index.get(app_id, [])
        result: list[Experiment] = []
        for eid in ids:
            exp = repo.get(eid, tenant_id)
            if exp is not None:
                result.append(exp)
        return sorted(result, key=lambda e: e.created_at, reverse=True)

    def list_measuring(
        self,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        store_dir = _store_dir(tenant_id)
        if not store_dir.exists():
            return []
        result: list[Experiment] = []
        for path in store_dir.glob("exp_*.json"):
            try:
                exp = Experiment(**json.loads(path.read_text(encoding="utf-8")))
                if exp.status in (ExperimentStatus.MEASURING, ExperimentStatus.PUBLISHED):
                    result.append(exp)
            except Exception as exc:
                logger.warning(f"[FileExperimentRepo] Overgeslagen ({path.name}): {exc}")
        return sorted(result, key=lambda e: e.created_at, reverse=True)

    def get_by_campaign(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        store_dir = _store_dir(tenant_id)
        if not store_dir.exists():
            return None
        for path in store_dir.glob("exp_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("campaign_id") == campaign_id:
                    return Experiment(**data)
            except Exception as exc:
                logger.warning(f"[FileExperimentRepo] Overgeslagen bij campagne-lookup ({path.name}): {exc}")
        return None

    def get_concluded_dimensions(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for exp in self.list_by_app(app_id, tenant_id):
            if exp.status == ExperimentStatus.CONCLUDED:
                counts[exp.hypothesis.dimension.value] += 1
        return dict(counts)

    # ── Index beheer ───────────────────────────────────────────────────

    def _load_index(self) -> dict[str, list[str]]:
        if not self._idx.exists():
            return {}
        try:
            return json.loads(self._idx.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[FileExperimentRepo] Index corrupt ({self._tenant_id}): {exc}")
            return {}

    def _update_index(self, experiment: Experiment) -> None:
        index = self._load_index()
        aid, eid = experiment.app_id, experiment.experiment_id
        if aid not in index:
            index[aid] = []
        if eid not in index[aid]:
            index[aid].append(eid)
        atomic_write_json(self._idx, index)
