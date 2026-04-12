"""
Campaign Pipeline — de volledige flow van idee tot publicatie-klaar bundel.

Stap 1: Laad app-context + brand memory
Stap 2: Genereer ideeën (IdeaGeneratorAgent)
Stap 3: Kies beste idee (of laat gebruiker kiezen)
Stap 4: Schrijf script (ScriptWriterAgent)
Stap 4b: Viral Algorithm Check — scoort script op viral potentieel
         Als score < 80 → herschrijf script met verbeterinstructies (max 3x)
Stap 5: Produceer video (VideoEngine)
Stap 6: Schrijf caption (CaptionWriterAgent)
Stap 7: Bundel alles → status PENDING_APPROVAL
Stap 8: Sla op + notificeer gebruiker
"""

import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable

from loguru import logger

from agents.idea_generator import IdeaGeneratorAgent
from agents.market_researcher import MarketResearchAgent
from agents.script_writer import ScriptWriterAgent
from agents.caption_writer import CaptionWriterAgent
from agents.viral_checker import ViralCheckerAgent
from agents import brand_memory as bm
from backend.constants import PIPELINE_DEFAULT_DURATION_SEC
from backend.cost_guardrails import CostGuardrails, BudgetExceededError
from backend.models.campaign import CampaignBundle, CampaignStatus
from backend.repository.factory import get_app_repo, get_campaign_repo
from backend.supabase import has_supabase_env, upload_file_to_public_bucket
from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_writable_dir, get_runtime_data_dir
from video_engine.orchestrator import VideoOrchestrator

ROOT = Path(__file__).parent.parent
CONFIGS_DIR = ROOT / "configs"
DATA_DIR = ensure_writable_dir(ROOT / "data" / "campaigns", get_runtime_data_dir("campaigns"))

# ── Per-app pipeline lock (idempotentie) ─────────────────────────────
# Voorkomt dat twee gelijktijdige requests dezelfde app tegelijk verwerken.
_app_locks: dict[str, threading.Lock] = {}
_app_locks_mutex = threading.Lock()


def _get_app_lock(app_id: str) -> threading.Lock:
    with _app_locks_mutex:
        if app_id not in _app_locks:
            _app_locks[app_id] = threading.Lock()
        return _app_locks[app_id]


def _resolve_campaigns_dir(tenant_id: str) -> Path:
    """
    'default' → data/campaigns/        (backward compat)
    overig    → data/tenants/{tenant_id}/campaigns/
    """
    if tenant_id == "default":
        return DATA_DIR
    return ensure_writable_dir(
        ROOT / "data" / "tenants" / tenant_id / "campaigns",
        get_runtime_data_dir("tenants", tenant_id, "campaigns"),
    )

# Log active feature flags once at import time
if os.getenv("EXPERIMENTS_ENABLED", "false").lower() == "true":
    logger.info("[Pipeline] EXPERIMENTS_ENABLED=true — variant-generatie actief")


def load_app(app_id: str) -> dict:
    app = get_app_repo(tenant_id="default").get_app(app_id)
    if app:
        return app
    raise ValueError(f"App niet gevonden: {app_id}")


def save_bundle(bundle: CampaignBundle, tenant_id: str = "default") -> Path:
    repo = get_campaign_repo(tenant_id=tenant_id)
    repo.save(bundle)
    campaigns_dir = _resolve_campaigns_dir(tenant_id)
    return campaigns_dir / f"{bundle.id}.json"


def load_bundle(campaign_id: str, tenant_id: str = "default") -> CampaignBundle:
    repo = get_campaign_repo(tenant_id=tenant_id)
    bundle = repo.get(campaign_id, tenant_id=tenant_id)
    if bundle is None:
        raise FileNotFoundError(campaign_id)
    return bundle


def list_pending_campaigns(tenant_id: str = "default") -> list[CampaignBundle]:
    repo = get_campaign_repo(tenant_id=tenant_id)
    return repo.list_pending(tenant_id=tenant_id)


