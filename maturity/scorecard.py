"""
ScorecardBuilder — orkestreert MetricsEngine + Evaluator en beheert persistentie.

Gebruik:
    builder   = ScorecardBuilder()
    scorecard = builder.compute("app_demo")     # berekent + slaat op
    latest    = builder.load_latest("app_demo") # laadt bestaande scorecard
    history   = builder.load_history("app_demo") # lijst van snapshots

Opslag:
    data/maturity/{app_id}/latest.json
    data/maturity/{app_id}/history.jsonl
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from maturity.evaluator import Evaluator
from maturity.metrics_engine import MetricsEngine
from maturity.models import MaturityScorecard, MaturitySnapshot
from utils.file_io import atomic_write_text

ROOT        = Path(__file__).parent.parent
MATURITY_DIR = ROOT / "data" / "maturity"


class ScorecardBuilder:
    """
    Orkestreert de volledige maturity-berekening en persistentie.
    Stateloze klasse — kan meerdere malen worden hergebruikt.
    """

    # ── Berekening ────────────────────────────────────────────────────

    def compute(self, app_id: str, tenant_id: str = "default") -> MaturityScorecard:
        """
        Bereken een verse MaturityScorecard voor app_id.
        Persistentie is de verantwoordelijkheid van de aanroeper (via repository).

        Returns:
            MaturityScorecard — volledig gevulde scorecard met tenant_id ingesteld
        """
        logger.info(f"[Maturity] Start berekening voor {app_id} (tenant={tenant_id})")
        engine = MetricsEngine(app_id, tenant_id=tenant_id)

        replication = engine.replication_score()
        prediction  = engine.prediction_accuracy()
        delta       = engine.learning_delta()
        adoption    = engine.operator_adoption()
        stability   = engine.stability_index()

        scorecard = Evaluator.build(
            app_id=app_id,
            replication=replication,
            prediction=prediction,
            delta=delta,
            adoption=adoption,
            stability=stability,
        )
        scorecard = scorecard.model_copy(update={"tenant_id": tenant_id})

        logger.info(
            f"[Maturity] {app_id} (tenant={tenant_id}) → "
            f"score={scorecard.maturity_score:.1f} status={scorecard.status.value}"
        )
        return scorecard

    # ── Laden ─────────────────────────────────────────────────────────

    def load_latest(self, app_id: str) -> Optional[MaturityScorecard]:
        """
        Laad de meest recente opgeslagen scorecard.
        Geeft None als er nog geen scorecard bestaat.
        """
        path = self._latest_path(app_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MaturityScorecard(**data)
        except Exception as exc:
            logger.warning(f"[Maturity] Kan latest.json niet laden voor {app_id}: {exc}")
            return None

    def load_history(self, app_id: str, limit: int = 50) -> list[MaturitySnapshot]:
        """
        Laad historische snapshots (nieuwste eerst).

        Args:
            app_id: app identifier
            limit:  maximaal aantal snapshots (default: 50)
        """
        path = self._history_path(app_id)
        if not path.exists():
            return []

        snapshots: list[MaturitySnapshot] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    snapshots.append(MaturitySnapshot(**data))
                except Exception as exc:
                    logger.warning(f"[Maturity] Snapshot parse fout: {exc}")

        # Nieuwste eerst, gecapped op limit
        snapshots.sort(key=lambda s: s.saved_at, reverse=True)
        return snapshots[:limit]

    # ── Persistentie ──────────────────────────────────────────────────

    def _save(self, scorecard: MaturityScorecard) -> None:
        """Sla scorecard op als latest + append aan history."""
        app_dir = MATURITY_DIR / scorecard.app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        # Overschrijf latest — atomisch om corruptie bij crash te voorkomen
        atomic_write_text(
            self._latest_path(scorecard.app_id),
            scorecard.model_dump_json(indent=2),
        )

        # Append snapshot aan history
        snapshot = MaturitySnapshot(
            app_id=scorecard.app_id,
            maturity_score=scorecard.maturity_score,
            status=scorecard.status,
            scorecard=scorecard,
        )
        history_path = self._history_path(scorecard.app_id)
        line = snapshot.model_dump_json() + "\n"
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(line)

    def _latest_path(self, app_id: str) -> Path:
        return MATURITY_DIR / app_id / "latest.json"

    def _history_path(self, app_id: str) -> Path:
        return MATURITY_DIR / app_id / "history.jsonl"
