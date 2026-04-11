"""
Apps API endpoints — app management, CRUD, en AI-analyse.
"""

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from agents import brand_memory as bm
from backend.repository.factory import get_app_repo

router = APIRouter()


# ── Request models ────────────────────────────────────────────────────

class AppCreateRequest(BaseModel):
    name: str
    url: str = ""
    description: str = ""
    target_audience: str = ""
    usp: str = ""
    niche: str = "general"
    active_channels: list[str] = ["tiktok"]


class AppUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    description: str | None = None
    target_audience: str | None = None
    usp: str | None = None
    niche: str | None = None
    active_channels: list[str] | None = None
    active: bool | None = None


class AnalyzeURLRequest(BaseModel):
    url: str = ""


# ── Helpers ───────────────────────────────────────────────────────────

def _repo():
    return get_app_repo(tenant_id="default")


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/")
def list_apps():
    """Lijst alle geregistreerde apps."""
    return _repo().list_apps()


@router.post("/analyze-url")
def analyze_url_standalone(req: AnalyzeURLRequest):
    """
    Analyseer een URL zonder bestaande app — voor preview voordat je een app toevoegt.
    Moet VOOR /{app_id} routes staan om route-conflict te voorkomen.
    """
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="URL is verplicht")

    try:
        from agents.url_analyzer import URLAnalyzerAgent
        agent = URLAnalyzerAgent()
        result = agent.run(url=req.url)
        return result
    except Exception as e:
        logger.error(f"[Apps] Standalone URL analyse mislukt: {e}")
        raise HTTPException(status_code=500, detail=f"Analyse mislukt: {str(e)}")


@router.post("/")
def create_app(req: AppCreateRequest):
    """Registreer een nieuwe app met optionele URL voor AI-analyse."""
    app_id = f"app_{uuid.uuid4().hex[:8]}"
    new_app = {
        "id": app_id,
        "name": req.name,
        "url": req.url,
        "description": req.description,
        "target_audience": req.target_audience,
        "usp": req.usp,
        "niche": req.niche,
        "active_channels": req.active_channels,
        "active": True,
        "brand_memory_file": f"data/brand_memory/{app_id}.json",
        "created_at": datetime.utcnow().isoformat(),
    }
    _repo().save_app(new_app)
    logger.info(f"[Apps] Nieuwe app aangemaakt: {app_id} ({req.name})")
    return new_app


@router.get("/{app_id}")
def get_app(app_id: str):
    """Haal een specifieke app op."""
    app = _repo().get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} niet gevonden")
    return app


@router.put("/{app_id}")
def update_app(app_id: str, req: AppUpdateRequest):
    """Werk een bestaande app bij."""
    repo = _repo()
    app = repo.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} niet gevonden")
    for key, val in req.model_dump(exclude_none=True).items():
        app[key] = val
    repo.save_app(app)
    logger.info(f"[Apps] App bijgewerkt: {app_id}")
    return app


@router.delete("/{app_id}")
def delete_app(app_id: str):
    """Verwijder een app uit het register."""
    repo = _repo()
    if not repo.delete_app(app_id):
        raise HTTPException(status_code=404, detail=f"App {app_id} niet gevonden")
    repo.delete_brand_memory(app_id)
    logger.info(f"[Apps] App verwijderd: {app_id}")
    return {"deleted": app_id}


@router.get("/{app_id}/brand-memory")
def get_brand_memory(app_id: str):
    """Haal brand memory op voor een app."""
    memory = bm.load(app_id)
    if not memory:
        raise HTTPException(status_code=404, detail=f"Brand memory voor {app_id} niet gevonden")
    return memory


@router.patch("/{app_id}/brand-memory")
def update_brand_memory(app_id: str, updates: dict):
    """Werk brand memory bij voor een app."""
    return bm.apply_updates(app_id, updates)


