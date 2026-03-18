"""
Observability Decorators — minimale code-invasie in bestaande workflows

ONTWERP PRINCIPE:
  Bestaande code NIET herschrijven — alleen wrappen.
  Elke decorator voegt één verantwoordelijkheid toe:
    @observed       → correlation ID + audit trail + structured log
    @with_retry     → retry-logica via RetryEngine
    @idempotent     → publish-bescherming via idempotency key

GEBRUIK IN BESTAANDE CODE:

  # campaign_pipeline.py
  from observability.decorators import observed

  @observed(job_type=JobType.CAMPAIGN_PIPELINE, job_name="run_pipeline")
  def run_pipeline(app_id: str, ...) -> CampaignBundle:
      ...

  # approval_service.py
  from observability.decorators import observed, idempotent

  @idempotent(key_fn=lambda bundle: f"publish_{bundle.id}_tiktok")
  @observed(job_type=JobType.PUBLISH, job_name="TikTokPublisher.publish")
  def _publish_now(bundle: CampaignBundle) -> CampaignBundle:
      ...

  # agents/base_agent.py
  from observability.decorators import observed

  @observed(job_type=JobType.AI_AGENT_CALL, job_name_fn=lambda self, *a, **k: f"{self.__class__.__name__}.run")
  def _call_api(self, system: str, user_message: str) -> str:
      ...
"""

import functools
import time
import traceback
from datetime import datetime
from typing import Any, Callable, TypeVar

from loguru import logger

from observability.audit_store import get_audit_store
from observability.correlation import get_correlation_id, set_correlation_id
from observability.models import (
    DEFAULT_RETRY_POLICIES,
    JobOutcome,
    JobType,
    OperationalEvent,
)
from observability.retry_engine import IdempotencyViolation, get_retry_engine

T = TypeVar("T")


def observed(
    job_type: JobType = JobType.CAMPAIGN_PIPELINE,
    job_name: str = "",
    job_name_fn: Callable | None = None,
    extract_app_id: Callable | None = None,
    extract_campaign_id: Callable | None = None,
    log_cost: bool = False,
) -> Callable:
    """
    Decorator die een functie observeerbaar maakt.
    Voegt toe: correlation ID, audit trail, structured logging, duration tracking.

    Args:
        job_type: Categorie van de job
        job_name: Statische naam voor audit. Als leeg → fn.__name__
        job_name_fn: Functie die naam dynamisch berekent: fn(fn_self_or_args, *args, **kwargs)
        extract_app_id: Functie die app_id extraheeert uit args/kwargs
        extract_campaign_id: Idem voor campaign_id
        log_cost: Log cost_usd uit return value (als object .total_cost_usd heeft)

    Gebruik:
        @observed(job_type=JobType.CAMPAIGN_PIPELINE, job_name="run_pipeline")
        def run_pipeline(app_id: str, ...): ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            # Bepaal correlation ID (maak nieuw als niet aanwezig)
            cid = get_correlation_id()
            if not cid:
                app_id_val = _extract_value(extract_app_id, args, kwargs, default="")
                cid = set_correlation_id(app_id=app_id_val)

            name = job_name or (job_name_fn(*args, **kwargs) if job_name_fn else fn.__name__)
            app_id_val = _extract_value(extract_app_id, args, kwargs)
            campaign_id_val = _extract_value(extract_campaign_id, args, kwargs)

            event = OperationalEvent(
                job_type=job_type,
                job_name=name,
                app_id=app_id_val,
                campaign_id=campaign_id_val,
                correlation_id=cid,
            )

            logger.info(
                f"[{name}] Gestart",
                extra={
                    "component": name,
                    "app_id": app_id_val,
                    "campaign_id": campaign_id_val,
                    "correlation_id": cid,
                },
            )

            start = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                duration = time.monotonic() - start

                event.outcome = JobOutcome.SUCCESS
                event.ended_at = datetime.utcnow()
                event.duration_sec = duration

                if log_cost and hasattr(result, "total_cost_usd"):
                    event.cost_usd = result.total_cost_usd

                logger.info(
                    f"[{name}] Klaar in {duration:.2f}s",
                    extra={
                        "component": name,
                        "app_id": app_id_val,
                        "duration_ms": round(duration * 1000, 1),
                        "correlation_id": cid,
                    },
                )

                get_audit_store().write_from_event(event)
                return result

            except Exception as exc:
                duration = time.monotonic() - start
                event.outcome = JobOutcome.FAILURE
                event.ended_at = datetime.utcnow()
                event.duration_sec = duration
                event.error = str(exc)
                event.error_type = type(exc).__name__
                event.error_traceback = traceback.format_exc()

                logger.error(
                    f"[{name}] MISLUKT na {duration:.2f}s: {type(exc).__name__}: {str(exc)[:150]}",
                    extra={
                        "component": name,
                        "app_id": app_id_val,
                        "duration_ms": round(duration * 1000, 1),
                        "correlation_id": cid,
                        "error_type": type(exc).__name__,
                    },
                )

                get_audit_store().write_from_event(event)
                raise

        return wrapper
    return decorator


def with_retry(
    job_type: JobType | None = None,
    job_name: str = "",
    extract_app_id: Callable | None = None,
    extract_campaign_id: Callable | None = None,
    payload_fn: Callable | None = None,
) -> Callable:
    """
    Decorator die retry-logica toevoegt via RetryEngine.
    Combineert met @observed voor volledige observability.

    Args:
        job_type: Bepaalt welke RetryPolicy gebruikt wordt
        job_name: Naam voor logging
        extract_app_id: Extraheer app_id uit args
        payload_fn: Functie die serialiseerbare payload maakt voor dead letter opslag

    Gebruik:
        @with_retry(job_type=JobType.AI_AGENT_CALL, job_name="IdeaGenerator.run")
        def run(self, ...): ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            resolved_job_type = job_type or JobType.AI_AGENT_CALL
            name = job_name or fn.__name__
            app_id_val = _extract_value(extract_app_id, args, kwargs)
            payload = payload_fn(*args, **kwargs) if payload_fn else {}

            engine = get_retry_engine()
            return engine.execute(
                fn=fn,
                args=args,
                kwargs=kwargs,
                job_type=resolved_job_type,
                job_name=name,
                app_id=app_id_val,
                payload=payload,
            )
        return wrapper
    return decorator