def run_pipeline(
    app_id: str,
    platform: str = "tiktok",
    idea_index: int = 0,
    tenant_id: str = "default",
    voice: str = "nova",
    tts_speed: float = 1.0,
    voice_settings: dict | None = None,
    on_progress: Callable[[str], None] | None = None,
    chosen_idea: dict | None = None,
    campaign_id: str | None = None,
    custom_brief: str | None = None,
    forced_content_format: str | None = None,
) -> CampaignBundle:
    """
    Voer de volledige campagne-pipeline uit voor een app.

    Args:
        app_id: ID van de app (uit app_registry.json)
        platform: Doelplatform
        idea_index: Welk van de 5 gegenereerde ideeën te gebruiken (0-4)
        voice_settings: Dict met stability, similarity_boost, style voor TTS
        on_progress: Optionele callback voor voortgangsupdates
        chosen_idea: Optioneel pre-gekozen idee (skip idee-generatie)
        campaign_id: Optioneel pre-aangemaakt campaign ID (van start_campaign endpoint)

    Returns:
        CampaignBundle met status PENDING_APPROVAL
    """

    def progress(msg: str):
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    # Idempotentie: blokkeer gelijktijdige pipeline-runs voor dezelfde app
    app_lock = _get_app_lock(app_id)
    if not app_lock.acquire(blocking=False):
        raise RuntimeError(
            f"Pipeline al actief voor app '{app_id}'. "
            f"Wacht tot de huidige run is afgerond voordat je opnieuw start."
        )

    # Gebruik pre-aangemaakt bundle ID als opgegeven (zodat API-tracking werkt)
    bundle = CampaignBundle(
        app_id=app_id,
        tenant_id=tenant_id,
        platform=platform,
        status=CampaignStatus.GENERATING,
    )
    if campaign_id:
        bundle.id = campaign_id

    total_cost = 0.0
    guardrails = CostGuardrails(tenant_id=tenant_id)

    try:
        # Budget check vóór start
        guardrails.check_budget()

        # Stap 1: App + Brand Memory laden
        progress("Stap 1/7: App-context en brand memory laden...")
        app = load_app(app_id)
        memory = bm.load(app_id)

        # Zorg dat niche en url beschikbaar zijn voor video provider
        memory["niche"] = memory.get("niche") or app.get("niche", "")
        memory["url"] = memory.get("url") or app.get("url", "")
        memory["app_name"] = memory.get("app_name") or app.get("name", "")

        # Brand memory stem wordt NIET meer geforceerd — gebruikerskeuze uit
        # dashboard heeft altijd prioriteit. Brand memory voice is enkel een
        # suggestie die het dashboard als default kan tonen.
        # if memory.get("tts_voice"):
        #     voice = memory["tts_voice"]

        # Genereer display_name: "Campagne N — AppNaam"
        app_name = memory.get("app_name") or app.get("name", app_id)
        repo = get_campaign_repo(tenant_id=tenant_id)
        existing_count = len(repo.list(tenant_id=tenant_id))
        bundle.display_name = f"Campagne {existing_count + 1} — {app_name}"

        # Stap 2: Marktonderzoek + Ideeën genereren (of pre-gekozen idee gebruiken)
        if chosen_idea:
            progress("Stap 2/7: Pre-gekozen idee laden...")
            bundle.idea = chosen_idea
            progress(f"  > Idee: '{chosen_idea.get('title', '?')}'")
        else:
            # Stap 2a: Marktonderzoek (verrijkt idee-generatie met niche-inzichten)
            market_research_str = ""
            try:
                progress("Stap 2/7: Marktonderzoek uitvoeren...")
                research_agent = MarketResearchAgent()
                raw_research = research_agent.run(app=app, platform=platform, custom_brief=custom_brief)
                market_research_str = MarketResearchAgent.format_for_idea_prompt(raw_research)
                total_cost += research_agent.total_cost_usd
                guardrails.record_cost(research_agent.total_cost_usd, "MarketResearchAgent", bundle.id)
                del research_agent
                progress(f"  > Marktonderzoek klaar — {len(raw_research.get('content_opportunities', []))} kansen gevonden")
            except Exception as research_err:
                logger.warning(f"[Pipeline] Marktonderzoek mislukt (niet kritiek): {research_err}")
                # Niet kritiek — pipeline gaat door zonder research

            # Stap 2b: Ideeën genereren met marktonderzoek als context
            progress("Stap 2/7: Campagne-ideeën genereren...")
            idea_agent = IdeaGeneratorAgent()
            # Haal recente campagne-titels op zodat de LLM geen duplicaten genereert
            recent_titles = []
            try:
                repo = get_campaign_repo(tenant_id=tenant_id)
                recent_campaigns = repo.list(tenant_id=tenant_id)
                recent_titles = [
                    c.get("idea", {}).get("title", "") or c.get("display_name", "")
                    for c in (recent_campaigns[-15:] if len(recent_campaigns) > 15 else recent_campaigns)
                    if isinstance(c, dict)
                ]
                recent_titles = [t for t in recent_titles if t]
            except Exception:
                pass  # Niet kritiek — ideeën worden alsnog gegenereerd
            with ThreadPoolExecutor(max_workers=1) as _pool:
                _f = _pool.submit(idea_agent.run, app=app, memory=memory,
                                  platform=platform, recent_titles=recent_titles,
                                  custom_brief=custom_brief,
                                  market_research=market_research_str)
                try:
                    ideas = _f.result(timeout=120)
                except FuturesTimeout:
                    raise RuntimeError("Idee generatie timeout (>2 min) — probeer opnieuw")
            total_cost += idea_agent.total_cost_usd
            guardrails.record_cost(idea_agent.total_cost_usd, "IdeaGeneratorAgent", bundle.id)
            del idea_agent  # Vrijgeven voor GC

            if not ideas:
                raise RuntimeError("Geen ideeën gegenereerd door AI")

            # Normaliseer ideas naar een lijst van dicts (bescherming tegen malformed JSON)
            if isinstance(ideas, dict):
                # AI gaf een dict terug (bijv. {"ideas": [...]}) — probeer de lijst te extraheren
                ideas = next((v for v in ideas.values() if isinstance(v, list)), list(ideas.values()))
                logger.warning(f"[Pipeline] IdeaGenerator retourneerde dict i.p.v. list. Geëxtraheerd: {len(ideas)} items")
            if not isinstance(ideas, list):
                raise RuntimeError(f"IdeaGeneratorAgent retourneerde ongeldig type: {type(ideas).__name__}")
            # Filter alleen echte dicts (verwijder strings, lijsten, None etc.)
            ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
            if not ideas:
                raise RuntimeError("IdeaGeneratorAgent retourneerde geen geldige ideeën (geen dicts met 'title')")

            chosen_idea = _score_and_pick_idea(ideas, idea_index, memory)
            if not isinstance(chosen_idea, dict):
                raise RuntimeError(f"_score_and_pick_idea retourneerde {type(chosen_idea).__name__} i.p.v. dict")
            bundle.idea = chosen_idea
            progress(f"  > Gekozen idee: '{chosen_idea.get('title', '?')}' (score: {chosen_idea.get('_score', '?')})")

        # Stap 3+4: Parallel Script Tournament — 3 scripts tegelijk, beste wint
        # Doel: 90+ viral score. Parallel = zelfde tijd als 1 poging, maar 3x kans.
        import concurrent.futures as _cf

        TOURNAMENT_SIZE = 2   # 2 scripts parallel = snellere selectie, nog steeds goedkoop
        TARGET_SCORE = 75     # Verlaagd: 75 is goed genoeg, voorkomt re-write loops

        progress("Stap 3/7: Video-script genereren...")

        video_type = _determine_video_type(chosen_idea, app=app, memory=memory, forced_content_format=forced_content_format)

        def _generate_and_score(idx: int) -> tuple[dict, dict, float, float]:
            """Genereer één script + viral check. Retourneert (script, viral_result, script_cost, viral_cost)."""
            agent = ScriptWriterAgent()
            viral = ViralCheckerAgent()
            try:
                s = agent.run(
                    idea=chosen_idea, app=app, memory=memory,
                    platform=platform,
                    target_duration_sec=PIPELINE_DEFAULT_DURATION_SEC,
                    video_type=video_type,
                )
                if not isinstance(s, dict) or not s.get("scenes"):
                    return {}, {}, agent.total_cost_usd, 0.0
                s = _enforce_echo_loop(s)  # Garandeer echo-loop vóór viral check
                v = viral.run(script=s, app=app, memory=memory)
                return s, v, agent.total_cost_usd, viral.total_cost_usd
            except Exception as exc:
                logger.warning(f"[Pipeline] Tournament slot {idx} mislukt: {exc}")
                return {}, {}, 0.0, 0.0

        with _cf.ThreadPoolExecutor(max_workers=TOURNAMENT_SIZE) as ex:
            futures = [ex.submit(_generate_and_score, i) for i in range(TOURNAMENT_SIZE)]
            results = [f.result() for f in futures]

        # Kies het best scorende script
        best_script, best_viral_result, best_viral_score = None, {}, 0.0
        for s, v, sc, vc in results:
            if not s:
                continue
            score = v.get("composite_score", 0)
            total_cost += sc + vc
            guardrails.record_cost(sc, "ScriptWriterAgent", bundle.id)
            guardrails.record_cost(vc, "ViralCheckerAgent", bundle.id)
            if score > best_viral_score:
                best_script, best_viral_result, best_viral_score = s, v, score

        if not best_script:
            raise RuntimeError("Alle 3 tournament-slots mislukten — geen geldig script")

        script = best_script
        viral_result = best_viral_result
        viral_score = best_viral_score
        bundle.script = script
        guardrails.check_campaign_budget(bundle.id, total_cost)

        verdict = viral_result.get("verdict", "UNKNOWN")
        scores_log = [f"{r[1].get('composite_score', 0):.1f}" for r in results if r[0]]
        progress(f"  > Tournament scores: [{', '.join(scores_log)}] — beste: {viral_score:.1f}/100 ({verdict})")

        # Targeted rewrite als beste tournament-score nog onder TARGET_SCORE (90) ligt.
        # Max 2 gerichte herschrijvingen op de zwakste dimensies. Keep-best altijd actief.
        MAX_REWRITES = 1  # Max 1 rewrite voor budget
        REWRITE_THRESHOLD = 75  # Alleen herschrijven als score echt te laag is
        DIM_MIN = 65  # Alleen rewrite bij extreem lage dimensie
        rewrite_count = 0
        # best_script / best_viral_result / best_viral_score zijn al gezet door tournament
        def _needs_rewrite(score: float, result: dict) -> bool:
            if score < REWRITE_THRESHOLD:
                return True
            scores_dict = result.get("scores", {})
            low_dims = [k for k, v in scores_dict.items() if v < DIM_MIN]
            if low_dims:
                logger.info(f"  > Dimensies te laag ({low_dims}) — script herschrijven ondanks composite {score:.1f}")
                return True
            return False
        while _needs_rewrite(viral_score, viral_result) and rewrite_count < MAX_REWRITES:
            rewrite_count += 1
            rewrite_instructions = viral_result.get("rewrite_instructions", {})
            hook_alts = rewrite_instructions.get("hook_alternatives", [])
            pacing = rewrite_instructions.get("pacing_fixes", "")
            line_fixes = rewrite_instructions.get("specific_line_fixes", [])
            loop_fix = rewrite_instructions.get("loop_fix", "")
            auth_fixes = rewrite_instructions.get("authenticity_fixes", "")
            share_add = rewrite_instructions.get("share_trigger_add", "")
            save_add = rewrite_instructions.get("save_trigger_add", "")
            cta_upgrade = rewrite_instructions.get("cta_upgrade", "")

            # Bouw extra instructie voor de script writer
            extra_parts = []
            if hook_alts:
                extra_parts.append(f"GEBRUIK EEN VAN DEZE HOOKS (kies de sterkste):\n" + "\n".join(f"- {h}" for h in hook_alts[:3]))
            if pacing:
                extra_parts.append(f"PACING VERBETERING: {pacing}")
            if loop_fix:
                extra_parts.append(f"LOOP FIX (KRITIEK): {loop_fix}")
            if auth_fixes:
                extra_parts.append(f"AUTHENTICITEIT FIX: {auth_fixes}")
            if share_add:
                extra_parts.append(f"SHARE TRIGGER TOEVOEGEN: {share_add}")
            if save_add:
                extra_parts.append(f"SAVE MOMENT TOEVOEGEN: {save_add}")
            if cta_upgrade:
                extra_parts.append(f"CTA UPGRADE: {cta_upgrade}")
            taal_fixes = rewrite_instructions.get("taal_fixes", "")
            if taal_fixes:
                extra_parts.append(f"TAAL FIXES (grammatica/TTS): {taal_fixes}")
            for fix in line_fixes[:3]:
                extra_parts.append(
                    f"VERVANG: \"{fix.get('original', '')}\" → \"{fix.get('improved', '')}\" (reden: {fix.get('reason', '')})"
                )

            # Voeg zwakke dimensies toe als focus (alles onder 80)
            scores = viral_result.get("scores", {})
            weak_dims = [k for k, v in scores.items() if v < 80]
            if weak_dims:
                extra_parts.append(f"FOCUS OP DEZE ZWAKKE PUNTEN (score < 80, VERBETER DIT SPECIFIEK): {', '.join(d.replace('_', ' ') for d in weak_dims)}")

            # Voeg strengths toe om te behouden
            strengths = viral_result.get("strengths", [])
            if strengths:
                extra_parts.append(f"BEHOUD DEZE STERKTES: {'; '.join(strengths[:2])}")

            extra_instruction = "\n".join(extra_parts)

            progress(
                f"  > Script herschrijven (poging {rewrite_count}/{MAX_REWRITES}) "
                f"met viral verbeteringen..."
            )

            # Herschrijf met extra instructies van viral checker
            try:
                rewrite_idea = {**chosen_idea}
                if hook_alts:
                    rewrite_idea["hook_options"] = hook_alts

                rewrite_agent = ScriptWriterAgent()
                script = rewrite_agent.run(
                    idea=rewrite_idea,
                    app=app,
                    memory=memory,
                    platform=platform,
                    target_duration_sec=PIPELINE_DEFAULT_DURATION_SEC,
                    video_type=_determine_video_type(chosen_idea, app=app, memory=memory),
                    extra_instruction=extra_instruction,
                )
                total_cost += rewrite_agent.total_cost_usd
                guardrails.record_cost(rewrite_agent.total_cost_usd, "ScriptWriterAgent-rewrite", bundle.id)
                if not script.get("scenes"):
                    logger.warning("[Pipeline] Herschreven script mist scenes — gebruik vorig script")
                    script = bundle.script  # Behou het vorige werkende script
                script = _enforce_echo_loop(script)  # Garandeer echo-loop na rewrite
                bundle.script = script

                # Check opnieuw
                recheck_agent = ViralCheckerAgent()
                viral_result = recheck_agent.run(script=script, app=app, memory=memory)
                total_cost += recheck_agent.total_cost_usd
                guardrails.record_cost(recheck_agent.total_cost_usd, "ViralCheckerAgent-recheck", bundle.id)

                viral_score = viral_result.get("composite_score", 0)
                verdict = viral_result.get("verdict", "UNKNOWN")
                progress(f"  > Nieuwe viral score: {viral_score}/100 ({verdict})")

                # Keep-best: gebruik altijd het hoogst scorende script
                if viral_score > best_viral_score:
                    best_script = script
                    best_viral_result = viral_result
                    best_viral_score = viral_score
                    logger.info(f"  > Nieuw beste script opgeslagen (score: {best_viral_score:.1f})")
                else:
                    logger.info(
                        f"  > Rewrite scoort lager ({viral_score:.1f} < {best_viral_score:.1f}) — "
                        f"beste script behouden"
                    )
                    # Gebruik het beste resultaat als basis voor volgende rewrite
                    viral_result = best_viral_result

            except Exception as rewrite_err:
                logger.warning(
                    f"[Pipeline] Rewrite poging {rewrite_count} mislukt: {rewrite_err}. "
                    f"Ga verder met beste script (score: {best_viral_score}/100)."
                )
                break  # Stop rewrites, gebruik het beste script

        # Herstel beste script (kan afwijken van laatste rewrite)
        script = best_script
        viral_result = best_viral_result
        viral_score = best_viral_score
        bundle.script = script
        progress(f"  > Finaal script geselecteerd: score {viral_score:.1f}/100")

        # Sla viral score op in bundle
        bundle.viral_score = {
            "composite_score": viral_result.get("composite_score", 0),
            "verdict": viral_result.get("verdict", "UNKNOWN"),
            "scores": viral_result.get("scores", {}),
            "summary": viral_result.get("summary", ""),
            "strengths": viral_result.get("strengths", []),
            "weaknesses": viral_result.get("weaknesses", []),
            "algorithm_tips": viral_result.get("algorithm_tips", []),
            "rewrites_needed": rewrite_count,
        }

        # Geheugen vrijmaken na tournament voordat zware video-encode start
        import gc
        gc.collect()

        # Stap 5+6: Video produceren EN caption schrijven (parallel)
        vs_info = f", stability={voice_settings['stability']}" if voice_settings else ""
        progress(f"Stap 5/7: Video produceren (stem: {voice}, snelheid: {tts_speed}x{vs_info})...")
        progress("Stap 6/7: Caption en hashtags schrijven (parallel)...")

        vo_kwargs = {"voice": voice, "tts_speed": tts_speed, "on_progress": on_progress}
        if voice_settings:
            vo_kwargs["voice_settings"] = voice_settings

        def _produce_video():
            engine = VideoOrchestrator(**vo_kwargs)
            path = engine.produce(script=script, memory=memory, app_id=app_id)
            return path, engine.total_cost_usd, getattr(engine, "last_error", "")

        def _write_caption():
            agent = CaptionWriterAgent()
            cap = agent.run(
                script=script, app=app, memory=memory,
                platform=platform, post_goal=chosen_idea.get("goal", "awareness"),
            )
            return cap, agent.total_cost_usd

        with ThreadPoolExecutor(max_workers=2) as pool:
            video_future = pool.submit(_produce_video)
            caption_future = pool.submit(_write_caption)

            # Wacht op caption (snel, ~5-10s)
            caption, caption_cost = caption_future.result()
            bundle.caption = caption
            total_cost += caption_cost
            guardrails.record_cost(caption_cost, "CaptionWriterAgent", bundle.id)
            progress("  > Caption klaar!")

            # Wacht op video (langzamer, ~60-180s) — timeout voorkomt eindeloos hangen
            try:
                video_path, video_cost, video_error = video_future.result(timeout=720)
            except FuturesTimeout:
                video_future.cancel()
                raise RuntimeError("Video productie timeout (>12 min) — probeer opnieuw of gebruik een kortere video")
            if not video_path:
                raise RuntimeError(
                    f"Video productie mislukt: {video_error or 'geen videobestand aangemaakt'}"
                )
            total_cost += video_cost
            guardrails.record_cost(video_cost, "VideoOrchestrator", bundle.id)
            if has_supabase_env():
                video_size = video_path.stat().st_size if video_path and video_path.exists() else 0
                logger.info(f"[Pipeline] Video uploaden naar Supabase: {video_path} ({video_size} bytes)")
                if video_size < 10_000:
                    raise RuntimeError(f"Video te klein voor upload ({video_size} bytes) — FFmpeg heeft stilletjes gefaald")
                progress("  > Video uploaden naar storage...")
                bundle.video_path = upload_file_to_public_bucket(
                    "campaign-videos",
                    f"{bundle.id}/master.mp4",
                    video_path,
                    content_type="video/mp4",
                )
            else:
                bundle.video_path = str(video_path)
            progress("  > Video klaar!")

        # Stap 6b: Experiment varianten genereren (optioneel — achter feature flag)
        if os.getenv("EXPERIMENTS_ENABLED", "false").lower() == "true":
            try:
                from experiments.variant_generator import VariantGenerator
                from backend.services.experiment_service import ExperimentService

                progress("Experiment: varianten genereren...")
                experiment = VariantGenerator(tenant_id=tenant_id).generate(
                    campaign_bundle=bundle.model_dump(mode="json"),
                    app_id=app_id,
                    on_progress=on_progress,
                )
                progress("Experiment: kwaliteit scoren...")
                ExperimentService(tenant_id=tenant_id).score_experiment(experiment.experiment_id, app_id)
                bundle.experiment_id = experiment.experiment_id
                progress(
                    f"  > Experiment aangemaakt: {experiment.experiment_id} "
                    f"({len(experiment.variants)} varianten)"
                )
            except Exception as exp_err:
                logger.warning(f"Experiment generatie overgeslagen: {exp_err}")

        # Stap 7: Bundelen en opslaan
        progress("Stap 7/7: Campagne bundelen voor goedkeuring...")
        bundle.total_cost_usd = total_cost
        bundle.status = CampaignStatus.PENDING_APPROVAL
        save_bundle(bundle, tenant_id=tenant_id)

        # Auto-goedkeuring proberen (als APPROVAL_REQUIRED=false of viral score ≥ drempel)
        auto_approved = False
        try:
            from backend.services.approval_service import try_auto_approve
            auto_approved = try_auto_approve(bundle, tenant_id=tenant_id)
            if auto_approved:
                progress(
                    f"  > AUTO-GOEDGEKEURD en gepubliceerd op TikTok! "
                    f"(viral score: {viral_score}/100)"
                )
        except Exception as auto_err:
            logger.warning(f"[Pipeline] Auto-approve mislukt (niet kritiek): {auto_err}")

        if not auto_approved:
            progress(
                f"[OK] Campagne klaar! ID={bundle.id} | "
                f"Totale kosten=${total_cost:.4f} | "
                f"Status=WACHT OP GOEDKEURING"
            )
        else:
            progress(
                f"[OK] Campagne gepubliceerd! ID={bundle.id} | "
                f"Totale kosten=${total_cost:.4f}"
            )

    except Exception as e:
        bundle.status = CampaignStatus.FAILED
        bundle.total_cost_usd = total_cost  # Sla kosten op tot punt van falen
        bundle.approval_notes = f"Pipeline fout in stap: {str(e)[:200]}"
        logger.error(f"Pipeline mislukt voor {app_id} (tenant={tenant_id}): {e}")
        save_bundle(bundle, tenant_id=tenant_id)

        # Sla geleerde les op als insight (zodat dezelfde fout vermeden wordt)
        try:
            error_msg = str(e)[:100]
            bm.add_insight(app_id, f"Pipeline fout: {error_msg}")
        except Exception:
            pass

        raise

    finally:
        # Altijd lock vrijgeven — ook bij uitzonderingen
        app_lock.release()

    return bundle


