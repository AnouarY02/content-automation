"""
Reliability & Observability — Datamodellen

Alle Pydantic modellen voor:
  - OperationalEvent    → één geobserveerde workflow-executie
  - JobStatus           → live state van een draaiende of voltooide job
  - RetryPolicy         → regels voor retry-gedrag per job-type
  - AuditEntry          → onveranderbaar audit-record (write-once)
  - HealthSnapshot      → momentopname van systeem-gezondheid
  - DeadLetterEntry     → mislukte job in quarantaine
  - AlertRecord         → gegenereerd alert

DESIGN PRINCIPE:
  AuditEntry is write-once — nooit muteren, alleen appenden.
  OperationalEvent en JobStatus zijn mutable tijdens executie.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────

class JobType(str, Enum):
    CAMPAIGN_PIPELINE   = "campaign_pipeline"
    APPROVAL            = "approval"
    PUBLISH             = "publish"
    ANALYTICS_FETCH     = "analytics_fetch"
    FEEDBACK_INJECTION  = "feedback_injection"
    SCHEDULER_JOB       = "scheduler_job"
    HEALTH_CHECK        = "health_check"
    VIDEO_GENERATION    = "video_generation"
    AI_AGENT_CALL       = "ai_agent_call"
    BRAND_MEMORY_UPDATE = "brand_memory_update"


class JobOutcome(str, Enum):
    SUCCESS       = "success"
    FAILURE       = "failure"
    RETRY         = "retry"
    DEAD_LETTERED = "dead_lettered"
    SKIPPED       = "skipped"      # Idempotency guard: al eerder uitgevoerd
    IN_PROGRESS   = "in_progress"


class Severity(str, Enum):
    DEBUG    = "debug"
    INFO     = "info"
    WARNING  = "warning"
    HIGH     = "high"
    CRITICAL = "critical"


class HealthStatus(str, Enum):
    HEALTHY   = "healthy"
    DEGRADED  = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN   = "unknown"


class ComponentName(str, Enum):
    TIKTOK_PUBLISHER    = "tiktok_publisher"
    TIKTOK_FETCHER      = "tiktok_fetcher"
    VIDEO_ENGINE        = "video_engine"
    KLING_PROVIDER      = "kling_provider"
    RUNWAY_PROVIDER     = "runway_provider"
    DID_PROVIDER        = "did_provider"
    ELEVENLABS          = "elevenlabs"
    AI_AGENTS           = "ai_agents"
    ANTHROPIC_API       = "anthropic_api"
    OPENAI_API          = "openai_api"
    CAMPAIGN_PIPELINE   = "campaign_pipeline"
    SCHEDULER           = "scheduler"
    ANALYTICS_FETCHER   = "analytics_fetcher"
    SUPABASE            = "supabase"
    FILESYSTEM          = "filesystem"


# ──────────────────────────────────────────────
# OPERATIONAL EVENT
# ──────────────────────────────────────────────

class OperationalEvent(BaseModel):
    """
    Eén geobserveerde workflow-executie — aangemaakt bij start, afgesloten bij einde.

    Voorbeeld:
    {
      "event_id": "evt_a1b2c3d4",
      "correlation_id": "corr_campaign_app001_20260310",
      "job_type": "campaign_pipeline",
      "job_name": "run_pipeline:app_001",
      "app_id": "app_001",
      "campaign_id": "abc123",
      "started_at": "2026-03-10T09:00:00",
      "ended_at": "2026-03-10T09:02:34",
      "duration_sec": 154.2,
      "outcome": "success",
      "attempt_number": 1,
      "metadata": {"idea_index": 0, "platform": "tiktok"},
      "error": null,
      "error_type": null
    }
    """
    event_id: str = Field(default_factory=lambda: f"evt_{str(uuid4())[:8]}")
    correlation_id: str = ""
    job_type: JobType
    job_name: str
    app_id: str | None = None
    campaign_id: str | None = None

    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    duration_sec: float | None = None

    outcome: JobOutcome = JobOutcome.IN_PROGRESS
    attempt_number: int = 1
    max_attempts: int = 3

    metadata: dict[str, Any] = {}
    error: str | None = None
    error_type: str | None = None   # ExceptionClass naam
    error_traceback: str | None = None

    cost_usd: float | None = None


# ──────────────────────────────────────────────
# JOB STATUS
# ──────────────────────────────────────────────

class JobStatus(BaseModel):
    """
    Live status van een draaiende of recent voltooide job.
    Opgeslagen in data/health/active_jobs.json (overschrijfbaar).

    Voorbeeld:
    {
      "job_id": "job_abc123",
      "job_type": "campaign_pipeline",
      "app_id": "app_001",
      "status": "in_progress",
      "progress_pct": 60,
      "current_step": "Stap 4/6: Video produceren",
      "started_at": "2026-03-10T09:00:00",
      "estimated_completion": "2026-03-10T09:03:00"
    }
    """
    job_id: str = Field(default_factory=lambda: f"job_{str(uuid4())[:8]}")
    job_type: JobType
    app_id: str | None = None
    campaign_id: str | None = None
    correlation_id: str = ""

    status: JobOutcome = JobOutcome.IN_PROGRESS
    progress_pct: int = 0           # 0-100
    current_step: str = ""
    total_steps: int = 0
    completed_steps: int = 0

    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)
    estimated_completion: datetime | None = None

    def update(self, step: str, completed: int, total: int) -> None:
        self.current_step = step
        self.completed_steps = completed
        self.total_steps = total
        self.progress_pct = int((completed / max(total, 1)) * 100)
        self.last_updated_at = datetime.utcnow()


# ──────────────────────────────────────────────
# RETRY POLICY
# ──────────────────────────────────────────────

class RetryPolicy(BaseModel):
    """
    Retry-configuratie per job-type.

    ONTWERPBESLISSINGEN:
    - publish jobs: max 2 retries (idempotency beschermt, maar voorzichtig)
    - ai_agent_call: max 3 retries (API tijdelijk down)
    - video_generation: max 2 retries (duur, provider-fouten)
    - analytics_fetch: max 5 retries (lage kosten, API rate limits)

    Voorbeeld:
    {
      "job_type": "publish",
      "max_attempts": 2,
      "base_delay_sec": 5.0,
      "max_delay_sec": 30.0,
      "exponential_base": 2.0,
      "jitter": true,
      "retryable_exceptions": ["httpx.TimeoutException", "httpx.ConnectError"],
      "non_retryable_exceptions": ["PermissionError", "ValueError"],
      "idempotency_required": true
    }
    """
    job_type: JobType
    max_attempts: int = 3
    base_delay_sec: float = 2.0
    max_delay_sec: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True                  # Voeg willekeurige offset toe (stampede preventie)

    # Welke exceptions zijn retryable?
    retryable_exceptions: list[str] = [
        "httpx.TimeoutException",
        "httpx.ConnectError",
        "httpx.RemoteProtocolError",
        "anthropic.APIConnectionError",
        "anthropic.RateLimitError",
        "TimeoutError",
        "ConnectionError",
    ]

    # Welke exceptions stoppen DIRECT (geen retry)
    non_retryable_exceptions: list[str] = [
        "PermissionError",
        "ValueError",
        "FileNotFoundError",
        "json.JSONDecodeError",
    ]

    idempotency_required: bool = False   # Alleen True voor publish
    send_alert_on_dead_letter: bool = True


# Standaard policies per job-type
DEFAULT_RETRY_POLICIES: dict[JobType, RetryPolicy] = {
    JobType.CAMPAIGN_PIPELINE: RetryPolicy(
        job_type=JobType.CAMPAIGN_PIPELINE,
        max_attempts=2,
        base_delay_sec=5.0,
        max_delay_sec=30.0,
    ),
    JobType.PUBLISH: RetryPolicy(
        job_type=JobType.PUBLISH,
        max_attempts=2,
        base_delay_sec=5.0,
        max_delay_sec=30.0,
        idempotency_required=True,       # KRITIEK — nooit dubbel publiceren
        send_alert_on_dead_letter=True,
    ),
    JobType.AI_AGENT_CALL: RetryPolicy(
        job_type=JobType.AI_AGENT_CALL,
        max_attempts=3,
        base_delay_sec=2.0,
        max_delay_sec=20.0,
    ),
    JobType.VIDEO_GENERATION: RetryPolicy(
        job_type=JobType.VIDEO_GENERATION,
        max_attempts=2,
        base_delay_sec=10.0,
        max_delay_sec=60.0,
        send_alert_on_dead_letter=True,
    ),
    JobType.ANALYTICS_FETCH: RetryPolicy(
        job_type=JobType.ANALYTICS_FETCH,
        max_attempts=5,
        base_delay_sec=1.0,
        max_delay_sec=30.0,
    ),
    JobType.FEEDBACK_INJECTION: RetryPolicy(
        job_type=JobType.FEEDBACK_INJECTION,
        max_attempts=3,
        base_delay_sec=2.0,
        max_delay_sec=15.0,
    ),
    JobType.SCHEDULER_JOB: RetryPolicy(
        job_type=JobType.SCHEDULER_JOB,
        max_attempts=2,
        base_delay_sec=30.0,
        max_delay_sec=120.0,
        send_alert_on_dead_letter=True,
    ),
    JobType.BRAND_MEMORY_UPDATE: RetryPolicy(
        job_type=JobType.BRAND_MEMORY_UPDATE,
        max_attempts=3,
        base_delay_sec=1.0,
        max_delay_sec=10.0,
        non_retryable_exceptions=["PermissionError", "ValueError", "json.JSONDecodeError"],
    ),
}


# ──────────────────────────────────────────────
# AUDIT ENTRY (write-once)
# ──────────────────────────────────────────────

class AuditEntry(BaseModel):
    """
    Onveranderbaar audit-record — één per job-executie.
    Nooit muteren na aanmaken. Alleen appenden aan audit log.

    Opslaglocatie: data/audit/{app_id}/audit_{YYYY-MM}.jsonl (line-delimited JSON)

    Voorbeeld:
    {
      "audit_id": "aud_x9y8z7w6",
      "event_id": "evt_a1b2c3d4",
      "correlation_id": "corr_campaign_app001_20260310",
      "timestamp": "2026-03-10T09:02:34Z",
      "job_type": "publish",
      "job_name": "TikTokPublisher.publish",
      "app_id": "app_001",
      "campaign_id": "abc123",
      "outcome": "success",
      "attempt_number": 1,
      "duration_sec": 8.4,
      "actor": "approval_service",
      "idempotency_key": "publish_abc123_tiktok",
      "metadata": {"post_id": "7380123456789", "platform": "tiktok"},
      "cost_usd": 0.0
    }
    """
    audit_id: str = Field(default_factory=lambda: f"aud_{str(uuid4())[:8]}")
    event_id: str
    correlation_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    job_type: JobType
    job_name: str
    app_id: str | None = None
    campaign_id: str | None = None

    outcome: JobOutcome
    attempt_number: int = 1
    duration_sec: float | None = None

    actor: str = "system"              # Welk component initieerde dit?
    idempotency_key: str | None = None

    metadata: dict[str, Any] = {}
    error_summary: str | None = None   # Kort, geen stack trace
    cost_usd: float | None = None


# ──────────────────────────────────────────────
# DEAD LETTER ENTRY
# ──────────────────────────────────────────────

class DeadLetterEntry(BaseModel):
    """
    Mislukte job die de maximale retry-pogingen heeft uitgeput.
    Opgeslagen in data/dead_letter/{app_id}/{job_type}_{timestamp}.json

    Vereist handmatige review door operator.

    Voorbeeld:
    {
      "dl_id": "dl_m5n6o7p8",
      "original_event_id": "evt_a1b2c3",
      "job_type": "video_generation",
      "app_id": "app_001",
      "total_attempts": 2,
      "first_attempt": "2026-03-10T09:00:00",
      "last_attempt": "2026-03-10T09:01:20",
      "final_error": "KlingProvider: API timeout na 120s",
      "final_error_type": "TimeoutError",
      "payload": {"script_id": "script_001", "video_type": "screen_demo"},
      "resolution": null,
      "resolved_at": null,
      "resolved_by": null
    }
    """
    dl_id: str = Field(default_factory=lambda: f"dl_{str(uuid4())[:8]}")
    original_event_id: str
    correlation_id: str
    job_type: JobType
    job_name: str
    app_id: str | None = None
    campaign_id: str | None = None

    total_attempts: int
    first_attempt: datetime
    last_attempt: datetime = Field(default_factory=datetime.utcnow)

    final_error: str
    final_error_type: str
    full_traceback: str | None = None

    # Originele job-parameters voor heruitvoering
    payload: dict[str, Any] = {}

    # Resolutie (ingevuld door operator)
    resolution: str | None = None      # "retried_manually" | "discarded" | "fixed"
    resolved_at: datetime | None = None
    resolved_by: str | None = None


# ──────────────────────────────────────────────
# HEALTH SNAPSHOT
# ──────────────────────────────────────────────

class ComponentHealth(BaseModel):
    """
    Gezondheid van één systeem-component.

    Voorbeeld:
    {
      "component": "tiktok_publisher",
      "status": "healthy",
      "latency_ms": 234,
      "last_check": "2026-03-10T09:00:00",
      "last_success": "2026-03-10T08:45:00",
      "consecutive_failures": 0,
      "details": {"api_version": "v2", "token_valid": true}
    }
    """
    component: ComponentName
    status: HealthStatus = HealthStatus.UNKNOWN
    latency_ms: float | None = None
    last_check: datetime = Field(default_factory=datetime.utcnow)
    last_success: datetime | None = None
    consecutive_failures: int = 0
    error_message: str | None = None
    details: dict[str, Any] = {}


class HealthSnapshot(BaseModel):
    """
    Volledige momentopname van systeem-gezondheid.
    Opgeslagen in data/health/latest.json (overschrijven) + data/health/history.jsonl

    Opslaglocatie: data/health/latest.json

    Voorbeeld:
    {
      "snapshot_id": "snap_q1r2s3t4",
      "taken_at": "2026-03-10T09:00:00",
      "overall_status": "degraded",
      "healthy_count": 5,
      "degraded_count": 1,
      "unhealthy_count": 0,
      "components": {...}
    }
    """
    snapshot_id: str = Field(default_factory=lambda: f"snap_{str(uuid4())[:8]}")
    taken_at: datetime = Field(default_factory=datetime.utcnow)
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    healthy_count: int = 0
    degraded_count: int = 0
    unhealthy_count: int = 0
    components: dict[str, ComponentHealth] = {}

    def compute_overall(self) -> HealthStatus:
        """Bereken overall status op basis van alle componenten."""
        statuses = [c.status for c in self.components.values()]
        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        if any(s == HealthStatus.DEGRADED for s in statuses):
            return HealthStatus.DEGRADED
        if all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        return HealthStatus.UNKNOWN


# ──────────────────────────────────────────────
# ALERT RECORD
# ──────────────────────────────────────────────

class AlertRecord(BaseModel):
    """
    Gegenereerd alert — aangemaakt door alerting.py.
    Opgeslagen in data/alerts/alerts_{YYYY-MM}.jsonl

    Voorbeeld:
    {
      "alert_id": "alrt_u5v6w7x8",
      "triggered_at": "2026-03-10T09:05:00",
      "severity": "high",
      "title": "Job dead-lettered: video_generation voor app_001",
      "message": "Video generatie mislukt na 2 pogingen. KlingProvider timeout.",
      "component": "video_engine",
      "app_id": "app_001",
      "campaign_id": "abc123",
      "correlation_id": "corr_...",
      "deduplication_key": "video_generation_app_001_dead_letter",
      "suppressed_until": null,
      "acknowledged": false,
      "resolved": false
    }
    """
    alert_id: str = Field(default_factory=lambda: f"alrt_{str(uuid4())[:8]}")
    triggered_at: datetime = Field(default_factory=datetime.utcnow)
    severity: Severity
    title: str
    message: str

    component: str | None = None
    app_id: str | None = None
    campaign_id: str | None = None
    correlation_id: str | None = None

    # Deduplicatie — voorkomt alert spam
    deduplication_key: str = ""
    suppressed_until: datetime | None = None

    acknowledged: bool = False
    resolved: bool = False
    resolved_at: datetime | None = None

    metadata: dict[str, Any] = {}
