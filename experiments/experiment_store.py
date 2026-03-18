"""
Experiment Store — JSON-persistentie voor experimenten.

Opslaglocatie:
  default tenant : data/experiments/{experiment_id}.json
  overige tenants: data/tenants/{tenant_id}/experiments/{experiment_id}.json

Index per tenant: {store_dir}/_index.json  (app_id → [experiment_ids])

Backward compat: ExperimentStore() (geen args) werkt identiek als voor tenant-support.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from experiments.models import Experiment, ExperimentDimension, ExperimentStatus
from utils.file_io import atomic_write_json, atomic_write_text

ROOT = Path(__file__).parent.parent


def _resolve_store_dir(root: Path, tenant_id: str) -> Path:
    """
    'default' → data/experiments/        (backward compat — bestaande data onaangetast)
    overig    → data/tenants/{tenant_id}/experiments/
    """
    if tenant_id == "default":
        return root / "data" / "experiments"
    return root / "data" / "tenants" / tenant_id / "experiments"


# Module-level constanten voor code die nog niet tenant-aware is
STORE_DIR  = _resolve_store_dir(ROOT, "default")
INDEX_PATH = STORE_DIR / "_index.json"


class ExperimentStore:
    """
    Lees/schrijf experimenten naar JSON-bestanden op schijf.

    Elk experiment wordt opgeslagen als een apart JSON-bestand.
    Een index-bestand (_index.json) houdt app_id → [experiment_ids] bij
    voor efficiënte list-queries zonder alle bestanden te lezen.

    Args:
        tenant_id: Tenant isolatie. "default" gebruikt bestaande data/-paden.
    """

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id
        self._store_dir = _resolve_store_dir(ROOT, tenant_id)
        self._index_path = self._store_dir / "_index.json"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    # ── Schrijven ─────────────────────────────────────────────────────

    def save(self, experiment: Experiment) -> Path:
        """Sla een experiment op (aanmaken of overschrijven)."""
        # Koppel experiment_id aan varianten als dat nog niet gedaan is
        for variant in experiment.variants:
            if not variant.experiment_id:
                variant.experiment_id = experiment.experiment_id

        path = self._store_dir / f"{experiment.experiment_id}.json"
        atomic_write_text(path, experiment.model_dump_json(indent=2))
        self._update_index(experiment)
        return path

    # ── Lezen ─────────────────────────────────────────────────────────

    def load(self, experiment_id: str) -> Optional[Experiment]:
        """Laad een experiment op ID. Geeft None als niet gevonden."""
        path = self._store_dir / f"{experiment_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Experiment(**data)
        except Exception as exc:
            logger.warning(f"[ExperimentStore] Kan {experiment_id} niet laden: {exc}")
            return None

    def list_by_app(self, app_id: str) -> list[Experiment]:
        """
        Geef alle experimenten voor een app_id, gesorteerd op created_at (nieuwste eerst).
        Gebruikt de index voor efficiëntie.
        """
        index = self._load_index()
        ids = index.get(app_id, [])
        experiments = []
        for exp_id in ids:
            exp = self.load(exp_id)
            if exp is not None:
                experiments.append(exp)
        return sorted(experiments, key=lambda e: e.created_at, reverse=True)

    def list_all(self) -> list[Experiment]:
        """Laad alle experimenten uit de store (voor scheduler/comparator)."""
        experiments = []
        for path in self._store_dir.glob("exp_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                experiments.append(Experiment(**data))
            except Exception as exc:
                logger.warning(f"[ExperimentStore] Overgeslagen ({path.name}): {exc}")
                continue
        return sorted(experiments, key=lambda e: e.created_at, reverse=True)

    def get_by_campaign(self, campaign_id: str) -> Optional[Experiment]:
        """Zoek het experiment dat hoort bij een specifieke campagne."""
        for path in self._store_dir.glob("exp_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("campaign_id") == campaign_id:
                    return Experiment(**data)
            except Exception as exc:
                logger.warning(f"[ExperimentStore] Overgeslagen bij campagne-lookup ({path.name}): {exc}")
                continue
        return None

    # ── Analyse queries ───────────────────────────────────────────────

    def get_concluded_dimensions(self, app_id: str) -> dict[str, int]:
        """
        Geef per dimensie het aantal geconcludeerde experimenten voor een app.

        Retourneert bijv: {"hook_type": 3, "cta_type": 1}
        Gebruikt door VariantGenerator om te bepalen welke dimensie het minst getest is.
        """
        counts: dict[str, int] = defaultdict(int)
        for exp in self.list_by_app(app_id):
            if exp.status == ExperimentStatus.CONCLUDED:
                counts[exp.hypothesis.dimension.value] += 1
        return dict(counts)

    def get_winning_values(self, app_id: str, dimension: ExperimentDimension) -> list[str]:
        """
        Geef de winnende dimensie-waarden voor alle geconcludeerde experimenten
        op een specifieke dimensie, meest recent eerst.
        """
        winners = []
        for exp in self.list_by_app(app_id):
            if (
                exp.status == ExperimentStatus.CONCLUDED
                and exp.hypothesis.dimension == dimension
                and exp.winning_variant_id
            ):
                winner = exp.get_variant(exp.winning_variant_id)
                if winner:
                    winners.append(winner.spec.dimension_value)
        return winners

    def get_pending_experiments(self, app_id: str) -> list[Experiment]:
        """Experimenten die wachten op operator goedkeuring."""
        return [
            e for e in self.list_by_app(app_id)
            if e.status in (ExperimentStatus.PENDING, ExperimentStatus.QUALITY_FAIL)
        ]

    def get_measuring_experiments(self) -> list[Experiment]:
        """Alle experimenten die momenteel views verzamelen (voor comparator)."""
        return [
            e for e in self.list_all()
            if e.status in (ExperimentStatus.MEASURING, ExperimentStatus.PUBLISHED)
        ]

    # ── Index beheer ──────────────────────────────────────────────────

    def _load_index(self) -> dict[str, list[str]]:
        if not self._index_path.exists():
            return {}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[ExperimentStore] Index corrupt of onleesbaar ({self._tenant_id}): {exc} — leeg index gebruikt")
            return {}

    def _update_index(self, experiment: Experiment) -> None:
        index = self._load_index()
        app_id = experiment.app_id
        exp_id = experiment.experiment_id

        if app_id not in index:
            index[app_id] = []
        if exp_id not in index[app_id]:
            index[app_id].append(exp_id)

        atomic_write_json(self._index_path, index)