def run_post_pipeline(
    app_id: str,
    platform: str = "facebook",
    post_type: str = "video",
    tenant_id: str = "default",
    on_progress: Callable[[str], None] | None = None,
    campaign_id: str | None = None,
    custom_brief: str | None = None,
) -> CampaignBundle:
    """
    Lichtgewicht pipeline voor tekst-, foto- en videoposts.

    Args:
        app_id:     ID van de app (uit app_registry.json)
        platform:   Doelplatform ("facebook", "tiktok", ...)
        post_type:  "text" | "photo" | "video"
        tenant_id:  Tenant isolatie
        on_progress: Optionele voortgangs-callback

    Returns:
        CampaignBundle met status PENDING_APPROVAL en post_type gezet
    """
    if post_type == "video":
        bundle = run_pipeline(app_id=app_id, platform=platform, tenant_id=tenant_id,
                              on_progress=on_progress, campaign_id=campaign_id, custom_brief=custom_brief)
        bundle.post_type = "video"
        save_bundle(bundle, tenant_id=tenant_id)
        return bundle

    def progress(msg: str):
        logger.info(msg)
        if on_progress:
            on_progress(msg)

    bundle = CampaignBundle(
        app_id=app_id,
        tenant_id=tenant_id,
        platform=platform,
        post_type=post_type,
        status=CampaignStatus.GENERATING,
    )
    if campaign_id:
        bundle.id = campaign_id

    total_cost = 0.0
    guardrails = CostGuardrails(tenant_id=tenant_id)

    try:
        guardrails.check_budget()

        # Stap 1: App + brand memory laden
        progress(f"Stap 1/3: App-context laden ({post_type} post)...")
        app = load_app(app_id)
        memory = bm.load(app_id)
        memory["niche"] = memory.get("niche") or app.get("niche", "")
        memory["url"] = memory.get("url") or app.get("url", "")
        memory["app_name"] = memory.get("app_name") or app.get("name", "")

        app_name = memory.get("app_name") or app.get("name", app_id)
        repo = get_campaign_repo(tenant_id=tenant_id)
        existing_count = len(repo.list(tenant_id=tenant_id))
        bundle.display_name = f"Post {existing_count + 1} — {app_name}"

        # Stap 2: Idee genereren
        progress("Stap 2/3: Post-idee genereren...")
        idea_agent = IdeaGeneratorAgent()
        with ThreadPoolExecutor(max_workers=1) as _pool:
            _f = _pool.submit(
                idea_agent.run,
                app=app,
                memory=memory,
                platform=platform,
                recent_titles=[],
                custom_brief=f"Kort {post_type} post — geen script of video nodig",
            )
            try:
                ideas = _f.result(timeout=120)
            except TimeoutError:
                raise RuntimeError("Idee generatie timeout (>2 min) — probeer opnieuw")
        total_cost += idea_agent.total_cost_usd
        guardrails.record_cost(idea_agent.total_cost_usd, "IdeaGeneratorAgent", bundle.id)

        if not ideas:
            raise RuntimeError("Geen ideeën gegenereerd")
        if isinstance(ideas, dict):
            ideas = next((v for v in ideas.values() if isinstance(v, list)), list(ideas.values()))
        if not isinstance(ideas, list):
            raise RuntimeError(f"IdeaGeneratorAgent retourneerde ongeldig type: {type(ideas).__name__}")
        ideas = [i for i in ideas if isinstance(i, dict) and i.get("title")]
        if not ideas:
            raise RuntimeError("Geen geldige ideeën (geen dicts met 'title')")

        chosen_idea = ideas[0]
        bundle.idea = chosen_idea
        progress(f"  > Idee: '{chosen_idea.get('title', '?')}'")

        # Stap 2b (foto): één DALL-E afbeelding genereren
        if post_type == "photo":
            progress("Stap 2b/3: Afbeelding genereren (DALL-E)...")
            try:
                import openai as _openai
                _client = _openai.OpenAI()
                visual_prompt = (
                    chosen_idea.get("visual_description")
                    or chosen_idea.get("hook", "")
                    or chosen_idea.get("title", "")
                )
                image_resp = _client.images.generate(
                    model="dall-e-3",
                    prompt=f"{visual_prompt[:900]} — high quality, social media marketing photo",
                    size="1024x1024",
                    quality="standard",
                    n=1,
                )
                image_url = image_resp.data[0].url
                # Download afbeelding lokaal zodat de URL niet verloopt
                try:
                    import requests as _requests
                    from utils.runtime_paths import get_generated_assets_dir, ensure_dir
                    img_dir = ensure_dir(get_generated_assets_dir() / "images")
                    img_path = img_dir / f"{bundle.id}.png"
                    img_data = _requests.get(image_url, timeout=30)
                    img_data.raise_for_status()
                    img_path.write_bytes(img_data.content)
                    local_url = f"/assets/images/{bundle.id}.png"
                    bundle.thumbnail_path = local_url
                    bundle.idea = {**chosen_idea, "image_url": local_url}
                except Exception as dl_err:
                    logger.warning(f"[Pipeline] Afbeelding downloaden mislukt, gebruik tijdelijke URL: {dl_err}")
                    bundle.thumbnail_path = image_url
                    bundle.idea = {**chosen_idea, "image_url": image_url}
                # DALL-E 3 standaard kosten: ~$0.04 per afbeelding
                total_cost += 0.04
                guardrails.record_cost(0.04, "DALL-E-3", bundle.id)
                progress(f"  > Afbeelding gegenereerd")
            except Exception as img_err:
                logger.warning(f"[Pipeline] DALL-E afbeelding mislukt (niet kritiek): {img_err}")

        # Stap 3: Caption schrijven
        progress("Stap 3/3: Caption schrijven...")
        caption_agent = CaptionWriterAgent()
        # Geef het idee mee als script context zodat de caption concreet aansluit op de hook
        idea_as_script = {
            "title": chosen_idea.get("title", ""),
            "hook": chosen_idea.get("hook_options", [chosen_idea.get("hook", "")])[0] if chosen_idea.get("hook_options") else chosen_idea.get("hook", ""),
            "angle": chosen_idea.get("angle", ""),
            "psychological_mechanic": chosen_idea.get("psychological_mechanic", ""),
            "emotional_arc": chosen_idea.get("emotional_arc", ""),
            "shocking_fact": chosen_idea.get("shocking_fact", ""),
            "share_reason": chosen_idea.get("share_reason", ""),
            "comment_trigger": chosen_idea.get("comment_trigger", ""),
            "core_message": chosen_idea.get("core_message", ""),
        }
        caption = caption_agent.run(
            script=idea_as_script,
            app=app,
            memory=memory,
            platform=platform,
            post_goal=chosen_idea.get("goal", "awareness"),
        )
        bundle.caption = caption
        total_cost += caption_agent.total_cost_usd
        guardrails.record_cost(caption_agent.total_cost_usd, "CaptionWriterAgent", bundle.id)
        progress("  > Caption klaar!")

        bundle.total_cost_usd = total_cost
        bundle.status = CampaignStatus.PENDING_APPROVAL
        save_bundle(bundle, tenant_id=tenant_id)

        progress(
            f"[OK] {post_type.capitalize()} post klaar! ID={bundle.id} | "
            f"Kosten=${total_cost:.4f} | Status=WACHT OP GOEDKEURING"
        )

    except Exception as e:
        bundle.status = CampaignStatus.FAILED
        bundle.total_cost_usd = total_cost
        bundle.approval_notes = f"Pipeline fout: {str(e)[:200]}"
        logger.error(f"run_post_pipeline mislukt voor {app_id} ({post_type}): {e}")
        save_bundle(bundle, tenant_id=tenant_id)
        raise

    return bundle


