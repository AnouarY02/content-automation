"""
Correlation ID — context propagatie door de volledige call-stack

Gebruikt Python contextvars zodat correlation_id automatisch meestroomt
door async en sync code, zonder dat je hem overal expliciet mee hoeft te geven.

GEBRUIK:
  from observability.correlation import set_correlation_id, get_correlation_id

  # Bij start van een campaign run:
  cid = set_correlation_id(app_id="app_001", campaign_id="abc123")

  # Overal elders in dezelfde call-stack:
  cid = get_correlation_id()   # → "corr_app001_abc123_20260310_090000"
"""

import uuid
from contextvars import ContextVar
from datetime import datetime

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_job_id: ContextVar[str] = ContextVar("job_id", default="")


def set_correlation_id(
    app_id: str = "",
    campaign_id: str = "",
    prefix: str = "corr",
) -> str:
    """
    Maak een nieuwe correlation ID aan en stel hem in voor de huidige context.
    Geeft de ID terug zodat je hem kunt loggen bij de start.
    """
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    parts = [prefix]
    if app_id:
        parts.append(app_id.replace("_", ""))
    if campaign_id:
        parts.append(campaign_id[:8])
    parts.append(ts)

    cid = "_".join(parts)
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    """Haal de huidige correlation ID op. Leeg string als niet ingesteld."""
    return _correlation_id.get()


def set_job_id(job_id: str) -> None:
    _job_id.set(job_id)


def get_job_id() -> str:
    return _job_id.get()


def new_correlation_id() -> str:
    """Genereer een eenmalige correlation ID zonder context in te stellen."""
    return f"corr_{str(uuid.uuid4())[:12]}"
