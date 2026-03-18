"""
Campaign API endpoints — multi-tenant ready.
"""

import asyncio
import json
import os
import threading
import time
import traceback
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from backend.models.campaign import CampaignBundle, CampaignStatus  # noqa: F401 — ook gebruikt in run()
from backend.repository.file_campaigns import FileCampaignRepository
from workflows.campaign_pipeline import run_pipeline, load_app
from video_engine.providers.pro_video_provider import ProVideoProvider

from agents.idea_generator import IdeaGeneratorAgent
from agents import brand_memory as bm

router = APIRouter()

# ── Input sanitization (path traversal preventie) ────────────────────
import re as _re
_SAFE_ID = _re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')

def _safe_id(value: str, field: str = "id") -> str:
    """Saniteer een ID — voorkomt path traversal via ../."""
    if not value or not _SAFE_ID.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Ongeldig {field}: alleen letters, cijfers, _ en - toegestaan (max 64).",
        )
    return value


def _assert_app_belongs_to_tenant(app_id: str, tenant_id: str) -> None:
    """
    Valideer dat app_id toebehoort aan tenant_id.

    In development-modus (ENVIRONMENT=development) wordt validatie overgeslagen
    zodat lokaal testen eenvoudig blijft. In productie wordt altijd gevalideerd.
    """
    if os.getenv("ENVIRONMENT", "development").lower() == "development":
        return  # Auth is uitgeschakeld in dev — skip validatie

    try:
        app = load_app(app_id)
        app_tenant = app.get("tenant_id", "default")
        if app_tenant != tenant_id:
            logger.warning(
                f"[Auth] Tenant '{tenant_id}' probeert toegang tot app '{app_id}' "
                f"van tenant '{app_tenant}'"
            )
            raise HTTPException(
                status_code=403,
                detail=f"App '{app_id}' is niet toegankelijk voor deze tenant.",
            )
    except HTTPException:
        raise
    except Exception:
        # App niet gevonden — laat load_app de juiste fout geven bij aanroep
        pass

# ── SSE progress tracking ────────────────────────────────────────────
# campaign_id -> list of messages (thread-safe via lock)
_progress_store: dict[str, list[str]] = {}
_progress_timestamps: dict[str, float] = {}  # campaign_id -> aanmaaktijd (monotonic)
_progress_lock = threading.Lock()
_PROGRESS_TTL_SEC = 1800  # 30 minuten — campagnes zonder SSE-verbinding worden opgeruimd


def _start_progress_cleanup() -> None:
    """Achtergrondthread: ruim stale progress-entries op (campagnes zonder SSE-verbinding)."""
    def _cleanup_loop():
        while True:
            time.sleep(300)  # Elke 5 minuten controleren
            cutoff = time.monotonic() - _PROGRESS_TTL_SEC
            with _progress_lock:
                stale = [cid for cid, ts in _progress_timestamps.items() if ts < cutoff]
                for cid in stale:
                    _progress_store.pop(cid, None)
                    _progress_timestamps.pop(cid, None)
            if stale:
                logger.debug(f"[Campaigns] Progress store opgeruimd: {len(stale)} verlopen entries verwijderd")

    t = threading.Thread(target=_cleanup_loop, daemon=True, name="progress-cleanup")
    t.start()


_start_progress_cleanup()


class GenerateIdeasRequest(BaseModel):
    app_id: str
    platform: str = "tiktok"


class StartCampaignRequest(BaseModel):
    app_id: str
    platform: str = "tiktok"
    idea_index: int = 0
    tenant_id: str = "default"
    voice: str = "roos"       # ElevenLabs default — native NL vrouwelijk
    tts_speed: float = 1.0
    voice_stability: float = 0.58      # 0.0-1.0 — hoger = consistenter
    voice_similarity: float = 0.92     # 0.0-1.0 — hoger = dichter bij originele stem
    voice_style: float = 0.45          # 0.0-1.0 — hoger = meer expressief
    chosen_idea: dict | None = None  # Pre-gekozen idee (skip idee-generatie)


class VoicePreviewRequest(BaseModel):
    voice: str = "nova"
    speed: float = 1.0
    text: str = "Hallo! Dit is een test van de stemkeuze."
    stability: float = 0.58
    similarity_boost: float = 0.92
    style: float = 0.45


