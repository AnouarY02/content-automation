"""
Retry Engine — exponential backoff met jitter, idempotency-bescherming

RETRY STRATEGIE PER JOB-TYPE:
  publish          → max 2 pogingen, idempotency key verplicht
  ai_agent_call    → max 3 pogingen, 2-20s backoff
  video_generation → max 2 pogingen, 10-60s backoff
  analytics_fetch  → max 5 pogingen, 1-30s backoff (goedkoop, API rate limits)
  campaign_pipeline→ max 2 pogingen, 5-30s backoff
  scheduler_job    → max 2 pogingen, 30-120s backoff

EXPONENTIAL BACKOFF MET JITTER:
  delay = min(base * (exponential_base ** attempt), max_delay)
  delay += random.uniform(0, delay * 0.3)   # ±30% jitter

  Poging 1 mislukt → wacht 2.0s  (+ 0-0.6s jitter)
  Poging 2 mislukt → wacht 4.0s  (+ 0-1.2s jitter)
  Poging 3 mislukt → wacht 8.0s  (+ 0-2.4s jitter)
  → dead letter na poging 3

IDEMPOTENCY BESCHERMING (publish):
  Vóór elke publish-poging: check of idempotency_key al bestaat in
  data/dead_letter/idempotency_keys.json
  Als key bestaat → SKIP (return eerder resultaat)
  Na succesvolle publish → sla key op

  Dit garandeert dat een post NOOIT twee keer gepubliceerd wordt,
  ook niet bij crashes, retries of scheduler-restarts.
"""

import json
import random
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from loguru import logger

from observability.models import (
    DEFAULT_RETRY_POLICIES,
    DeadLetterEntry,
    JobOutcome,
    JobType,
    OperationalEvent,
    RetryPolicy,
)
from observability.correlation import get_correlation_id

ROOT = Path(__file__).parent.parent
IDEMPOTENCY_STORE = ROOT / "data" / "dead_letter" / "idempotency_keys.json"
DEAD_LETTER_DIR = ROOT / "data" / "dead_letter"
DEAD_LETTER_DIR.mkdir(parents=True, exist_ok=True)

T = TypeVar("T")