@router.post("/{app_id}/analyze")
def analyze_app_url(app_id: str, req: AnalyzeURLRequest | None = None):
    """
    Analyseer de app-URL met AI. Vult automatisch beschrijving, doelgroep, USP en niche in.
    Gebruikt de URL uit het request of de opgeslagen URL van de app.
    """
    repo = _repo()
    app = repo.get_app(app_id)
    if not app:
        raise HTTPException(status_code=404, detail=f"App {app_id} niet gevonden")

    url = (req.url if req and req.url else app.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="Geen URL opgegeven en geen URL opgeslagen voor deze app")

    try:
        from agents.url_analyzer import URLAnalyzerAgent
        agent = URLAnalyzerAgent()
        result = agent.run(url=url, existing_info={"name": app.get("name"), "description": app.get("description")})
    except Exception as e:
        logger.error(f"[Apps] URL analyse mislukt voor {app_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Analyse mislukt: {str(e)}")

    # Update app met AI-resultaten (overschrijf alleen lege velden, of alles als force)
    update_fields = {}
    if result.get("name") and not app.get("name"):
        update_fields["name"] = result["name"]
    if result.get("description"):
        update_fields["description"] = result["description"]
    if result.get("target_audience"):
        update_fields["target_audience"] = result["target_audience"]
    if result.get("usp"):
        update_fields["usp"] = result["usp"]
    if result.get("niche") and result["niche"] != "general":
        update_fields["niche"] = result["niche"]
    if not app.get("url"):
        update_fields["url"] = url

    # Extra velden opslaan als metadata
    if result.get("features"):
        update_fields["features"] = result["features"]
    if result.get("tone"):
        update_fields["tone"] = result["tone"]
    if result.get("content_angles"):
        update_fields["content_angles"] = result["content_angles"]

    for key, val in update_fields.items():
        app[key] = val
    repo.save_app(app)

    logger.info(f"[Apps] App {app_id} geanalyseerd — {len(update_fields)} velden bijgewerkt")
    return {"app": app, "analysis": result, "fields_updated": list(update_fields.keys())}


@router.get("/{app_id}/learnings")
def get_app_learnings(app_id: str):
    """
    Haal de volledige LearningStore op voor een app.
    Bevat alle geleerde patronen met confidence-niveaus en categorieën.
    """
    from analytics.metrics_store import MetricsStore
    from workflows.feedback_loop import get_learning_summary
    ms = MetricsStore()
    try:
        store = ms.load_learning_store(app_id)
        benchmark = ms.load_benchmark(app_id)
        active = store.active_learnings()
        summary = get_learning_summary(app_id)
    except Exception:
        return {"app_id": app_id, "learnings": [], "summary": {}, "benchmark": {}}

    return {
        "app_id": app_id,
        "total_posts_analyzed": store.total_posts_analyzed,
        "active_learnings_count": len(active),
        "learnings": [
            {
                "id": l.id,
                "category": l.category,
                "type": l.type,
                "finding": l.finding,
                "action": l.action,
                "confidence": l.confidence.value,
                "times_confirmed": l.times_confirmed,
                "sample_size": l.sample_size,
                "created_at": l.created_at.isoformat() if hasattr(l.created_at, 'isoformat') else str(l.created_at),
                "last_confirmed_at": l.last_confirmed_at.isoformat() if hasattr(l.last_confirmed_at, 'isoformat') else str(l.last_confirmed_at),
                "evidence": l.evidence,
            }
            for l in sorted(active, key=lambda x: x.times_confirmed, reverse=True)
        ],
        "benchmark": {
            "avg_score": round(benchmark.avg_composite_score, 1),
            "avg_views": int(benchmark.avg_views),
            "total_posts": benchmark.total_posts,
            "best_score": round(benchmark.best_score, 1),
        },
        "summary": summary,
    }