class CampaignResponse(BaseModel):
    id: str
    app_id: str
    status: str
    total_cost_usd: float
    idea_title: str | None = None
    video_path: str | None = None
    viral_score: dict | None = None
    created_at: str
    # Extra velden voor uitgebreide tabel
    platform: str = "tiktok"
    video_type: str | None = None
    duration_sec: int | None = None
    published_at: str | None = None
    approved_at: str | None = None
    has_video: bool = False
    display_name: str | None = None


def _to_response(bundle: CampaignBundle) -> CampaignResponse:
    # Video type uit script
    video_type = None
    duration_sec = None
    if bundle.script:
        video_type = bundle.script.get("video_type")
        duration_sec = bundle.script.get("total_duration_sec")

    return CampaignResponse(
        id=bundle.id,
        app_id=bundle.app_id,
        status=bundle.status,
        total_cost_usd=bundle.total_cost_usd,
        idea_title=bundle.idea.get("title") if bundle.idea else None,
        video_path=bundle.video_path,
        viral_score=bundle.viral_score,
        created_at=str(bundle.created_at),
        platform=bundle.platform,
        video_type=video_type,
        duration_sec=duration_sec,
        published_at=str(bundle.published_at) if bundle.published_at else None,
        approved_at=str(bundle.approved_at) if bundle.approved_at else None,
        has_video=bool(bundle.video_path),
        display_name=getattr(bundle, 'display_name', None),
    )


@router.post("/generate-ideas")
def generate_ideas(req: GenerateIdeasRequest):
    """Genereer 5 campagne-ideeën voor een app (zonder pipeline te starten)."""
    _assert_app_belongs_to_tenant(req.app_id, "default")  # tenant uit auth token in productie
    app = load_app(req.app_id)
    memory = bm.load(req.app_id)
    memory["niche"] = memory.get("niche") or app.get("niche", "")
    memory["url"] = memory.get("url") or app.get("url", "")
    memory["app_name"] = memory.get("app_name") or app.get("name", "")

    agent = IdeaGeneratorAgent()
    ideas = agent.run(app=app, memory=memory, platform=req.platform)

    if not ideas:
        raise HTTPException(status_code=500, detail="Geen ideeën gegenereerd")

    # Voeg leesbare labels toe
    GOAL_LABELS = {
        "awareness": "Bereik vergroten",
        "consideration": "Interesse wekken",
        "conversie": "Actie stimuleren",
    }
    FORMAT_LABELS = {
        "problem-solution": "Probleem & Oplossing",
        "before-after": "Voor & Na",
        "tutorial": "Uitleg / Tutorial",
        "social-proof": "Social Proof",
        "trend": "Trending Format",
    }

    for idea in ideas:
        idea["goal_label"] = GOAL_LABELS.get(idea.get("goal", ""), idea.get("goal", ""))
        idea["format_label"] = FORMAT_LABELS.get(idea.get("content_format", ""), idea.get("content_format", ""))

    return {"ideas": ideas, "cost_usd": agent.total_cost_usd}


