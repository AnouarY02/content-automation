"""
Maturity API endpoints — multi-tenant ready.

Routes:
  GET  /api/maturity/{app_id}           — laad of bereken scorecard
  POST /api/maturity/{app_id}/compute   — forceer herberekening
  GET  /api/maturity/{app_id}/history   — lijst historische snapshots
  GET  /api/maturity/{app_id}/dimensions — dimensie-detail replication
  GET  /api/maturity/{app_id}/report    — tekst-rapport

Query params (elke route):
  tenant_id: str = "default"  — tenant isolatie
"""

import threading

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from loguru import logger

from backend.repository.file_maturity import FileMaturityRepository
from maturity.models import MaturityScorecard, MaturitySnapshot
from maturity.report_generator import generate_report
from maturity.scorecard import ScorecardBuilder

router   = APIRouter()
_builder = ScorecardBuilder()

# Per-(tenant, app) lock: voorkomt dubbele gelijktijdige compute
_compute_locks: dict[str, threading.Lock] = {}
_locks_guard   = threading.Lock()


def _get_lock(tenant_id: str, app_id: str) -> threading.Lock:
    key = f"{tenant_id}:{app_id}"
    with _locks_guard:
        if key not in _compute_locks:
            _compute_locks[key] = threading.Lock()
        return _compute_locks[key]


# ── GET /api/maturity/{app_id} ────────────────────────────────────────

@router.get("/{app_id}", response_model=MaturityScorecard)
def get_scorecard(
    app_id:    str,
    refresh:   bool = False,
    tenant_id: str  = Query("default", description="Tenant identifier"),
):
    """
    Geeft de scorecard terug voor app_id binnen een tenant.

    - refresh=false (default): laad bestaande scorecard als die er is
    - refresh=true: forceer herberekening
    """
    repo = FileMaturityRepository(tenant_id=tenant_id)
    if not refresh:
        existing = repo.get_latest(app_id, tenant_id)
        if existing is not None:
            return existing

    return _compute_or_raise(app_id, tenant_id)


# ── POST /api/maturity/{app_id}/compute ───────────────────────────────

@router.post("/{app_id}/compute", response_model=MaturityScorecard)
def compute_scorecard(
    app_id:    str,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """
    Forceer een verse maturity-berekening voor app_id.
    Idempotent: concurrent requests voor dezelfde (tenant, app) worden geserialiseerd.
    """
    return _compute_or_raise(app_id, tenant_id)


# ── GET /api/maturity/{app_id}/history ───────────────────────────────

@router.get("/{app_id}/history", response_model=list[MaturitySnapshot])
def get_history(
    app_id:    str,
    limit:     int = 20,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Historische maturity-snapshots (nieuwste eerst). Max 100."""
    limit = min(max(limit, 1), 100)
    repo  = FileMaturityRepository(tenant_id=tenant_id)
    return repo.get_history(app_id, tenant_id, limit=limit)


# ── GET /api/maturity/{app_id}/dimensions ────────────────────────────

@router.get("/{app_id}/dimensions")
def get_dimensions(
    app_id:    str,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Dimensie-maturity details van de laatste scorecard (voor replication breakdown)."""
    repo      = FileMaturityRepository(tenant_id=tenant_id)
    scorecard = repo.get_latest(app_id, tenant_id)
    if scorecard is None:
        raise HTTPException(
            status_code=404,
            detail=f"Nog geen scorecard voor {app_id} (tenant={tenant_id}). Roep eerst POST /compute aan.",
        )
    return {
        "app_id":            scorecard.app_id,
        "tenant_id":         scorecard.tenant_id,
        "computed_at":       scorecard.computed_at,
        "replication_score": scorecard.replication_score,
        "dimensions":        scorecard.dimension_details,
    }


# ── GET /api/maturity/{app_id}/report ────────────────────────────────

@router.get("/{app_id}/report")
def get_report(
    app_id:    str,
    refresh:   bool = False,
    tenant_id: str  = Query("default", description="Tenant identifier"),
):
    """Leesbaar tekstrapport van de maturity scorecard. Content-Type: text/plain."""
    repo = FileMaturityRepository(tenant_id=tenant_id)
    if refresh:
        scorecard = _compute_or_raise(app_id, tenant_id)
    else:
        scorecard = repo.get_latest(app_id, tenant_id)
        if scorecard is None:
            scorecard = _compute_or_raise(app_id, tenant_id)

    return PlainTextResponse(content=generate_report(scorecard))


# ── Hulp ──────────────────────────────────────────────────────────────

def _compute_or_raise(app_id: str, tenant_id: str = "default") -> MaturityScorecard:
    lock = _get_lock(tenant_id, app_id)
    if not lock.acquire(timeout=60):
        logger.warning(f"[Maturity] Compute al bezig voor ({tenant_id}, {app_id}) — timeout na 60s")
        raise HTTPException(
            status_code=503,
            detail=f"Maturity compute al bezig voor {app_id} — probeer later",
        )
    try:
        scorecard = _builder.compute(app_id, tenant_id=tenant_id)
        # Persist via repository (tenant-aware pad)
        repo = FileMaturityRepository(tenant_id=tenant_id)
        repo.save_scorecard(scorecard)
        return scorecard
    except ValueError as exc:
        logger.warning(f"[Maturity] Ongeldige invoer ({tenant_id}, {app_id}): {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        logger.warning(f"[Maturity] Data niet gevonden ({tenant_id}, {app_id}): {exc}")
        raise HTTPException(
            status_code=404,
            detail=f"Benodigde data ontbreekt voor {app_id}: {exc}",
        )
    except Exception as exc:
        logger.error(
            f"[Maturity] Berekening mislukt ({tenant_id}, {app_id}): {exc}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Maturity berekening mislukt voor {app_id}: {exc}",
        )
    finally:
        lock.release()
