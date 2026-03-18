"""
Unit tests: observability/models.py — Pydantic modellen voor reliability systeem.

Dekt:
  - OperationalEvent: aanmaken, defaults
  - JobStatus: update() progressie
  - HealthSnapshot: compute_overall()
  - RetryPolicy: standaard policies per job type
  - AuditEntry: write-once model
  - Enums: alle waarden aanwezig
"""

import pytest

from observability.models import (
    AuditEntry,
    ComponentHealth,
    ComponentName,
    DEFAULT_RETRY_POLICIES,
    HealthSnapshot,
    HealthStatus,
    JobOutcome,
    JobStatus,
    JobType,
    OperationalEvent,
    RetryPolicy,
    Severity,
)


class TestJobType:
    def test_alle_types_aanwezig(self):
        expected = {
            "campaign_pipeline", "approval", "publish", "analytics_fetch",
            "feedback_injection", "scheduler_job", "health_check",
            "video_generation", "ai_agent_call", "brand_memory_update",
        }
        assert {jt.value for jt in JobType} == expected


class TestOperationalEvent:
    def test_default_waarden(self):
        event = OperationalEvent(
            job_type=JobType.PUBLISH,
            job_name="test",
        )
        assert event.outcome == JobOutcome.IN_PROGRESS
        assert event.attempt_number == 1
        assert event.event_id.startswith("evt_")

    def test_cost_usd_optioneel(self):
        event = OperationalEvent(
            job_type=JobType.AI_AGENT_CALL,
            job_name="agent",
            cost_usd=0.05,
        )
        assert event.cost_usd == 0.05


class TestJobStatus:
    def test_update_progressie(self):
        status = JobStatus(
            job_type=JobType.CAMPAIGN_PIPELINE,
        )
        status.update("Stap 3/6", completed=3, total=6)
        assert status.current_step == "Stap 3/6"
        assert status.progress_pct == 50
        assert status.completed_steps == 3
        assert status.total_steps == 6

    def test_update_100_procent(self):
        status = JobStatus(job_type=JobType.CAMPAIGN_PIPELINE)
        status.update("Klaar", completed=6, total=6)
        assert status.progress_pct == 100


class TestHealthSnapshot:
    def test_compute_overall_healthy(self):
        snap = HealthSnapshot(
            components={
                "api": ComponentHealth(component=ComponentName.ANTHROPIC_API, status=HealthStatus.HEALTHY),
                "fs": ComponentHealth(component=ComponentName.FILESYSTEM, status=HealthStatus.HEALTHY),
            }
        )
        assert snap.compute_overall() == HealthStatus.HEALTHY

    def test_compute_overall_degraded(self):
        snap = HealthSnapshot(
            components={
                "api": ComponentHealth(component=ComponentName.ANTHROPIC_API, status=HealthStatus.HEALTHY),
                "pub": ComponentHealth(component=ComponentName.TIKTOK_PUBLISHER, status=HealthStatus.DEGRADED),
            }
        )
        assert snap.compute_overall() == HealthStatus.DEGRADED

    def test_compute_overall_unhealthy_wint(self):
        snap = HealthSnapshot(
            components={
                "api": ComponentHealth(component=ComponentName.ANTHROPIC_API, status=HealthStatus.DEGRADED),
                "pub": ComponentHealth(component=ComponentName.TIKTOK_PUBLISHER, status=HealthStatus.UNHEALTHY),
            }
        )
        assert snap.compute_overall() == HealthStatus.UNHEALTHY

    def test_compute_overall_unknown_bij_mix(self):
        snap = HealthSnapshot(
            components={
                "api": ComponentHealth(component=ComponentName.ANTHROPIC_API, status=HealthStatus.HEALTHY),
                "fs": ComponentHealth(component=ComponentName.FILESYSTEM, status=HealthStatus.UNKNOWN),
            }
        )
        assert snap.compute_overall() == HealthStatus.UNKNOWN

    def test_lege_componenten(self):
        snap = HealthSnapshot()
        # Geen componenten → all() op lege lijst is True → HEALTHY
        assert snap.compute_overall() == HealthStatus.HEALTHY


class TestRetryPolicy:
    def test_publish_policy_idempotency_required(self):
        policy = DEFAULT_RETRY_POLICIES[JobType.PUBLISH]
        assert policy.idempotency_required is True
        assert policy.max_attempts == 2

    def test_ai_agent_policy(self):
        policy = DEFAULT_RETRY_POLICIES[JobType.AI_AGENT_CALL]
        assert policy.max_attempts == 3
        assert policy.idempotency_required is False

    def test_analytics_fetch_meer_retries(self):
        policy = DEFAULT_RETRY_POLICIES[JobType.ANALYTICS_FETCH]
        assert policy.max_attempts == 5

    def test_alle_job_types_hebben_policy(self):
        expected_types = {
            JobType.CAMPAIGN_PIPELINE, JobType.PUBLISH, JobType.AI_AGENT_CALL,
            JobType.VIDEO_GENERATION, JobType.ANALYTICS_FETCH,
            JobType.FEEDBACK_INJECTION, JobType.SCHEDULER_JOB,
            JobType.BRAND_MEMORY_UPDATE,
        }
        assert set(DEFAULT_RETRY_POLICIES.keys()) == expected_types


class TestAuditEntry:
    def test_write_once_model(self):
        entry = AuditEntry(
            event_id="evt_001",
            correlation_id="corr_001",
            job_type=JobType.PUBLISH,
            job_name="test",
            outcome=JobOutcome.SUCCESS,
        )
        assert entry.audit_id.startswith("aud_")
        assert entry.actor == "system"

    def test_optionele_velden(self):
        entry = AuditEntry(
            event_id="evt_002",
            correlation_id="corr_002",
            job_type=JobType.AI_AGENT_CALL,
            job_name="agent",
            outcome=JobOutcome.FAILURE,
            cost_usd=0.03,
            error_summary="Timeout",
        )
        assert entry.cost_usd == 0.03
        assert entry.error_summary == "Timeout"