@router.post("/test-script")
def test_script_quality(req: StartCampaignRequest):
    """
    Snel script-kwaliteitstest: idee genereren + script schrijven + viral check.
    Geen video productie. Resultaat in ~60-90 seconden (vs 3-5 min voor volledige pipeline).
    """
    from agents.script_writer import ScriptWriterAgent
    from agents.viral_checker import ViralCheckerAgent
    from workflows.campaign_pipeline import _score_and_pick_idea, _determine_video_type, _enforce_echo_loop
    from backend.constants import PIPELINE_DEFAULT_DURATION_SEC

    _assert_app_belongs_to_tenant(req.app_id, "default")
    app = load_app(req.app_id)
    memory = bm.load(req.app_id)
    memory["niche"] = memory.get("niche") or app.get("niche", "")
    memory["url"] = memory.get("url") or app.get("url", "")
    memory["app_name"] = memory.get("app_name") or app.get("name", "")

    total_cost = 0.0
    results = []

    if req.chosen_idea:
        idea = req.chosen_idea
    else:
        idea_agent = IdeaGeneratorAgent()
        ideas = idea_agent.run(app=app, memory=memory, platform=req.platform)
        total_cost += idea_agent.total_cost_usd
        if not ideas or not isinstance(ideas, list):
            raise HTTPException(status_code=500, detail="Geen ideeën gegenereerd")
        ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
        idea = _score_and_pick_idea(ideas, req.idea_index, memory)

    script_agent = ScriptWriterAgent()
    viral_agent = ViralCheckerAgent()

    # Eerste poging
    script = script_agent.run(
        idea=idea, app=app, memory=memory, platform=req.platform,
        target_duration_sec=PIPELINE_DEFAULT_DURATION_SEC,
        video_type=_determine_video_type(idea),
    )
    total_cost += script_agent.total_cost_usd
    script = _enforce_echo_loop(script)
    viral = viral_agent.run(script=script, app=app, memory=memory)
    total_cost += viral_agent.total_cost_usd
    best_score = viral.get("composite_score", 0)
    results.append({"attempt": 1, "score": best_score, "verdict": viral.get("verdict")})

    # Max 2 extra rewrites (snelheidsoptimalisatie: 1 minder dan volledige pipeline)
    for attempt in range(2, 4):
        if best_score >= 88:
            break
        ri = viral.get("rewrite_instructions", {})
        extra_parts = []
        if ri.get("hook_alternatives"):
            extra_parts.append("GEBRUIK EEN VAN DEZE HOOKS:\n" + "\n".join(f"- {h}" for h in ri["hook_alternatives"][:3]))
        if ri.get("share_trigger_add"):
            extra_parts.append(f"SHARE TRIGGER: {ri['share_trigger_add']}")
        if ri.get("loop_fix"):
            extra_parts.append(f"LOOP FIX: {ri['loop_fix']}")
        weak = [k for k, v in viral.get("scores", {}).items() if v < 80]
        if weak:
            extra_parts.append(f"VERBETER DIT: {', '.join(weak)}")

        script_agent2 = ScriptWriterAgent()
        script2 = script_agent2.run(
            idea=idea, app=app, memory=memory, platform=req.platform,
            target_duration_sec=PIPELINE_DEFAULT_DURATION_SEC,
            video_type=_determine_video_type(idea),
            extra_instruction="\n".join(extra_parts),
        )
        total_cost += script_agent2.total_cost_usd
        script2 = _enforce_echo_loop(script2)
        viral2 = viral_agent.run(script=script2, app=app, memory=memory)
        total_cost += viral_agent.total_cost_usd
        score2 = viral2.get("composite_score", 0)
        results.append({"attempt": attempt, "score": score2, "verdict": viral2.get("verdict")})
        if score2 > best_score:
            best_score = score2
            viral = viral2
            script = script2

    return {
        "idea_title": idea.get("title"),
        "best_score": best_score,
        "verdict": viral.get("verdict"),
        "scores": viral.get("scores", {}),
        "summary": viral.get("summary"),
        "strengths": viral.get("strengths", []),
        "weaknesses": viral.get("weaknesses", []),
        "hook": script.get("scenes", [{}])[0].get("voiceover", "") if script.get("scenes") else "",
        "last_line": script.get("scenes", [{}])[-1].get("voiceover", "") if script.get("scenes") else "",
        "attempts": results,
        "total_cost_usd": round(total_cost, 4),
    }


@router.post("/start", response_model=CampaignResponse)
def start_campaign(req: StartCampaignRequest, background_tasks: BackgroundTasks):
    """Start een nieuwe campagne-pipeline voor een app."""
    _assert_app_belongs_to_tenant(req.app_id, req.tenant_id)
    bundle = CampaignBundle(
        app_id=req.app_id,
        tenant_id=req.tenant_id,
        platform=req.platform,
        status=CampaignStatus.GENERATING,
    )
    FileCampaignRepository(tenant_id=req.tenant_id).save(bundle)

    campaign_id = bundle.id

    # Init progress store (inclusief aanmaaktijdstempel voor TTL-cleanup)
    with _progress_lock:
        _progress_store[campaign_id] = []
        _progress_timestamps[campaign_id] = time.monotonic()

    def on_progress(msg: str):
        with _progress_lock:
            if campaign_id in _progress_store:
                _progress_store[campaign_id].append(msg)

    def run():
        try:
            run_pipeline(
                app_id=req.app_id,
                platform=req.platform,
                idea_index=req.idea_index,
                tenant_id=req.tenant_id,
                voice=req.voice,
                tts_speed=req.tts_speed,
                voice_settings={
                    "stability": req.voice_stability,
                    "similarity_boost": req.voice_similarity,
                    "style": req.voice_style,
                },
                on_progress=on_progress,
                chosen_idea=req.chosen_idea,
                campaign_id=campaign_id,
            )
            on_progress("__DONE__")
        except Exception as e:
            tb = traceback.format_exc()
            logger.error(
                f"[Campaigns] Pipeline CRASH ({req.app_id}, campaign={campaign_id}, "
                f"tenant={req.tenant_id}):\n{tb}"
            )
            # Markeer campagne als mislukt in de repository
            try:
                repo = FileCampaignRepository(tenant_id=req.tenant_id)
                failed_bundle = repo.get(campaign_id, req.tenant_id)
                if failed_bundle and failed_bundle.status == CampaignStatus.GENERATING:
                    failed_bundle.status = CampaignStatus.FAILED
                    repo.save(failed_bundle)
                    logger.info(f"[Campaigns] Campagne {campaign_id} gemarkeerd als FAILED")
            except Exception as save_err:
                logger.error(f"[Campaigns] Kon FAILED status niet opslaan: {save_err}")
            on_progress(f"__ERROR__:{e}")

    background_tasks.add_task(run)
    return _to_response(bundle)