def idempotent(
    key_fn: Callable[..., str],
    job_type: JobType = JobType.PUBLISH,
) -> Callable:
    """
    Decorator die dubbele uitvoering van publish-operaties voorkomt.
    Controleert idempotency key VÓÓR uitvoering.

    Args:
        key_fn: Functie die de idempotency key berekent uit args/kwargs
                Voorbeeld: lambda bundle: f"publish_{bundle.id}_tiktok"

    KRITIEK: Gebruik dit op ALLE publish-functies.
    Bij crash+restart wordt de publish NIET opnieuw uitgevoerd als de key al bestaat.

    Gebruik:
        @idempotent(key_fn=lambda bundle, *a, **k: f"publish_{bundle.id}_tiktok")
        def _publish_now(bundle): ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            try:
                idem_key = key_fn(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[Idempotent] Key berekening mislukt: {e} — doorgaan zonder check")
                return fn(*args, **kwargs)

            engine = get_retry_engine()

            # Check VOOR uitvoering
            if engine._idempotency_key_exists(idem_key):
                logger.warning(
                    f"[Idempotent] SKIP — al eerder uitgevoerd: {idem_key}",
                    extra={"component": "idempotent", "idempotency_key": idem_key},
                )
                raise IdempotencyViolation(
                    f"Operatie al eerder succesvol uitgevoerd (key={idem_key}). "
                    f"Dubbele publicatie voorkomen."
                )

            result = fn(*args, **kwargs)

            # Registreer NA succesvolle uitvoering
            engine._register_idempotency_key(idem_key, fn.__name__)
            return result

        return wrapper
    return decorator


def track_health_on_failure(component_name: str) -> Callable:
    """
    Decorator die health-status bijwerkt bij consistente fouten.
    Gebruikt consecutive_failures teller om DEGRADED/UNHEALTHY te triggeren.

    Gebruik:
        @track_health_on_failure("tiktok_publisher")
        def publish(self, bundle): ...
    """
    _failure_counts: dict[str, int] = {}

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            try:
                result = fn(*args, **kwargs)
                _failure_counts[component_name] = 0  # Reset bij succes
                return result
            except Exception as exc:
                count = _failure_counts.get(component_name, 0) + 1
                _failure_counts[component_name] = count

                if count >= 3:
                    logger.error(
                        f"[HealthTracker] {component_name} heeft {count} opeenvolgende fouten — "
                        f"overweeg health check",
                        extra={"component": "health_tracker", "consecutive_failures": count},
                    )
                    # Trigger alert bij 3+ opeenvolgende fouten
                    if count == 3:
                        try:
                            from observability.alerting import get_alerting_service
                            from observability.models import Severity
                            get_alerting_service().send(
                                severity=Severity.HIGH,
                                title=f"Component degraded: {component_name}",
                                message=f"{count} opeenvolgende fouten. Laatste: {type(exc).__name__}: {str(exc)[:100]}",
                                component=component_name,
                                deduplication_key=f"{component_name}_consecutive_failures",
                            )
                        except Exception:
                            pass
                raise
        return wrapper
    return decorator


# ──────────────────────────────────────────────
# HULPFUNCTIES
# ──────────────────────────────────────────────

def _extract_value(
    extractor: Callable | None,
    args: tuple,
    kwargs: dict,
    default: str | None = None,
) -> str | None:
    """Veilig een waarde extraheren uit functie-argumenten."""
    if extractor is None:
        return default
    try:
        return extractor(*args, **kwargs)
    except Exception:
        return default