@router.post("/{app_id}/run-analysis")
def run_app_analysis(app_id: str):
    """
    Voer een handmatige wekelijkse analyse uit voor een app.
    Analyseert alle beschikbare post-data en genereert/updatet learnings.
    """
    try:
        from workflows.feedback_loop import run_weekly_analysis
        result = run_weekly_analysis(app_id)
        return {
            "app_id": app_id,
            "status": "completed",
            "new_learnings": result.get("new_learnings", 0),
            "posts_analyzed": result.get("posts_analyzed", 0),
            "result": result,
        }
    except Exception as e:
        from loguru import logger
        logger.warning(f"[Apps] Analyse mislukt voor {app_id}: {e}")
        return {"app_id": app_id, "status": "no_data", "new_learnings": 0, "message": str(e)}


@router.get("/{app_id}/insights")
def get_app_insights(app_id: str):
    """
    Haal learning insights en brand memory samenvatting op voor een app.
    Geoptimaliseerd voor het Insights-dashboard.
    """
    memory = bm.load(app_id)
    app = _repo().get_app(app_id)

    insights = memory.get("learned_insights", [])
    top_hooks = memory.get("top_performing_hooks", [])
    avoided = memory.get("avoided_topics", [])
    perf = memory.get("performance_history", {})
    content_fmts = memory.get("content_formats", {})

    return {
        "app_id": app_id,
        "app_name": (app or {}).get("name", app_id),
        "niche": (app or {}).get("niche", ""),
        "insights": insights,
        "insights_count": len(insights),
        "top_hooks": top_hooks,
        "avoided_topics": avoided,
        "best_format": perf.get("best_post_type", content_fmts.get("best_performing", "")),
        "optimal_post_time": perf.get("optimal_post_time", ""),
        "tone_of_voice": memory.get("tone_of_voice", ""),
        "last_updated": memory.get("last_updated", ""),
    }


@router.get("/{app_id}/content")
def get_app_content(app_id: str):
    """
    Haal alle content op voor een app — campagnes met idee, script, caption, video status.
    Geoptimaliseerd voor het Content-overzicht in het dashboard.
    """
    from backend.repository.factory import get_campaign_repo

    repo = get_campaign_repo(tenant_id="default")
    bundles = repo.list(tenant_id="default", app_id=app_id)

    content_items = []
    for b in bundles:
        idea_title = b.idea.get("title", "") if b.idea else ""
        # hook_options is een lijst; fallback naar hook veld
        hook_options = b.idea.get("hook_options", []) if b.idea else []
        idea_hook = (hook_options[0] if hook_options else b.idea.get("hook", "")) if b.idea else ""
        script_scenes = b.script.get("scenes", []) if b.script else []
        # caption zit genest in caption_options lijst
        caption_options = b.caption.get("caption_options", []) if b.caption else []
        caption_text = caption_options[0].get("caption", "") if caption_options else (b.caption.get("caption", "") if b.caption else "")
        raw_hashtags = b.caption.get("hashtags", {}) if b.caption else {}
        if isinstance(raw_hashtags, dict):
            # Hashtags als dict met primary/secondary/etc
            hashtags = raw_hashtags.get("primary", [])[:5]
        elif isinstance(raw_hashtags, (list, tuple)):
            hashtags = list(raw_hashtags)[:5]
        else:
            hashtags = []

        content_items.append({
            "campaign_id": b.id,
            "status": b.status,
            "platform": b.platform,
            "idea_title": idea_title,
            "idea_hook": idea_hook,
            "script_scene_count": len(script_scenes),
            "script_preview": script_scenes[0].get("voiceover", "")[:120] if script_scenes else "",
            "caption_preview": caption_text[:150],
            "hashtags": hashtags[:5],
            "video_path": b.video_path,
            "thumbnail_path": b.thumbnail_path,
            "has_video": b.video_path is not None,
            "post_type": getattr(b, "post_type", "video"),
            "experiment_id": b.experiment_id,
            "total_cost_usd": b.total_cost_usd,
            "created_at": str(b.created_at),
            "published_at": str(b.published_at) if b.published_at else None,
        })

    return {
        "app_id": app_id,
        "total": len(content_items),
        "content": content_items,
    }
