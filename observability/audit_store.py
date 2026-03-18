"""
Audit Store — onveranderbare audit trail per app en per maand

ONTWERP:
  - Write-once: AuditEntry records worden alleen toegevoegd, nooit gewijzigd
  - Opslagformaat: JSONL (line-delimited JSON) — efficiënt voor append
  - Locatie: data/audit/{app_id}/audit_{YYYY-MM}.jsonl
  - Systeem-brede events: data/audit/system/audit_{YYYY-MM}.jsonl

QUERYING:
  - Laad alle records voor een app + maand
  - Filter op job_type, outcome, date range
  - Geen database nodig voor MVP (bestanden zijn klein)

RETENTIE:
  - Audit logs: 1 jaar bewaren (compliance)
  - Automatisch geroteerd per maand

VOORBEELD JSONL BESTAND (elke regel is één JSON object):
  {"audit_id": "aud_a1b2", "timestamp": "...", "job_type": "publish", "outcome": "success", ...}
  {"audit_id": "aud_c3d4", "timestamp": "...", "job_type": "publish", "outcome": "failure", ...}
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

from loguru import logger

from observability.models import AuditEntry, JobOutcome, JobType, OperationalEvent

ROOT = Path(__file__).parent.parent
AUDIT_DIR = ROOT / "data" / "audit"
AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_audit_dir(tenant_id: str) -> Path:
    """
    'default' → data/audit/            (backward compat)
    overig    → data/tenants/{tenant_id}/audit/
    """
    if tenant_id == "default":
        return AUDIT_DIR
    return ROOT / "data" / "tenants" / tenant_id / "audit"


class AuditStore:
    """Write-once audit trail opslag — tenant-geïsoleerd."""

    def __init__(self, tenant_id: str = "default"):
        self._tenant_id = tenant_id
        self._audit_dir = _resolve_audit_dir(tenant_id)
        self._audit_dir.mkdir(parents=True, exist_ok=True)

    def write(self, entry: AuditEntry) -> None:
        """
        Schrijf één audit record naar schijf.
        Thread-safe door append-mode (file-system garantie).
        """
        path = self._get_path(entry.app_id, entry.timestamp)
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

        logger.info(
            f"[Audit] {entry.job_type} → {entry.outcome} | {entry.job_name} | "
            f"{entry.duration_sec:.1f}s" if entry.duration_sec else
            f"[Audit] {entry.job_type} → {entry.outcome} | {entry.job_name}",
            extra={"component": "audit_store", "app_id": entry.app_id, "is_audit": True},
        )

    def write_from_event(
        self,
        event: OperationalEvent,
        actor: str = "system",
        idempotency_key: str | None = None,
    ) -> AuditEntry:
        """
        Maak een AuditEntry van een OperationalEvent en sla het op.
        Convenience methode voor gebruik in decorators.
        """
        duration = None
        if event.started_at and event.ended_at:
            duration = (event.ended_at - event.started_at).total_seconds()

        entry = AuditEntry(
            event_id=event.event_id,
            correlation_id=event.correlation_id,
            timestamp=event.ended_at or datetime.utcnow(),
            job_type=event.job_type,
            job_name=event.job_name,
            app_id=event.app_id,
            campaign_id=event.campaign_id,
            outcome=event.outcome,
            attempt_number=event.attempt_number,
            duration_sec=duration,
            actor=actor,
            idempotency_key=idempotency_key,
            metadata=event.metadata,
            error_summary=event.error[:200] if event.error else None,
            cost_usd=event.cost_usd,
        )
        self.write(entry)
        return entry

    def read(
        self,
        app_id: str | None = None,
        year_month: str | None = None,    # "2026-03"
    ) -> list[AuditEntry]:
        """
        Laad audit records. Geeft leeg resultaat als geen data beschikbaar.
        """
        if year_month is None:
            year_month = datetime.utcnow().strftime("%Y-%m")

        path = self._get_path(app_id, datetime.strptime(year_month, "%Y-%m"))
        if not path.exists():
            return []

        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(AuditEntry(**json.loads(line)))
                except Exception as e:
                    logger.warning(f"[AuditStore] Kan regel niet parsen: {e}")
        return entries

    def read_recent(
        self,
        app_id: str | None = None,
        limit: int = 100,
        job_type: JobType | None = None,
        outcome: JobOutcome | None = None,
    ) -> list[AuditEntry]:
        """Haal de meest recente audit entries op met optionele filters."""
        all_entries = self.read(app_id)
        if job_type:
            all_entries = [e for e in all_entries if e.job_type == job_type]
        if outcome:
            all_entries = [e for e in all_entries if e.outcome == outcome]
        return sorted(all_entries, key=lambda e: e.timestamp, reverse=True)[:limit]

    def get_failure_rate(
        self,
        app_id: str | None = None,
        job_type: JobType | None = None,
        hours: int = 24,
    ) -> float:
        """Bereken failure-rate voor de laatste N uur (0.0 - 1.0)."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        entries = self.read_recent(app_id=app_id, limit=500, job_type=job_type)
        relevant = [e for e in entries if e.timestamp >= cutoff]
        if not relevant:
            return 0.0
        failures = [e for e in relevant if e.outcome in (JobOutcome.FAILURE, JobOutcome.DEAD_LETTERED)]
        return len(failures) / len(relevant)

    def _get_path(self, app_id: str | None, dt: datetime) -> Path:
        """Bepaal het pad voor een audit bestand — tenant-geïsoleerd."""
        subdir = self._audit_dir / (app_id or "system")
        ym = dt.strftime("%Y-%m")
        return subdir / f"audit_{ym}.jsonl"

    def stream(self, app_id: str | None = None) -> Iterator[AuditEntry]:
        """Generator die alle audit records yieldt — efficiënt voor grote bestanden."""
        subdir = self._audit_dir / (app_id or "system")
        if not subdir.exists():
            return
        for path in sorted(subdir.glob("audit_*.jsonl")):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield AuditEntry(**json.loads(line))
                        except Exception as exc:
                            logger.warning(f"[AuditStore] Kan audit-regel niet parsen in {path.name}: {exc}")


# Module-level singleton cache (per tenant_id)
_stores: dict[str, AuditStore] = {}


def get_audit_store(tenant_id: str = "default") -> AuditStore:
    if tenant_id not in _stores:
        _stores[tenant_id] = AuditStore(tenant_id=tenant_id)
    return _stores[tenant_id]