def _enforce_echo_loop(script: dict) -> dict:
    """
    Garandeert dat de allerlaatste zin van scene 4 een echo-vraag is die
    het EXACTE KEY ELEMENT (getal) uit scene 1 bevat.

    Modellen eindigen met taglines of vage vragen zonder getal → loop_potential=70.
    Fix: vervang het laatste CTA-gedeelte (alles na de laatste echte zin) door
    "Ben jij ook [X uur] per week kwijt? Type het getal ↓" met het exacte getal.
    """
    import re as _re

    scenes = script.get("scenes", [])
    if not scenes:
        return script

    scene1_voice = scenes[0].get("voiceover", "")
    scene4 = scenes[-1]
    scene4_voice = scene4.get("voiceover", "")
    if not scene4_voice:
        return script

    # Extraheer key element (getal + eenheid) uit scene 1
    key_element = None
    number_map = ["tien", "twee", "drie", "vier", "vijf", "zes", "zeven",
                  "acht", "negen", "twaalf", "vijftien", "twintig", "dertig", "vijftig"]
    digit_ctx = _re.search(r'\b(\d+)\s*(uur|minuten|minuut|procent|%)\b', scene1_voice)
    if digit_ctx:
        key_element = digit_ctx.group(0)
    else:
        for word in number_map:
            ctx = _re.search(r'\b' + word + r'\b\s*(uur|minuten|minuut)?', scene1_voice.lower())
            if ctx:
                key_element = ctx.group(0).strip()
                break
        if not key_element:
            digit_only = _re.search(r'\b(\d+)\b', scene1_voice)
            key_element = (digit_only.group(1) + " uur") if digit_only else None

    key_num = key_element.split()[0] if key_element else None

    # Check: al goed? (vraag aanwezig EN specifiek getal echoot in laatste 100 chars)
    tail = scene4_voice.rstrip()[-100:].lower()
    if (("?" in tail or "↓" in tail) and (not key_num or key_num in tail)):
        return script  # Al correct

    if not key_element:
        key_element = "dit"

    # Vind de "prefix" — alles VOOR het CTA-gedeelte (voor de laatste "Hoeveel/Ben jij/Stuur")
    # Strategie: vind de positie van de LAATSTE serieuze sentence start
    # Zoek de laatste punt die NIET deel is van een getal/afkorting
    v = scene4_voice.rstrip()
    last_q_pos = v.rfind("?")

    if last_q_pos >= 0:
        # Vind het begin van de zin die de vraag bevat (zoek terug naar laatste ".")
        sentence_start = v.rfind(".", 0, last_q_pos)
        if sentence_start >= 0:
            # Neem alles T/M de periode als prefix
            prefix = v[:sentence_start + 1].rstrip()
        else:
            # Geen periode voor de vraag — gebruik lege prefix
            prefix = ""
    else:
        # Geen vraag — neem alles T/M de laatste periode als prefix
        last_dot = v.rfind(".")
        prefix = v[:last_dot + 1].rstrip() if last_dot >= 0 else v

    # Bouw echo-vraag: bevat EXACT KEY ELEMENT (loop_potential 85+)
    # EN vraagt om een getal (comment_bait 85+)
    # Formaat: "Ik verloor [X uur] — hoeveel verlies jij? Type het getal ↓"
    if "uur" in key_element or (key_element[0].isdigit() and "min" not in key_element):
        echo_q = f" Ik verloor {key_element} per week — hoeveel verlies jij? Type het getal ↓"
    elif "minuten" in key_element or "minuut" in key_element:
        echo_q = f" Ik verloor {key_element} — hoeveel verlies jij? Type het getal ↓"
    elif "procent" in key_element or "%" in key_element:
        echo_q = f" Bij mij was het {key_element} — bij jou? Type het getal ↓"
    else:
        echo_q = f" Ik verloor {key_element} — hoeveel verlies jij? Type het getal ↓"

    scene4["voiceover"] = (prefix + echo_q).strip() if prefix else echo_q.strip()

    # Fix ook full_voiceover_text
    full_vo = script.get("full_voiceover_text", "")
    if full_vo:
        full_tail = full_vo.rstrip()[-100:].lower()
        if "?" not in full_tail or not key_num or key_num not in full_tail:
            fv = full_vo.rstrip()
            fv_last_q = fv.rfind("?")
            if fv_last_q >= 0:
                fv_sent_start = fv.rfind(".", 0, fv_last_q)
                fv_prefix = fv[:fv_sent_start + 1].rstrip() if fv_sent_start >= 0 else ""
            else:
                fv_last_dot = fv.rfind(".")
                fv_prefix = fv[:fv_last_dot + 1].rstrip() if fv_last_dot >= 0 else fv
            script["full_voiceover_text"] = (fv_prefix + echo_q).strip() if fv_prefix else echo_q.strip()

    logger.debug(f"[Pipeline] Echo-loop afgedwongen: '{echo_q.strip()}'")
    return script