@router.get("/pending", response_model=list[CampaignResponse])
def get_pending(
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Haal alle campagnes op die wachten op goedkeuring."""
    repo = FileCampaignRepository(tenant_id=tenant_id)
    return [_to_response(b) for b in repo.list_pending(tenant_id)]


# ── Voice endpoints (VÓÓR /{campaign_id} om route conflict te voorkomen) ──

@router.get("/voices/list")
def list_voices():
    """Lijst beschikbare stemmen, ElevenLabs eerst (beter NL)."""
    el_keys = set(ProVideoProvider.ELEVENLABS_VOICES.keys())
    voices = []
    for k, v in ProVideoProvider.VOICES.items():
        voices.append({
            "id": k,
            "name": k.capitalize(),
            "description": v,
            "provider": "elevenlabs" if k in el_keys else "openai",
        })
    return {
        "voices": voices,
        "default": "aria",  # ElevenLabs warm vrouwelijk als default
    }


@router.post("/voices/preview")
def preview_voice(req: VoicePreviewRequest):
    """Genereer een kort audio-fragment om een stem te testen."""
    preview_dir = Path(__file__).parent.parent.parent / "assets" / "generated" / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    # Unieke cache-key op basis van alle voice settings
    cache_key = f"preview_{req.voice}_{req.speed}_{req.stability}_{req.similarity_boost}_{req.style}"
    audio_path = preview_dir / f"{cache_key}.mp3"

    # Cache: als preview al bestaat, niet opnieuw genereren
    if audio_path.exists():
        return FileResponse(str(audio_path), media_type="audio/mpeg", filename=f"preview_{req.voice}.mp3")

    # ElevenLabs preview
    el_voice = ProVideoProvider.ELEVENLABS_VOICES.get(req.voice)
    el_key = os.getenv("ELEVENLABS_API_KEY", "")

    if el_voice and el_key and len(el_key) >= 10:
        try:
            import httpx
            resp = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{el_voice['id']}",
                headers={
                    "xi-api-key": el_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": req.text[:200],
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": req.stability,
                        "similarity_boost": req.similarity_boost,
                        "style": req.style,
                        "use_speaker_boost": True,
                    },
                },
                timeout=15,
            )
            resp.raise_for_status()
            audio_path.write_bytes(resp.content)
            return FileResponse(str(audio_path), media_type="audio/mpeg", filename=f"preview_{req.voice}.mp3")
        except Exception as e:
            logger.warning(f"[Voices] ElevenLabs preview mislukt: {e}")

    # OpenAI fallback
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise HTTPException(status_code=500, detail="Geen TTS API key geconfigureerd (ELEVENLABS of OPENAI)")

    try:
        import openai
        client = openai.OpenAI(api_key=openai_key)
        # Map ElevenLabs voice naar OpenAI equivalent
        oa_voice = req.voice
        if req.voice in ProVideoProvider.ELEVENLABS_VOICES:
            desc = ProVideoProvider.ELEVENLABS_VOICES[req.voice].get("desc", "").lower()
            oa_voice = "nova" if "vrouwelijk" in desc else "onyx"

        response = client.audio.speech.create(
            model="tts-1-hd",
            voice=oa_voice,
            input=req.text[:200],
            response_format="mp3",
            speed=max(0.5, min(req.speed, 2.0)),
        )
        response.stream_to_file(str(audio_path))
        return FileResponse(str(audio_path), media_type="audio/mpeg", filename=f"preview_{req.voice}.mp3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS preview mislukt: {str(e)}")


# ── SSE progress stream ──

@router.get("/progress/{campaign_id}")
async def stream_progress(campaign_id: str):
    """SSE stream voor live pipeline voortgang."""
    async def event_generator():
        seen = 0
        stale_count = 0
        while True:
            with _progress_lock:
                messages = _progress_store.get(campaign_id, [])
                new_msgs = messages[seen:]
                seen = len(messages)

            for msg in new_msgs:
                stale_count = 0
                if msg == "__DONE__":
                    yield {"event": "done", "data": json.dumps({"status": "completed"})}
                    # Cleanup
                    with _progress_lock:
                        _progress_store.pop(campaign_id, None)
                    return
                elif msg.startswith("__ERROR__:"):
                    yield {"event": "error", "data": json.dumps({"error": msg[10:]})}
                    with _progress_lock:
                        _progress_store.pop(campaign_id, None)
                    return
                else:
                    yield {"event": "progress", "data": json.dumps({"message": msg})}

            if not new_msgs:
                stale_count += 1
                # Timeout na 5 minuten inactiviteit
                if stale_count > 300:
                    yield {"event": "timeout", "data": json.dumps({"status": "timeout"})}
                    with _progress_lock:
                        _progress_store.pop(campaign_id, None)
                    return

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


# ── Campaign detail/list routes ──

@router.get("/")
def list_campaigns(
    status: str | None = None,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Lijst alle campagnes, optioneel gefilterd op status."""
    repo = FileCampaignRepository(tenant_id=tenant_id)
    return [_to_response(b) for b in repo.list(tenant_id, status=status)]


@router.post("/{campaign_id}/publish")
def publish_campaign(
    campaign_id: str,
    body: dict | None = None,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Publiceer een goedgekeurde campagne naar TikTok."""
    _safe_id(campaign_id, "campaign_id")
    repo = FileCampaignRepository(tenant_id=tenant_id)
    bundle = repo.get(campaign_id, tenant_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Campagne {campaign_id} niet gevonden")
    if bundle.status != CampaignStatus.APPROVED:
        raise HTTPException(status_code=400, detail=f"Campagne moet status 'approved' hebben (nu: {bundle.status})")
    if not bundle.video_path:
        raise HTTPException(status_code=400, detail="Campagne heeft geen video")

    from channels.tiktok.publisher import publish_to_tiktok
    try:
        result = publish_to_tiktok(bundle)
        bundle.status = CampaignStatus.PUBLISHING
        repo.save(bundle)
        return {"status": "publishing", "campaign_id": campaign_id, "result": result}
    except Exception as e:
        logger.error(f"[Campaigns] Publicatie mislukt: {e}")
        raise HTTPException(status_code=500, detail=f"Publicatie mislukt: {str(e)}")


@router.post("/{campaign_id}/regenerate-video")
def regenerate_video(
    campaign_id: str,
    background_tasks: BackgroundTasks,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Genereer de video opnieuw voor een bestaande campagne (behoudt script & idee)."""
    _safe_id(campaign_id, "campaign_id")
    repo = FileCampaignRepository(tenant_id=tenant_id)
    bundle = repo.get(campaign_id, tenant_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Campagne {campaign_id} niet gevonden")
    if not bundle.script or not bundle.script.get("scenes"):
        raise HTTPException(status_code=400, detail="Campagne heeft geen script — regenereren niet mogelijk")

    # Reset status naar generating
    bundle.status = CampaignStatus.GENERATING
    bundle.video_path = None
    repo.save(bundle)

    # Init progress store
    with _progress_lock:
        _progress_store[campaign_id] = []
        _progress_timestamps[campaign_id] = time.monotonic()

    def on_progress(msg: str):
        with _progress_lock:
            if campaign_id in _progress_store:
                _progress_store[campaign_id].append(msg)

    def run():
        try:
            on_progress("[1/3] Video opnieuw genereren...")
            voice = "roos"
            tts_speed = 1.0
            voice_settings = None

            # Probeer voice settings uit de oorspronkelijke campagne te halen
            script = bundle.script
            memory = bm.load(bundle.app_id)
            app = load_app(bundle.app_id)
            memory["niche"] = memory.get("niche") or app.get("niche", "")
            memory["url"] = memory.get("url") or app.get("url", "")
            memory["app_name"] = memory.get("app_name") or app.get("name", "")

            on_progress("[2/3] Video produceren met ProVideoProvider...")
            from video_engine.orchestrator import VideoOrchestrator
            video_engine = VideoOrchestrator(
                voice=voice, tts_speed=tts_speed,
                voice_settings=voice_settings,
            )
            video_path = video_engine.produce(script, memory, bundle.app_id)

            if video_path:
                updated = repo.get(campaign_id, tenant_id)
                if updated:
                    updated.video_path = str(video_path)
                    updated.total_cost_usd += video_engine.total_cost_usd
                    updated.status = CampaignStatus.PENDING_APPROVAL
                    repo.save(updated)

            on_progress("[3/3] Video regeneratie voltooid!")
            on_progress("__DONE__")
        except Exception as e:
            logger.error(f"[Campaigns] Video regeneratie mislukt ({campaign_id}): {e}")
            try:
                failed = repo.get(campaign_id, tenant_id)
                if failed:
                    failed.status = CampaignStatus.FAILED
                    repo.save(failed)
            except Exception:
                pass
            on_progress(f"__ERROR__:{e}")

    background_tasks.add_task(run)
    return _to_response(bundle)


@router.get("/{campaign_id}", response_model=CampaignBundle)
def get_campaign(
    campaign_id: str,
    tenant_id: str = Query("default", description="Tenant identifier"),
):
    """Haal details op van een specifieke campagne."""
    _safe_id(campaign_id, "campaign_id")
    _safe_id(tenant_id, "tenant_id")
    bundle = FileCampaignRepository(tenant_id=tenant_id).get(campaign_id, tenant_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Campagne {campaign_id} niet gevonden")
    return bundle


# ── Universele Scene Visual Generator ─────────────────────────

def _generate_scene_visuals(script_text: str, app: dict, audio_duration: float) -> list:
    """
    Genereert scene-indeling + visual queries op basis van script en app-niche.
    Werkt voor elk thema — de AI bepaalt de juiste stock footage queries.
    """
    import openai

    app_name = app.get("name", "de app")
    niche = app.get("niche", "general")
    description = app.get("description", "")

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[{
            "role": "system",
            "content": (
                "Je bent een video-regisseur die stock footage selecteert voor TikTok video's.\n\n"
                "TAAK: Analyseer het voiceover script en genereer 4 scenes met passende "
                "stock footage zoektermen (in het Engels, voor Pexels/Pixabay).\n\n"
                "REGELS VOOR VISUAL QUERIES:\n"
                "- Elke query is 3-5 woorden Engels, geschikt voor stock video sites\n"
                "- Queries moeten CONCREET zijn (niet abstract)\n"
                "- Scenes moeten visueel op elkaar aansluiten (zelfde sfeer/setting)\n"
                "- Hook + Problem: negatieve emotie passend bij het probleem\n"
                "- Solution + CTA: positieve emotie passend bij de oplossing\n"
                "- Gebruik NOOIT merknamen in queries\n"
                "- Denk aan wat er ECHT bestaat op stock footage sites\n\n"
                "REGELS VOOR ON_SCREEN_TEXT:\n"
                "- Max 4 woorden per scene, in het Nederlands\n"
                "- Gebruik getallen/cijfers als die in het script staan\n"
                "- Hook: het shocking getal\n"
                "- Problem: de doorberekening\n"
                "- Solution: het resultaat/verschil\n"
                "- CTA: call to action tekst\n\n"
                f"APP: {app_name}\n"
                f"NICHE: {niche}\n"
                f"BESCHRIJVING: {description}\n\n"
                "ANTWOORD als JSON:\n"
                '{"scenes": [\n'
                '  {"type": "hook", "visual_search_query": "...", "on_screen_text": "...", "duration_pct": 0.15},\n'
                '  {"type": "problem", "visual_search_query": "...", "on_screen_text": "...", "duration_pct": 0.25},\n'
                '  {"type": "solution", "visual_search_query": "...", "on_screen_text": "...", "duration_pct": 0.40},\n'
                '  {"type": "cta", "visual_search_query": "...", "on_screen_text": "...", "duration_pct": 0.20}\n'
                "]}"
            ),
        }, {
            "role": "user",
            "content": f"VOICEOVER SCRIPT:\n{script_text}",
        }],
    )

    import json as _json
    try:
        data = _json.loads(resp.choices[0].message.content)
        ai_scenes = data.get("scenes", [])
    except Exception:
        ai_scenes = []

    # Fallback als AI geen geldige scenes teruggeeft
    if len(ai_scenes) != 4:
        ai_scenes = [
            {"type": "hook", "visual_search_query": "person frustrated desk", "on_screen_text": "Het probleem", "duration_pct": 0.15},
            {"type": "problem", "visual_search_query": "person stressed working late", "on_screen_text": "De impact", "duration_pct": 0.25},
            {"type": "solution", "visual_search_query": "person smiling using phone", "on_screen_text": "De oplossing", "duration_pct": 0.40},
            {"type": "cta", "visual_search_query": "person excited tapping phone", "on_screen_text": "Probeer het", "duration_pct": 0.20},
        ]

    scenes = []
    for s in ai_scenes:
        scenes.append({
            "type": s.get("type", "hook"),
            "voiceover": "",
            "on_screen_text": s.get("on_screen_text", ""),
            "visual_search_query": s.get("visual_search_query", "person using phone"),
            "duration_sec": audio_duration * s.get("duration_pct", 0.25),
        })

    logger.info(f"[SceneVisuals] Gegenereerd voor '{app_name}': "
                + " | ".join(f"{s['type']}='{s['visual_search_query']}'" for s in scenes))
    return scenes


# ── Audio Upload + Video Generatie ─────────────────────────────

@router.post("/generate-with-audio")
async def generate_with_audio(
    audio: UploadFile = File(...),
    app_id: str = Form("app_76fe2fdd"),
    script_text: str = Form(""),
):
    """Genereer video met door gebruiker ingesproken audio (geen TTS)."""
    import subprocess
    import uuid

    ROOT = Path(__file__).parent.parent.parent
    ASSETS = ROOT / "assets" / "generated"
    work_dir = ASSETS / "work" / str(uuid.uuid4())[:8]
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Sla audio op
    audio_webm = work_dir / "user_audio.webm"
    audio_mp3 = work_dir / "user_audio.mp3"
    content = await audio.read()
    audio_webm.write_bytes(content)

    # 2. Converteer webm → mp3
    subprocess.run([
        "ffmpeg", "-y", "-i", str(audio_webm),
        "-c:a", "libmp3lame", "-q:a", "2",
        str(audio_mp3),
    ], capture_output=True, timeout=30)

    if not audio_mp3.exists():
        raise HTTPException(status_code=500, detail="Audio conversie mislukt")

    # 2b. Speech-to-Speech: converteer naar Peter-stem via ElevenLabs
    el_api = os.getenv("ELEVENLABS_API_KEY", "")
    clone_id = os.getenv("ELEVENLABS_CLONE_VOICE_ID", "")
    if el_api and clone_id:
        try:
            import httpx as _httpx
            logger.info("[Campaigns] Speech-to-Speech: converteer naar Peter-stem...")
            audio_bytes = audio_mp3.read_bytes()
            sts_resp = _httpx.post(
                f"https://api.elevenlabs.io/v1/speech-to-speech/{clone_id}",
                headers={"xi-api-key": el_api, "Accept": "audio/mpeg"},
                data={"model_id": "eleven_multilingual_sts_v2"},
                files={"audio": ("audio.mp3", audio_bytes, "audio/mpeg")},
                timeout=120,
            )
            if sts_resp.status_code == 200:
                peter_mp3 = work_dir / "peter_audio.mp3"
                peter_mp3.write_bytes(sts_resp.content)
                audio_mp3 = peter_mp3  # Gebruik Peter-stem
                logger.success("[Campaigns] Speech-to-Speech klaar: Peter-stem")
            else:
                logger.warning(f"[Campaigns] STS mislukt ({sts_resp.status_code}), gebruik originele stem")
        except Exception as e:
            logger.warning(f"[Campaigns] STS fout: {e}, gebruik originele stem")

    # 3. Meet duur
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio_mp3)],
        capture_output=True, text=True, timeout=10,
    )
    audio_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 30.0

    # 4. Bouw script dict met AI-gegenereerde visual queries
    app = load_app(app_id)
    memory = bm.load(app_id)
    memory["niche"] = memory.get("niche") or app.get("niche", "")

    scenes = _generate_scene_visuals(script_text, app, audio_duration)

    script = {
        "scenes": scenes,
        "full_voiceover_text": script_text,
        "title": "User recorded",
    }

    # 5. Genereer video met user audio (skip TTS)
    from video_engine.providers.pro_video_provider import ProVideoProvider
    provider = ProVideoProvider(voice="peter-natural")
    output_dir = ASSETS / "videos"
    output_dir.mkdir(parents=True, exist_ok=True)

    vid_id = str(uuid.uuid4())[:8]
    output_path = output_dir / f"user_{vid_id}.mp4"

    # Haal scene visuals op
    import concurrent.futures
    scene_durations = [s["duration_sec"] for s in scenes]
    scene_data = [
        {"scene": scene, "visual": None, "duration": dur, "idx": i}
        for i, (scene, dur) in enumerate(zip(scenes, scene_durations))
    ]
    provider._used_video_ids = set()

    def _fetch(sd):
        sd["visual"] = provider._get_scene_visual(
            sd["scene"], sd["idx"], memory, work_dir, sd["duration"], ""
        )
        return sd

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        scene_data = list(ex.map(_fetch, scene_data))

    # Maak clips
    def _make_clip(sd):
        return provider._create_visual_clip(sd, sd["idx"], work_dir, len(scene_data))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        clips = [c for c in ex.map(_make_clip, scene_data) if c and c.exists()]

    if not clips:
        raise HTTPException(status_code=500, detail="Geen visuele clips geproduceerd")

    # Concat
    raw_video = work_dir / "raw_video.mp4"
    provider._concat_visual_clips(clips, raw_video)

    # Combineer met user audio
    music_track = provider._select_music_for_mood(script)
    video_dur = provider._get_media_duration(raw_video) or audio_duration

    if music_track and music_track.exists():
        fade_out = max(0, video_dur - 2.5)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video), "-i", str(audio_mp3),
            "-stream_loop", "-1", "-i", str(music_track),
            "-filter_complex", (
                f"[1:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                f"acompressor=threshold=0.089:ratio=3:attack=5:release=50:makeup=1.2[voice];"
                f"[2:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                f"volume=0.10,afade=t=in:d=1.5,afade=t=out:st={fade_out:.1f}:d=2.5[music];"
                f"[voice][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-shortest", "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(raw_video), "-i", str(audio_mp3),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
            str(output_path),
        ]

    subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    # Cleanup work dir
    import shutil
    shutil.rmtree(str(work_dir), ignore_errors=True)

    if not output_path.exists():
        raise HTTPException(status_code=500, detail="Video rendering mislukt")

    logger.success(f"[Campaigns] User-audio video klaar: {output_path}")
    return {"video_path": str(output_path), "duration": audio_duration}


