"""
Unit tests: observability/audit_store.py — write-once audit trail.

Dekt:
  - AuditStore.write(): schrijf audit entry naar JSONL
  - AuditStore.read(): lees entries terug
  - AuditStore.read_recent(): filteren op job_type en outcome
  - AuditStore.write_from_event(): OperationalEvent → AuditEntry
  - AuditStore.stream(): generator over alle entries
  - AuditStore.get_failure_rate(): failure rate berekening
  - Tenant isolatie: gescheiden paden per tenant
  - Lege/niet-bestaande data → lege resultaten
"""

from datetime import datetime

import pytest

import observability.audit_store as store_module
from observability.audit_store import AuditStore
from observability.models import (
    AuditEntry,
    JobOutcome,
    JobType,
    OperationalEvent,
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Isoleer AuditStore naar tmp_path."""
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    monkeypatch.setattr(store_module, "AUDIT_DIR", tmp_path / "data" / "audit")
    (tmp_path / "data" / "audit").mkdir(parents=True, exist_ok=True)
    # Clear singleton cache
    store_module._stores.clear()
    return tmp_path


def _make_entry(
    app_id: str = "app_test",
    job_type: JobType = JobType.PUBLISH,
    outcome: JobOutcome = JobOutcome.SUCCESS,
    timestamp: datetime | None = None,
) -> AuditEntry:
    return AuditEntry(
        event_id="evt_001",
        correlation_id="corr_001",
        timestamp=timestamp or datetime(2026, 3, 10, 12, 0),
        job_type=job_type,
        job_name="test_job",
        app_id=app_id,
        outcome=outcome,
    )


class TestWrite:
    def test_schrijf_en_lees_entry(self):
        store = AuditStore()
        entry = _make_entry()
        store.write(entry)
        entries = store.read(app_id="app_test", year_month="2026-03")
        assert len(entries) == 1
        assert entries[0].event_id == "evt_001"

    def test_meerdere_entries_append(self):
        store = AuditStore()
        store.write(_make_entry())
        store.write(_make_entry(outcome=JobOutcome.FAILURE))
        entries = store.read(app_id="app_test", year_month="2026-03")
        assert len(entries) == 2

    def test_system_app_id_none(self):
        store = AuditStore()
        entry = _make_entry(app_id=None)
        store.write(entry)
        entries = store.read(app_id=None, year_month="2026-03")
        assert len(entries) == 1


class TestReadRecent:
    def test_filter_op_job_type(self):
        store = AuditStore()
        store.write(_make_entry(job_type=JobType.PUBLISH))
        store.write(_make_entry(job_type=JobType.AI_AGENT_CALL))
        recent = store.read_recent(app_id="app_test", job_type=JobType.PUBLISH)
        assert len(recent) == 1
        assert recent[0].job_type == JobType.PUBLISH

    def test_filter_op_outcome(self):
        store = AuditStore()
        store.write(_make_entry(outcome=JobOutcome.SUCCESS))
        store.write(_make_entry(outcome=JobOutcome.FAILURE))
        recent = store.read_recent(app_id="app_test", outcome=JobOutcome.FAILURE)
        assert len(recent) == 1
        assert recent[0].outcome == JobOutcome.FAILURE

    def test_limit_werkt(self):
        store = AuditStore()
        for _ in range(10):
            store.write(_make_entry())
        recent = store.read_recent(app_id="app_test", limit=3)
        assert len(recent) == 3

    def test_leeg_bij_geen_data(self):
        store = AuditStore()
        assert store.read_recent(app_id="onbekend") == []


class TestWriteFromEvent:
    def test_event_naar_entry(self):
        store = AuditStore()
        event = OperationalEvent(
            job_type=JobType.CAMPAIGN_PIPELINE,
            job_name="run_pipeline",
            app_id="app_test",
            outcome=JobOutcome.SUCCESS,
            ended_at=datetime(2026, 3, 10, 12, 5),
        )
        entry = store.write_from_event(event, actor="test")
        assert entry.job_name == "run_pipeline"
        assert entry.actor == "test"
        # Entry is ook op schijf geschreven
        entries = store.read(app_id="app_test", year_month="2026-03")
        assert len(entries) == 1


class TestStream:
    def test_stream_yieldt_entries(self):
        store = AuditStore()
        store.write(_make_entry())
        store.write(_make_entry())
        items = list(store.stream(app_id="app_test"))
        assert len(items) == 2

    def test_stream_leeg_bij_geen_data(self):
        store = AuditStore()
        items = list(store.stream(app_id="niet_bestaand"))
        assert items == []


class TestGetFailureRate:
    def test_nul_bij_alleen_success(self):
        store = AuditStore()
        store.write(_make_entry(outcome=JobOutcome.SUCCESS, timestamp=datetime.utcnow()))
        assert store.get_failure_rate(app_id="app_test") == 0.0

    def test_nul_bij_geen_entries(self):
        store = AuditStore()
        assert store.get_failure_rate(app_id="app_test") == 0.0


class TestTenantIsolatie:
    def test_gescheiden_data_per_tenant(self):
        store_a = AuditStore(tenant_id="tenant_a")
        store_b = AuditStore(tenant_id="tenant_b")

        store_a.write(_make_entry(app_id="app_1"))
        store_b.write(_make_entry(app_id="app_1"))
        store_b.write(_make_entry(app_id="app_1"))

        assert len(store_a.read(app_id="app_1", year_month="2026-03")) == 1
        assert len(store_b.read(app_id="app_1", year_month="2026-03")) == 2

    def test_default_tenant_backward_compat(self):
        store = AuditStore(tenant_id="default")
        store.write(_make_entry())
        entries = store.read(app_id="app_test", year_month="2026-03")
        assert len(entries) == 1
