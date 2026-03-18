"""
SqliteExperimentRepository — SQLite implementatie van IExperimentRepository.

Ontwerp:
  - Experiment model als JSON blob + geïndexeerde kolommen voor queries
  - Upsert via INSERT OR REPLACE
  - Geen index-bestand nodig — SQL queries doen het werk
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from loguru import logger

from experiments.models import Experiment, ExperimentStatus
from backend.repository.sqlite_db import get_connection, init_tables


class SqliteExperimentRepository:
    """SQLite-backed experiment repository met tenant isolatie."""

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._conn = get_connection(tenant_id)
        init_tables(self._conn)

    def save(self, experiment: Experiment) -> None:
        """Sla experiment op (upsert op experiment_id)."""
        if not experiment.tenant_id or experiment.tenant_id == "default":
            experiment = experiment.model_copy(update={"tenant_id": self._tenant_id})

        self._conn.execute(
            """INSERT OR REPLACE INTO experiments
               (experiment_id, tenant_id, app_id, campaign_id, status,
                created_at, dimension, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                experiment.experiment_id,
                self._tenant_id,
                experiment.app_id,
                experiment.campaign_id,
                experiment.status.value,
                experiment.created_at.isoformat(),
                experiment.hypothesis.dimension.value if experiment.hypothesis else None,
                experiment.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get(
        self,
        experiment_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        row = conn.execute(
            "SELECT data_json FROM experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return Experiment(**json.loads(row["data_json"]))
        except Exception as exc:
            logger.warning(f"[SqliteExperimentRepo] Parse fout ({experiment_id}): {exc}")
            return None

    def list_by_app(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        rows = conn.execute(
            "SELECT data_json FROM experiments WHERE app_id = ? ORDER BY created_at DESC",
            (app_id,),
        ).fetchall()
        result: list[Experiment] = []
        for row in rows:
            try:
                result.append(Experiment(**json.loads(row["data_json"])))
            except Exception as exc:
                logger.warning(f"[SqliteExperimentRepo] Overgeslagen: {exc}")
        return result

    def list_measuring(
        self,
        tenant_id: str = "default",
    ) -> list[Experiment]:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        rows = conn.execute(
            """SELECT data_json FROM experiments
               WHERE status IN (?, ?)
               ORDER BY created_at DESC""",
            (ExperimentStatus.MEASURING.value, ExperimentStatus.PUBLISHED.value),
        ).fetchall()
        result: list[Experiment] = []
        for row in rows:
            try:
                result.append(Experiment(**json.loads(row["data_json"])))
            except Exception as exc:
                logger.warning(f"[SqliteExperimentRepo] Overgeslagen: {exc}")
        return result

    def get_by_campaign(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> Experiment | None:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        row = conn.execute(
            "SELECT data_json FROM experiments WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return Experiment(**json.loads(row["data_json"]))
        except Exception as exc:
            logger.warning(f"[SqliteExperimentRepo] Parse fout (campaign={campaign_id}): {exc}")
            return None

    def get_concluded_dimensions(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> dict[str, int]:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        rows = conn.execute(
            """SELECT dimension, COUNT(*) as cnt
               FROM experiments
               WHERE app_id = ? AND status = ?
               GROUP BY dimension""",
            (app_id, ExperimentStatus.CONCLUDED.value),
        ).fetchall()
        return {row["dimension"]: row["cnt"] for row in rows if row["dimension"]}