@router.post("/generate-script")
def generate_new_script(body: dict = {}):
    """Genereer een nieuw voiceover script voor opname."""
    import openai

    app_id = body.get("app_id", "app_76fe2fdd")
    app = load_app(app_id)

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.9,
        messages=[{
            "role": "system",
            "content": (
                f"Je schrijft een TikTok voiceover voor '{app.get('name', 'de app')}'.\n"
                f"Beschrijving: {app.get('description', 'een handige app')}\n"
                f"Niche: {app.get('niche', 'general')}\n"
                f"Doelgroep: {app.get('target_audience', 'professionals')}\n\n"
                "STRUCTUUR (als doorlopend verhaal, GEEN losse blokken):\n"
                "1. Begin met een shocking getal dat bij deze niche past\n"
                "2. Reken dat getal door naar iets groters (per jaar/van je leven)\n"
                "3. Vertel hoe je de app ontdekte via een collega, eerst twijfel, dan resultaat\n"
                "4. Eindig met specifieke call-to-action + 'type het getal'\n\n"
                "ABSOLUTE REGELS:\n"
                "- Schrijf als spraakbericht aan een vriend, NIET als presentatie\n"
                "- Lange doorlopende zinnen met komma's die doorlopen\n"
                "- VERBODEN: '...', korte losse zinnen, marketing-taal, opsommingen\n"
                "- WEL: 'weet je', 'serieus', 'echt', 'gewoon', twijfels, zelf-correcties\n"
                "- Max 100 woorden (~35 seconden)\n"
                "- ALLEEN de ingesproken tekst, geen aanwijzingen of labels\n"
                "- Nederlands, informele spreektaal"
            ),
        }, {
            "role": "user",
            "content": "Schrijf het script.",
        }],
    )

    return {"script_text": resp.choices[0].message.content}
