"""
Reliability & Observability Package — AY Marketing OS

Importeer vanuit hier voor een schone API:
  from observability import setup, observed, with_retry, idempotent
  from observability import get_audit_store, get_alerting_service, get_health_checker
"""

from observability.logger import setup_logging as setup
from observability.decorators import observed, with_retry, idempotent, track_health_on_failure
from observability.audit_store import get_audit_store
from observability.alerting import get_alerting_service
from observability.health_checker import get_health_checker
from observability.retry_engine import get_retry_engine, IdempotencyViolation
from observability.correlation import set_correlation_id, get_correlation_id

__all__ = [
    "setup",
    "observed",
    "with_retry",
    "idempotent",
    "track_health_on_failure",
    "get_audit_store",
    "get_alerting_service",
    "get_health_checker",
    "get_retry_engine",
    "IdempotencyViolation",
    "set_correlation_id",
    "get_correlation_id",
]
