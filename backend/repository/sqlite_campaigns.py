"""
SqliteCampaignRepository — SQLite implementatie van ICampaignRepository.

Ontwerp:
  - Pydantic models opgeslagen als JSON blob in data_json kolom
  - Geïndexeerde kolommen (app_id, status, created_at) voor snelle queries
  - Upsert via INSERT OR REPLACE
  - Thread-safe door sqlite3 + WAL mode

Switchover:
  Vervang FileCampaignRepository door SqliteCampaignRepository in factory.
"""

from __future__ import annotations

import json
import sqlite3

from loguru import logger

from backend.models.campaign import CampaignBundle, CampaignStatus
from backend.repository.sqlite_db import get_connection, init_tables


class SqliteCampaignRepository:
    """SQLite-backed campaign repository met tenant isolatie."""

    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._conn = get_connection(tenant_id)
        init_tables(self._conn)

    def save(self, bundle: CampaignBundle) -> None:
        """Sla campagne op (upsert op bundle.id)."""
        if not bundle.tenant_id or bundle.tenant_id == "default":
            bundle = bundle.model_copy(update={"tenant_id": self._tenant_id})

        self._conn.execute(
            """INSERT OR REPLACE INTO campaigns
               (id, tenant_id, app_id, status, created_at, data_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                bundle.id,
                self._tenant_id,
                bundle.app_id,
                bundle.status if isinstance(bundle.status, str) else bundle.status.value,
                bundle.created_at.isoformat(),
                bundle.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> CampaignBundle | None:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        row = conn.execute(
            "SELECT data_json FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return CampaignBundle(**json.loads(row["data_json"]))
        except Exception as exc:
            logger.warning(f"[SqliteCampaignRepo] Parse fout ({campaign_id}): {exc}")
            return None

    def list(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
        status: str | None = None,
    ) -> list[CampaignBundle]:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        query = "SELECT data_json FROM campaigns WHERE 1=1"
        params: list = []

        if app_id:
            query += " AND app_id = ?"
            params.append(app_id)
        if status:
            query += " AND status = ?"
            params.append(status if isinstance(status, str) else status)

        query += " ORDER BY created_at DESC"

        bundles: list[CampaignBundle] = []
        for row in conn.execute(query, params):
            try:
                bundles.append(CampaignBundle(**json.loads(row["data_json"])))
            except Exception as exc:
                logger.warning(f"[SqliteCampaignRepo] Overgeslagen: {exc}")
        return bundles

    def list_pending(
        self,
        tenant_id: str = "default",
        app_id: str | None = None,
    ) -> list[CampaignBundle]:
        return self.list(
            tenant_id=tenant_id,
            app_id=app_id,
            status=CampaignStatus.PENDING_APPROVAL,
        )

    def delete(
        self,
        campaign_id: str,
        tenant_id: str = "default",
    ) -> bool:
        conn = get_connection(tenant_id) if tenant_id != self._tenant_id else self._conn
        cursor = conn.execute(
            "DELETE FROM campaigns WHERE id = ?",
            (campaign_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
