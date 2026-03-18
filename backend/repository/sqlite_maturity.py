"""
SqliteMaturityRepository — SQLite implementatie van IMaturityRepository.

Ontwerp:
  - latest scorecard: INSERT OR REPLACE op (tenant_id, app_id) combinatie
  - history: append-only INSERT per snapshot
  - JSON blob voor volledige model trouw
"""

from __future__ import annotations

import json

from loguru import logger

from maturity.models import MaturityScorecard, MaturitySnapshot
from backend.repository.sqlite_db import get_connection, init_tables


class SqliteMaturityRepository:
    """SQLite-backed maturity repository met tenant isolatie."""

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._conn = get_connection(tenant_id)
        init_tables(self._conn)

    def save_scorecard(self, scorecard: MaturityScorecard) -> None:
        """Sla scorecard op als latest + append aan history."""
        if not scorecard.tenant_id or scorecard.tenant_id == "default":
            scorecard = scorecard.model_copy(update={"tenant_id": self._tenant_id})

        # Upsert latest — verwijder eerdere latest voor deze app
        self._conn.execute(
            "DELETE FROM maturity_scorecards WHERE app_id = ?",
            (scorecard.app_id,),
        )
        self._conn.execute(
            """INSERT INTO maturity_scorecards
               (scorecard_id, tenant_id, app_id, computed_at, score, status, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                scorecard.scorecard_id,
                self._tenant_id,
                scorecard.app_id,
                scorecard.computed_at.isoformat(),
                scorecard.maturity_score,
                scorecard.status.value,
                scorecard.model_dump_json(),
            ),
        )

        # Append snapshot aan history
        snapshot = MaturitySnapshot(
            app_id=scorecard.app_id,
            tenant_id=self._tenant_id,
            maturity_score=scorecard.maturity_score,
            status=scorecard.status,
            scorecard=scorecard,
        )
        self._conn.execute(
            """INSERT INTO maturity_history
               (snapshot_id, tenant_id, app_id, saved_at, score, status, data_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.snapshot_id,
                self._tenant_id,
                snapshot.app_id,
                snapshot.saved_at.isoformat(),
                snapshot.maturity_score,
                snapshot.status.value,
                snapshot.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get_latest(
        self,
        app_id: str,
        tenant_id: str = "default",
    ) -> MaturityScorecard | None:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        row = conn.execute(
            "SELECT data_json FROM maturity_scorecards WHERE app_id = ?",
            (app_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return MaturityScorecard(**json.loads(row["data_json"]))
        except Exception as exc:
            logger.warning(f"[SqliteMaturityRepo] Parse fout ({app_id}): {exc}")
            return None

    def get_history(
        self,
        app_id: str,
        tenant_id: str = "default",
        limit: int = 50,
    ) -> list[MaturitySnapshot]:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        rows = conn.execute(
            """SELECT data_json FROM maturity_history
               WHERE app_id = ?
               ORDER BY saved_at DESC
               LIMIT ?""",
            (app_id, limit),
        ).fetchall()
        result: list[MaturitySnapshot] = []
        for row in rows:
            try:
                result.append(MaturitySnapshot(**json.loads(row["data_json"])))
            except Exception as exc:
                logger.warning(f"[SqliteMaturityRepo] Snapshot parse fout: {exc}")
        return result
