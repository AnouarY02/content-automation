"""
Health & Observability API Endpoints

GET  /api/health              → Volledige health snapshot (gecached 4 min)
GET  /api/health/live         → Liveness probe (altijd 200 als process draait)
GET  /api/health/ready        → Readiness probe (200 als filesystem OK)
GET  /api/health/{component}  → Check één component (force fresh)
GET  /api/audit               → Recente audit entries
GET  /api/audit/failures      → Alleen mislukte operaties
GET  /api/alerts              → Actieve alerts
POST /api/alerts/{id}/resolve → Markeer alert als opgelost
GET  /api/dead-letter         → Dead letter queue inhoud
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from observability.health_checker import get_health_checker
from observability.audit_store import get_audit_store
from observability.alerting import get_alerting_service
from observability.models import (
    ComponentName,
    HealthStatus,
    JobOutcome,
    JobType,
    Severity,
)

router = APIRouter()
ROOT = Path(__file__).parent.parent.parent
DEAD_LETTER_DIR = ROOT / "data" / "dead_letter"


# ──────────────────────────────────────────────
# HEALTH ENDPOINTS
# ──────────────────────────────────────────────

@router.get("/")
def health_snapshot(force: bool = Query(False, description="Sla cache over")):
    """
    Volledige health snapshot van alle componenten.
    Gecached voor 4 minuten — gebruik force=true voor directe check.
    """
    checker = get_health_checker()

    if not force:
        cached = checker.load_latest()
        if cached:
            age_sec = (datetime.utcnow() - cached.taken_at).total_seconds()
            if age_sec < 240:
                return {
                    **cached.model_dump(mode="json"),
                    "cached": True,
                    "cache_age_sec": round(age_sec),
                }

    snapshot = checker.check_all(force=force)
    return {**snapshot.model_dump(mode="json"), "cached": False}


@router.get("/live")
def liveness():
    """
    Kubernetes-stijl liveness probe.
    Geeft altijd 200 terug als het process draait.
    """
    return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}


@router.get("/ready")
def readiness():
    """
    Readiness probe — systeem klaar als filesystem bereikbaar is.
    Geeft 503 terug als kritieke componenten DOWN zijn.
    """
    checker = get_health_checker()
    fs_health = checker.check_one(ComponentName.FILESYSTEM)

    if fs_health.status == HealthStatus.UNHEALTHY:
        raise HTTPException(
            status_code=503,
            detail=f"Systeem niet klaar: filesystem unhealthy — {fs_health.error_message}",
        )
    return {"status": "ready", "filesystem": fs_health.status}


# ──────────────────────────────────────────────
# AUDIT ENDPOINTS
# ──────────────────────────────────────────────

@router.get("/audit/recent")
def audit_recent(
    app_id: str | None = Query(None),
    job_type: str | None = Query(None),
    outcome: str | None = Query(None),
    limit: int = Query(50, le=500),
):
    """Haal recente audit entries op."""
    store = get_audit_store()
    job_type_enum = None
    if job_type:
        try:
            job_type_enum = JobType(job_type)
        except ValueError:
            valid = [j.value for j in JobType]
            raise HTTPException(status_code=400, detail=f"Onbekend job_type: '{job_type}'. Geldige waarden: {valid}")
    outcome_enum = None
    if outcome:
        try:
            outcome_enum = JobOutcome(outcome)
        except ValueError:
            valid = [o.value for o in JobOutcome]
            raise HTTPException(status_code=400, detail=f"Onbekende outcome: '{outcome}'. Geldige waarden: {valid}")
    entries = store.read_recent(
        app_id=app_id,
        limit=limit,
        job_type=job_type_enum,
        outcome=outcome_enum,
    )
    return [e.model_dump(mode="json") for e in entries]


@router.get("/audit/failures")
def audit_failures(app_id: str | None = Query(None), limit: int = Query(20)):
    """Haal mislukte operaties op uit de audit log."""
    store = get_audit_store()
    failures = store.read_recent(
        app_id=app_id,
        limit=limit,
        outcome=JobOutcome.FAILURE,
    )
    dead_letters = store.read_recent(
        app_id=app_id,
        limit=limit,
        outcome=JobOutcome.DEAD_LETTERED,
    )
    combined = sorted(failures + dead_letters, key=lambda e: e.timestamp, reverse=True)[:limit]
    return [e.model_dump(mode="json") for e in combined]


@router.get("/audit/failure-rate")
def failure_rate(
    app_id: str | None = Query(None),
    job_type: str | None = Query(None),
    hours: int = Query(24),
):
    """Bereken failure-rate voor de laatste N uur."""
    store = get_audit_store()
    job_type_enum = None
    if job_type:
        try:
            job_type_enum = JobType(job_type)
        except ValueError:
            valid = [j.value for j in JobType]
            raise HTTPException(status_code=400, detail=f"Onbekend job_type: '{job_type}'. Geldige waarden: {valid}")
    rate = store.get_failure_rate(app_id=app_id, job_type=job_type_enum, hours=hours)
    return {
        "failure_rate": round(rate, 4),
        "failure_rate_pct": round(rate * 100, 1),
        "hours": hours,
        "app_id": app_id,
        "job_type": job_type,
        "assessment": "high" if rate > 0.3 else "medium" if rate > 0.1 else "low",
    }


# ──────────────────────────────────────────────
# ALERTS ENDPOINTS
# ──────────────────────────────────────────────

@router.get("/alerts")
def get_alerts(
    app_id: str | None = Query(None),
    severity: str | None = Query(None),
):
    """Haal actieve (niet-opgeloste) alerts op."""
    service = get_alerting_service()
    severity_enum = None
    if severity:
        try:
            severity_enum = Severity(severity)
        except ValueError:
            valid = [s.value for s in Severity]
            raise HTTPException(
                status_code=400,
                detail=f"Onbekende severity: '{severity}'. Geldige waarden: {valid}",
            )
    alerts = service.get_active_alerts(app_id=app_id, severity=severity_enum)
    return [a.model_dump(mode="json") for a in alerts]


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str):
    """Markeer een alert als gezien."""
    success = get_alerting_service().acknowledge(alert_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} niet gevonden")
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str):
    """Markeer een alert als opgelost."""
    success = get_alerting_service().resolve(alert_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} niet gevonden")
    return {"status": "resolved", "alert_id": alert_id}


# ──────────────────────────────────────────────
# DEAD LETTER ENDPOINTS
# ──────────────────────────────────────────────

@router.get("/dead-letter")
def get_dead_letters(app_id: str | None = Query(None)):
    """Haal alle dead letter entries op die wachten op review."""
    entries = []
    search_dir = DEAD_LETTER_DIR / app_id if app_id else DEAD_LETTER_DIR

    if not search_dir.exists():
        return []

    for path in sorted(search_dir.rglob("*.json")):
        if path.name == "idempotency_keys.json":
            continue
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            if not entry.get("resolution"):  # Alleen onopgeloste
                entries.append(entry)
        except Exception:
            pass

    return sorted(entries, key=lambda e: e.get("last_attempt", ""), reverse=True)


@router.post("/dead-letter/{dl_id}/resolve")
def resolve_dead_letter(dl_id: str, resolution: str, app_id: str | None = Query(None)):
    """Markeer een dead letter als handmatig opgelost."""
    search_dir = DEAD_LETTER_DIR / app_id if app_id else DEAD_LETTER_DIR
    for path in search_dir.rglob(f"*{dl_id}*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            entry["resolution"] = resolution
            entry["resolved_at"] = datetime.utcnow().isoformat()
            entry["resolved_by"] = "operator"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(entry, f, ensure_ascii=False, indent=2)
            return {"status": "resolved", "dl_id": dl_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=404, detail=f"Dead letter {dl_id} niet gevonden")


# ──────────────────────────────────────────────
# SINGLE COMPONENT CHECK (catch-all — MUST be last)
# ──────────────────────────────────────────────

@router.get("/{component}")
def component_health(component: str):
    """Check de gezondheid van één specifiek component (altijd verse check)."""
    try:
        component_enum = ComponentName(component)
    except ValueError:
        valid = [c.value for c in ComponentName]
        raise HTTPException(
            status_code=400,
            detail=f"Onbekend component: '{component}'. Geldige waarden: {valid}",
        )

    checker = get_health_checker()
    result = checker.check_one(component_enum)
    return result.model_dump(mode="json")
