"""
Experiments API — endpoints voor experiment lifecycle beheer.

Routes:
  GET  /api/experiments/                        — lijst per app_id
  GET  /api/experiments/{id}                    — detail
  POST /api/experiments/{id}/select-variant     — operator keuze
  GET  /api/experiments/{id}/comparison         — post-publish vergelijking
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.services.experiment_service import ExperimentService
from experiments.experiment_store import ExperimentStore
from experiments.models import ExperimentStatus

router = APIRouter()


# ── Request bodies ────────────────────────────────────────────────────

class SelectVariantRequest(BaseModel):
    variant_id: str
    approved_by: str = "operator"
    tenant_id: str = "default"


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/")
def list_experiments(
    app_id: str = Query(..., description="App ID"),
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Geeft alle experimenten voor een app terug (meest recent eerst)."""
    store = ExperimentStore(tenant_id=tenant_id)
    experiments = store.list_by_app(app_id)
    return {
        "app_id": app_id,
        "tenant_id": tenant_id,
        "total": len(experiments),
        "experiments": [e.model_dump(mode="json") for e in experiments],
    }


@router.get("/pending")
def list_pending(
    app_id: str = Query(..., description="App ID"),
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Geeft experimenten terug die wachten op operator goedkeuring."""
    svc = ExperimentService(tenant_id=tenant_id)
    pending = svc.get_pending_for_approval(app_id)
    return {"app_id": app_id, "tenant_id": tenant_id, "total": len(pending), "experiments": pending}


@router.get("/{experiment_id}")
def get_experiment(
    experiment_id: str,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Geeft volledige experimentdetails inclusief alle varianten."""
    exp = ExperimentStore(tenant_id=tenant_id).load(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} niet gevonden")
    return exp.model_dump(mode="json")


@router.post("/{experiment_id}/select-variant")
def select_variant(experiment_id: str, body: SelectVariantRequest):
    """
    Registreert de operator-keuze voor een variant.
    Zet experiment status op SELECTED.
    """
    try:
        svc = ExperimentService(tenant_id=body.tenant_id)
        result = svc.select_variant(
            experiment_id=experiment_id,
            variant_id=body.variant_id,
            approved_by=body.approved_by,
        )
        return {"status": "selected", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{experiment_id}/comparison")
def get_comparison(
    experiment_id: str,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """
    Geeft post-publish vergelijking terug (winnaar, scores, causal confidence).
    Alleen beschikbaar als experiment CONCLUDED of INCONCLUSIVE is.
    """
    exp = ExperimentStore(tenant_id=tenant_id).load(experiment_id)
    if not exp:
        raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} niet gevonden")

    if exp.status not in (ExperimentStatus.CONCLUDED, ExperimentStatus.INCONCLUSIVE):
        raise HTTPException(
            status_code=409,
            detail=f"Experiment heeft status '{exp.status.value}' — vergelijking nog niet beschikbaar",
        )

    return {
        "experiment_id": experiment_id,
        "status": exp.status.value,
        "dimension": exp.hypothesis.dimension.value,
        "winning_variant_id": exp.winning_variant_id,
        "causal_confidence": exp.causal_confidence,
        "conclusion": exp.conclusion,
        "concluded_at": exp.concluded_at.isoformat() if exp.concluded_at else None,
        "variants": [
            {
                "variant_id": v.variant_id,
                "label": v.label,
                "dimension_value": v.spec.dimension_value,
                "performance_score": v.performance_score,
                "view_count": v.view_count,
                "tiktok_post_id": v.tiktok_post_id,
            }
            for v in exp.variants
        ],
    }