def _score_and_pick_idea(ideas: list[dict], idea_index: int, memory: dict) -> dict:
    """Score ideas op relevantie en kies de beste (of de door gebruiker gekozen index).

    v2: Scoring is bewust MINDER biased naar brand memory top_hooks.
    Voorheen: +15 per matching hook (+45 mogelijk) + +20 format = altijd hetzelfde.
    Nu: +5 per hook (max +10) + +8 format + random jitter = meer variatie.
    """
    import random

    ideas = [i for i in ideas if isinstance(i, dict)]
    if not ideas:
        raise RuntimeError("Geen geldige ideeën beschikbaar na validatie (alle elementen zijn geen dict)")
    if idea_index > 0:
        return ideas[min(idea_index, len(ideas) - 1)]

    top_hooks = [h.lower() for h in memory.get("top_performing_hooks", [])]
    avoided = [a.lower() for a in memory.get("avoided_topics", [])]
    perf_history = memory.get("performance_history", {})
    best_format = (
        (perf_history.get("best_post_type", "") if isinstance(perf_history, dict) else "")
        or memory.get("content_formats", {}).get("best_performing", "")
    ).lower()

    scored = []
    for idea in ideas:
        score = 50  # Base score
        title = (idea.get("title", "") or "").lower()
        hook = (idea.get("hook", "") or "").lower()
        fmt = (idea.get("content_format", "") or "").lower()
        goal = (idea.get("goal", "") or "").lower()
        hook_type = (idea.get("hook_type", "") or "").lower()

        # Kleine bonus voor overlap met top hooks (was +15, nu +5, max +10)
        hook_bonus = 0
        for top in top_hooks:
            overlap = len(set(top.split()) & set((title + " " + hook).split()))
            if overlap >= 2:
                hook_bonus += 5
        score += min(hook_bonus, 10)  # Cap: max +10 ipv onbeperkt

        # Penalty: bevat avoided topics
        for avoid in avoided:
            if avoid in title or avoid in hook:
                score -= 30

        # Kleine bonus voor matching format (was +20, nu +8)
        if best_format and best_format in fmt:
            score += 8

        # Bonus: engagement goals
        if goal in ("engagement", "conversion"):
            score += 8
        elif goal == "awareness":
            score += 4

        # Bonus: heeft een duidelijke hook
        if hook and len(hook) > 10:
            score += 8

        # NIEUW: Bonus voor diversiteit in hook_type
        # Minder gebruikte hook types krijgen een boost
        if hook_type in ("controversy", "contrast", "pov_herkenning"):
            score += 12  # Stimuleer variatie
        elif hook_type == "pattern_interrupt":
            score += 8

        # NIEUW: Random jitter (+/- 10 punten) voorkomt dat ranking deterministisch is
        score += random.randint(-10, 10)

        # Bonus voor hoge viral_potential inschatting
        perf = (idea.get("estimated_performance", "") or "").lower()
        if perf == "high":
            score += 6

        idea["_score"] = score
        scored.append((score, idea))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _determine_video_type(
    idea: dict,
    app: dict | None = None,
    memory: dict | None = None,
    forced_content_format: str | None = None,
) -> str:
    """
    Bepaal video-type op basis van content format en beschikbare providers.

    Kwaliteit eerst:
    - als er een echte product-URL is, stuur dan naar mixed/screen_demo zodat
      de bestaande demo- en screenshot-flow actief kan worden
    - talking_head alleen als dat format expliciet gevraagd wordt
    """
    # Geforceerd format overschrijft het idee's eigen format (bijv. via API content_format param)
    if forced_content_format:
        idea = dict(idea or {})
        idea["content_format"] = forced_content_format

    content_format = (idea.get("content_format", "problem-solution") or "").lower()
    app_url = (
        (memory or {}).get("url")
        or (app or {}).get("url")
        or ""
    ).strip()
    has_app_visuals = bool(app_url)
    did_enabled = bool(os.getenv("DID_API_KEY")) and os.getenv("DID_SKIP", "false").lower() != "true"

    # Alle formats die de app visueel moeten laten zien als app_url beschikbaar is
    demo_formats = {"problem-solution", "before-after", "tutorial", "social-proof", "trend"}
    if has_app_visuals and content_format in demo_formats:
        return "mixed" if did_enabled else "screen_demo"

    if content_format == "talking-head":
        return "talking_head"

    format_map = {
        "problem-solution": "screen_demo",
        "before-after": "screen_demo",
        "tutorial": "screen_demo",
        "social-proof": "screen_demo",  # Was text_on_screen — app moet zichtbaar zijn
        "trend": "mixed",
        "talking-head": "talking_head",
    }

    fallback = format_map.get(content_format, "screen_demo")
    if fallback == "screen_demo" and has_app_visuals and did_enabled:
        return "mixed"
    return fallback