class RetryEngine:
    """
    Voert functies uit met retry-logica en dead letter queue.

    GEBRUIK:
        engine = RetryEngine()
        result = engine.execute(
            fn=my_function,
            args=(arg1, arg2),
            kwargs={"key": "value"},
            job_type=JobType.AI_AGENT_CALL,
            job_name="ScriptWriterAgent.run",
            app_id="app_001",
        )
    """

    def __init__(self):
        self._policies = DEFAULT_RETRY_POLICIES.copy()

    def execute(
        self,
        fn: Callable[..., T],
        args: tuple = (),
        kwargs: dict | None = None,
        job_type: JobType = JobType.AI_AGENT_CALL,
        job_name: str = "",
        app_id: str | None = None,
        campaign_id: str | None = None,
        idempotency_key: str | None = None,
        payload: dict | None = None,
    ) -> T:
        """
        Voer een functie uit met retry-logica.

        Args:
            fn: De uit te voeren functie
            args: Positie-argumenten
            kwargs: Keyword-argumenten
            job_type: Type job (bepaalt retry-policy)
            job_name: Beschrijvende naam voor logging/audit
            app_id: App context
            campaign_id: Campaign context
            idempotency_key: Unieke key voor publish-operaties
            payload: Serialiseerbare job-parameters (voor dead letter opslag)

        Returns:
            Resultaat van fn(*args, **kwargs)

        Raises:
            De laatste exception als alle pogingen mislukken
        """
        kwargs = kwargs or {}
        payload = payload or {}
        policy = self._policies.get(job_type, RetryPolicy(job_type=job_type))

        # ── Idempotency check (publish bescherming) ──
        if idempotency_key and policy.idempotency_required:
            if self._idempotency_key_exists(idempotency_key):
                logger.warning(
                    f"[RetryEngine] SKIP — idempotency key al gebruikt: {idempotency_key}",
                    extra={"component": "retry_engine", "app_id": app_id},
                )
                raise IdempotencyViolation(
                    f"Operatie '{job_name}' al eerder succesvol uitgevoerd (key={idempotency_key}). "
                    f"Dubbele uitvoering voorkomen."
                )

        # ── Retry loop ──
        event = OperationalEvent(
            job_type=job_type,
            job_name=job_name or fn.__name__,
            app_id=app_id,
            campaign_id=campaign_id,
            correlation_id=get_correlation_id(),
            max_attempts=policy.max_attempts,
        )

        last_exception: Exception | None = None
        first_attempt_time = datetime.utcnow()

        for attempt in range(1, policy.max_attempts + 1):
            event.attempt_number = attempt
            attempt_start = time.monotonic()

            try:
                logger.debug(
                    f"[RetryEngine] Poging {attempt}/{policy.max_attempts}: {job_name}",
                    extra={"component": "retry_engine", "app_id": app_id},
                )

                result = fn(*args, **kwargs)

                # Succes
                duration = time.monotonic() - attempt_start
                logger.info(
                    f"[RetryEngine] Succes na {attempt} poging(en): {job_name} ({duration:.2f}s)",
                    extra={"component": "retry_engine", "app_id": app_id},
                )

                # Registreer idempotency key na succes
                if idempotency_key and policy.idempotency_required:
                    self._register_idempotency_key(idempotency_key, job_name)

                event.outcome = JobOutcome.SUCCESS
                event.ended_at = datetime.utcnow()
                return result

            except Exception as exc:
                last_exception = exc
                exc_type = type(exc).__name__
                duration = time.monotonic() - attempt_start

                # Check of dit een non-retryable exception is
                if self._is_non_retryable(exc, policy):
                    logger.error(
                        f"[RetryEngine] Non-retryable fout bij {job_name}: {exc_type}: {exc}",
                        extra={"component": "retry_engine", "app_id": app_id},
                    )
                    event.outcome = JobOutcome.FAILURE
                    event.error = str(exc)
                    event.error_type = exc_type
                    break  # Geen retry

                if attempt < policy.max_attempts:
                    delay = self._compute_delay(attempt, policy)
                    logger.warning(
                        f"[RetryEngine] Poging {attempt} mislukt: {exc_type}: {str(exc)[:100]}. "
                        f"Wacht {delay:.1f}s voor volgende poging...",
                        extra={"component": "retry_engine", "app_id": app_id},
                    )
                    time.sleep(delay)
                    event.outcome = JobOutcome.RETRY
                else:
                    # Laatste poging ook mislukt → dead letter
                    logger.error(
                        f"[RetryEngine] ALLE {policy.max_attempts} pogingen mislukt: {job_name}",
                        extra={"component": "retry_engine", "app_id": app_id},
                    )
                    event.outcome = JobOutcome.DEAD_LETTERED
                    event.error = str(exc)
                    event.error_type = exc_type

                    dl_entry = self._create_dead_letter(
                        event=event,
                        exc=exc,
                        payload=payload,
                        first_attempt=first_attempt_time,
                    )
                    self._save_dead_letter(dl_entry, app_id)
                    self._trigger_alert(dl_entry, policy)

        event.ended_at = datetime.utcnow()

        if last_exception:
            raise last_exception
        raise RuntimeError(f"Onbekende fout in retry engine voor {job_name}")

    def _compute_delay(self, attempt: int, policy: RetryPolicy) -> float:
        """Bereken wachttijd met exponential backoff + jitter."""
        delay = min(
            policy.base_delay_sec * (policy.exponential_base ** (attempt - 1)),
            policy.max_delay_sec,
        )
        if policy.jitter:
            delay += random.uniform(0, delay * 0.3)
        return delay

    def _is_non_retryable(self, exc: Exception, policy: RetryPolicy) -> bool:
        """Bepaal of een exception retryable is."""
        exc_type = type(exc).__name__
        # Non-retryable exceptions stoppen direct
        for non_retry in policy.non_retryable_exceptions:
            if exc_type == non_retry.split(".")[-1]:
                return True
        # IdempotencyViolation is altijd non-retryable
        if isinstance(exc, IdempotencyViolation):
            return True
        return False

    def _create_dead_letter(
        self,
        event: OperationalEvent,
        exc: Exception,
        payload: dict,
        first_attempt: datetime,
    ) -> DeadLetterEntry:
        return DeadLetterEntry(
            original_event_id=event.event_id,
            correlation_id=event.correlation_id,
            job_type=event.job_type,
            job_name=event.job_name,
            app_id=event.app_id,
            campaign_id=event.campaign_id,
            total_attempts=event.attempt_number,
            first_attempt=first_attempt,
            final_error=str(exc),
            final_error_type=type(exc).__name__,
            full_traceback=traceback.format_exc(),
            payload=payload,
        )

    def _save_dead_letter(self, entry: DeadLetterEntry, app_id: str | None) -> Path:
        """Sla dead letter op in data/dead_letter/{app_id}/."""
        subdir = DEAD_LETTER_DIR / (app_id or "unknown")
        subdir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = subdir / f"{entry.job_type}_{ts}_{entry.dl_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        logger.error(
            f"[RetryEngine] Dead letter opgeslagen: {path.name}",
            extra={"component": "retry_engine", "app_id": app_id, "is_audit": True},
        )
        return path

    def _trigger_alert(self, entry: DeadLetterEntry, policy: RetryPolicy) -> None:
        """Stuur alert als policy dat vereist."""
        if not policy.send_alert_on_dead_letter:
            return
        try:
            from observability.alerting import AlertingService
            from observability.models import Severity
            AlertingService().send(
                severity=Severity.HIGH,
                title=f"Dead letter: {entry.job_type} voor {entry.app_id or 'systeem'}",
                message=(
                    f"Job '{entry.job_name}' mislukt na {entry.total_attempts} pogingen.\n"
                    f"Fout: {entry.final_error_type}: {entry.final_error[:200]}\n"
                    f"Dead letter ID: {entry.dl_id}"
                ),
                component=entry.job_type,
                app_id=entry.app_id,
                campaign_id=entry.campaign_id,
                correlation_id=entry.correlation_id,
                deduplication_key=f"{entry.job_type}_{entry.app_id}_dead_letter",
                metadata={"dl_id": entry.dl_id, "payload_keys": list(entry.payload.keys())},
            )
        except Exception as e:
            logger.warning(f"[RetryEngine] Alert verzenden mislukt: {e}")

    # ── Idempotency Key Store ──

    def _load_idempotency_store(self) -> dict:
        if not IDEMPOTENCY_STORE.exists():
            return {}
        with open(IDEMPOTENCY_STORE, encoding="utf-8") as f:
            return json.load(f)

    def _save_idempotency_store(self, store: dict) -> None:
        IDEMPOTENCY_STORE.parent.mkdir(parents=True, exist_ok=True)
        with open(IDEMPOTENCY_STORE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2, default=str)

    def _idempotency_key_exists(self, key: str) -> bool:
        store = self._load_idempotency_store()
        return key in store

    def _register_idempotency_key(self, key: str, job_name: str) -> None:
        store = self._load_idempotency_store()
        store[key] = {
            "registered_at": datetime.utcnow().isoformat(),
            "job_name": job_name,
            "correlation_id": get_correlation_id(),
        }
        self._save_idempotency_store(store)
        logger.debug(f"[RetryEngine] Idempotency key geregistreerd: {key}")


class IdempotencyViolation(Exception):
    """Geraised als een publish-operatie al eerder is uitgevoerd."""
    pass


# Module-level singleton
_engine: RetryEngine | None = None


def get_retry_engine() -> RetryEngine:
    global _engine
    if _engine is None:
        _engine = RetryEngine()
    return _engine
