"""
Feedback Injector

Vertaalt gevalideerde leerpunten naar:
1. Brand Memory updates (persistente kennis)
2. Prompt-context injecties (tijdelijke sturing per campagne-run)
3. Experiment-tags voor A/B tracking

ONTWERP PRINCIPES:
- Directe brand memory writes: alleen HIGH confidence learnings
- MEDIUM confidence: opgeslagen in feedback_context, meegestuurd als suggestie
- LOW confidence: opgeslagen maar NIET actief ingespoten in prompts

VOORBEELD PROMPT INJECTIES (zie format_*_context methoden):

idea_generator krijgt:
  "GELEERDE PATRONEN (gebruik dit actief):
   - Question-hooks presteren 34% beter → geef voorkeur aan question-hooks
   - Vermijd: opening met prijs"

script_writer krijgt:
  "TOP PATRONEN UIT DATA:
   - Completion rate het hoogst bij 35-45 seconden
   - Save-rate hoog bij posts die een 'cheat code' of quick tip bevatten"

caption_writer krijgt:
  "CAPTION LEARNINGS:
   - Story-stijl captions leveren 2x meer profiel-bezoeken dan minimale captions
   - Gebruik CTAs die urgentie creëren (beperkt, nu, vandaag)"
"""

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from agents import brand_memory as bm
from analytics.metrics_store import MetricsStore
from analytics.models import LearningConfidence, LearningEntry, LearningStore, Platform

ROOT = Path(__file__).parent.parent
FEEDBACK_CONTEXT_DIR = ROOT / "data" / "analytics" / "learnings"

store = MetricsStore()


# ──────────────────────────────────────────────
# HOOFDFUNCTIE
# ──────────────────────────────────────────────

def inject_learnings(
    app_id: str,
    learnings: list[LearningEntry],
    brand_memory_updates: dict,
    platform: Platform = Platform.TIKTOK,
) -> dict:
    """
    Verwerk leerpunten en schrijf ze terug naar alle lagen.

    Args:
        app_id: App ID
        learnings: Gevalideerde LearningEntry objecten van AnalystAgent
        brand_memory_updates: Dict met directe brand memory wijzigingen
        platform: Doelplatform

    Returns:
        Samenvatting van wat er geüpdatet is
    """
    results = {
        "brand_memory_updated": False,
        "learning_store_updated": False,
        "high_confidence_count": 0,
        "medium_confidence_count": 0,
        "low_confidence_count": 0,
        "new_prompt_injections": [],
    }

    # Stap 1: Update brand memory (alleen bewezen feiten)
    _update_brand_memory(app_id, learnings, brand_memory_updates, results)

    # Stap 2: Update learning store (alle learnings met deduplicatie)
    _update_learning_store(app_id, learnings, platform, results)

    # Stap 3: Genereer prompt-context bestanden
    _write_prompt_context(app_id, learnings)

    logger.success(
        f"[FeedbackInjector] Injectie klaar voor {app_id}: "
        f"HIGH={results['high_confidence_count']} | "
        f"MED={results['medium_confidence_count']} | "
        f"LOW={results['low_confidence_count']}"
    )
    return results


# ──────────────────────────────────────────────
# BRAND MEMORY UPDATE
# ──────────────────────────────────────────────

def _update_brand_memory(
    app_id: str,
    learnings: list[LearningEntry],
    direct_updates: dict,
    results: dict,
) -> None:
    """
    Schrijf ALLEEN hoog-confidence learnings naar brand memory.

    Rationale: Brand memory is de langetermijngeheugen van het systeem.
    Hier schrijf je alleen wat bewezen en herhaald is.
    """
    memory = bm.load(app_id)
    memory_changed = False

    # Directe updates van de analyst (bijv. beste hook, tijdstip)
    if direct_updates:
        bm.apply_updates(app_id, direct_updates)
        memory_changed = True

    # HIGH confidence positives → direct in brand memory
    high_positives = [
        l for l in learnings
        if l.confidence == LearningConfidence.HIGH and l.type == "positive"
    ]
    for learning in high_positives:
        if learning.category == "hook" and learning.finding:
            # Voeg beste hook toe aan top_performing_hooks
            hook_text = _extract_hook_text(learning)
            if hook_text:
                bm.add_insight(app_id, f"[BEWEZEN] {learning.finding}")
                memory_changed = True

    # HIGH confidence negatives → toevoegen aan avoided_topics
    high_negatives = [
        l for l in learnings
        if l.confidence == LearningConfidence.HIGH and l.type == "negative"
    ]
    for learning in high_negatives:
        avoided = _extract_avoid_text(learning)
        if avoided:
            current = bm.load(app_id)
            if avoided not in current.get("avoided_topics", []):
                bm.apply_updates(app_id, {"add_to_avoided": [avoided]})
                memory_changed = True

    if memory_changed:
        results["brand_memory_updated"] = True
        logger.info(f"[FeedbackInjector] Brand memory geüpdatet voor {app_id}")


# ──────────────────────────────────────────────
# LEARNING STORE UPDATE
# ──────────────────────────────────────────────

def _update_learning_store(
    app_id: str,
    new_learnings: list[LearningEntry],
    platform: Platform,
    results: dict,
) -> None:
    """
    Voeg nieuwe learnings toe aan de cumulative store.
    Dedupliceer op basis van category + type combinatie.
    Bij duplicaat: verhoog times_confirmed in plaats van dupliceren.
    """
    learning_store = store.load_learning_store(app_id, platform)

    for new_learning in new_learnings:
        # Tel per confidence niveau
        if new_learning.confidence == LearningConfidence.HIGH:
            results["high_confidence_count"] += 1
        elif new_learning.confidence == LearningConfidence.MEDIUM:
            results["medium_confidence_count"] += 1
        else:
            results["low_confidence_count"] += 1

        # Zoek naar bestaand gelijksoortig learning
        existing = _find_similar_learning(learning_store, new_learning)
        if existing:
            # Bevestig bestaand learning
            existing.times_confirmed += 1
            existing.last_confirmed_at = datetime.utcnow()
            # Upgrade confidence als het vaker bevestigd is
            if existing.times_confirmed >= 5 and existing.confidence != LearningConfidence.HIGH:
                existing.confidence = LearningConfidence.HIGH
                logger.info(f"[FeedbackInjector] Learning gepromoveerd naar HIGH: {existing.id}")
        else:
            learning_store.learnings.append(new_learning)

    learning_store.total_posts_analyzed += 1
    store.save_learning_store(learning_store)
    results["learning_store_updated"] = True


def _find_similar_learning(
    store: LearningStore,
    new: LearningEntry,
) -> LearningEntry | None:
    """
    Zoek naar een bestaand learning met dezelfde categorie en type.
    Eenvoudige matching op basis van category + type + eerste 50 tekens van finding.
    """
    for existing in store.learnings:
        if (
            existing.category == new.category
            and existing.type == new.type
            and existing.finding[:50] == new.finding[:50]
        ):
            return existing
    return None


# ──────────────────────────────────────────────
# PROMPT CONTEXT SCHRIJVEN
# ──────────────────────────────────────────────

def _write_prompt_context(app_id: str, learnings: list[LearningEntry]) -> None:
    """
    Schrijf prompt-context bestanden die agents laden bij elke run.
    Gesplitst per agent-type.
    """
    ctx_dir = FEEDBACK_CONTEXT_DIR / app_id
    ctx_dir.mkdir(parents=True, exist_ok=True)

    # Idea generator context
    idea_ctx = format_idea_generator_context(app_id, learnings)
    (ctx_dir / "idea_generator_context.txt").write_text(idea_ctx, encoding="utf-8")

    # Script writer context
    script_ctx = format_script_writer_context(app_id, learnings)
    (ctx_dir / "script_writer_context.txt").write_text(script_ctx, encoding="utf-8")

    # Caption writer context
    caption_ctx = format_caption_writer_context(app_id, learnings)
    (ctx_dir / "caption_writer_context.txt").write_text(caption_ctx, encoding="utf-8")

    logger.info(f"[FeedbackInjector] Prompt-context bestanden geschreven voor {app_id}")


def load_agent_context(app_id: str, agent_type: str) -> str:
    """
    Laad de prompt-context voor een specifieke agent.
    Roep dit aan in de agents bij het opbouwen van de system prompt.

    Args:
        app_id: App ID
        agent_type: "idea_generator" | "script_writer" | "caption_writer"

    Returns:
        Prompt-context tekst (leeg als niet beschikbaar)
    """
    path = FEEDBACK_CONTEXT_DIR / app_id / f"{agent_type}_context.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# ──────────────────────────────────────────────
# FORMAT FUNCTIES — PROMPT INJECTIES
# ──────────────────────────────────────────────

def format_idea_generator_context(app_id: str, learnings: list[LearningEntry]) -> str:
    """
    Formatteer learnings als context voor de idea generator.

    Focus: hook-types, content formats, timing.
    """
    lines = ["=== GELEERDE PATRONEN UIT PERFORMANCE DATA ==="]
    lines.append("(Gebruik dit actief bij het genereren van campagne-ideeën)\n")

    hook_learnings = _filter_by_category(learnings, "hook")
    format_learnings = _filter_by_category(learnings, "content_format")
    timing_learnings = _filter_by_category(learnings, "timing")

    if hook_learnings:
        lines.append("HOOK INZICHTEN:")
        for l in hook_learnings[:3]:
            icon = "✅" if l.type == "positive" else "❌"
            lines.append(f"  {icon} {l.prompt_instruction or l.action}")

    if format_learnings:
        lines.append("\nCONTENT FORMAT INZICHTEN:")
        for l in format_learnings[:3]:
            icon = "✅" if l.type == "positive" else "❌"
            lines.append(f"  {icon} {l.prompt_instruction or l.action}")

    if timing_learnings:
        lines.append("\nTIMING INZICHTEN:")
        for l in timing_learnings[:2]:
            lines.append(f"  ⏰ {l.prompt_instruction or l.action}")

    if not any([hook_learnings, format_learnings, timing_learnings]):
        lines.append("(Nog niet genoeg data — gebruik algemene best practices)")

    return "\n".join(lines)


def format_script_writer_context(app_id: str, learnings: list[LearningEntry]) -> str:
    """
    Formatteer learnings als context voor de script writer.

    Focus: videoduur, video-type, engagement-patronen, CTA.
    """
    lines = ["=== SCRIPT OPTIMALISATIE OP BASIS VAN PERFORMANCE DATA ===\n"]

    duration_learnings = _filter_by_category(learnings, "duration")
    video_type_learnings = _filter_by_category(learnings, "video_type")
    cta_learnings = _filter_by_category(learnings, "cta")

    if duration_learnings:
        lines.append("VIDEODUUR:")
        for l in duration_learnings[:2]:
            lines.append(f"  📏 {l.prompt_instruction or l.finding}")

    if video_type_learnings:
        lines.append("\nVIDEO TYPE:")
        for l in video_type_learnings[:2]:
            icon = "✅" if l.type == "positive" else "❌"
            lines.append(f"  {icon} {l.prompt_instruction or l.action}")

    if cta_learnings:
        lines.append("\nCTA EFFECTIVITEIT:")
        for l in cta_learnings[:2]:
            lines.append(f"  🎯 {l.prompt_instruction or l.action}")

    save_learnings = [l for l in learnings if "save" in l.finding.lower() and l.type == "positive"]
    if save_learnings:
        lines.append("\nSAVE-RATE BOOSTERS (hoge saves = algoritme boost):")
        for l in save_learnings[:2]:
            lines.append(f"  💾 {l.prompt_instruction or l.action}")

    if len(lines) <= 2:
        lines.append("(Nog niet genoeg data — gebruik algemene script best practices)")

    return "\n".join(lines)


def format_caption_writer_context(app_id: str, learnings: list[LearningEntry]) -> str:
    """
    Formatteer learnings als context voor de caption writer.

    Focus: caption-stijl, hashtag-strategie, CTA-conversie.
    """
    lines = ["=== CAPTION OPTIMALISATIE OP BASIS VAN DATA ===\n"]

    caption_learnings = _filter_by_category(learnings, "caption")
    cta_learnings = _filter_by_category(learnings, "cta")

    if caption_learnings:
        lines.append("CAPTION STIJL:")
        for l in caption_learnings[:3]:
            icon = "✅" if l.type == "positive" else "❌"
            lines.append(f"  {icon} {l.prompt_instruction or l.finding}")

    if cta_learnings:
        lines.append("\nCTA DIE WERKT:")
        for l in cta_learnings[:2]:
            lines.append(f"  🎯 {l.prompt_instruction or l.action}")

    # Profiel-bezoek learnings (conversie-signaal)
    intent_learnings = [l for l in learnings if "profiel" in l.finding.lower() or "profile" in l.finding.lower()]
    if intent_learnings:
        lines.append("\nCONVERSIE-INZICHTEN:")
        for l in intent_learnings[:2]:
            lines.append(f"  📊 {l.prompt_instruction or l.action}")

    if len(lines) <= 2:
        lines.append("(Nog niet genoeg data — gebruik algemene caption best practices)")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# HULPFUNCTIES
# ──────────────────────────────────────────────

def _filter_by_category(
    learnings: list[LearningEntry],
    category: str,
    min_confidence: LearningConfidence = LearningConfidence.LOW,
) -> list[LearningEntry]:
    """Filter learnings op categorie en minimum confidence."""
    order = {LearningConfidence.LOW: 0, LearningConfidence.MEDIUM: 1, LearningConfidence.HIGH: 2}
    return [
        l for l in learnings
        if l.category == category
        and order[l.confidence] >= order[min_confidence]
    ]


def _extract_hook_text(learning: LearningEntry) -> str:
    """Probeer een concrete hook-tekst te extraheren uit een learning."""
    for key in ["best_hook", "example", "hook_text"]:
        if key in learning.evidence:
            return str(learning.evidence[key])
    return ""


def _extract_avoid_text(learning: LearningEntry) -> str:
    """Extraheer een concrete 'vermijd' instructie uit een negatieve learning."""
    if learning.prompt_instruction:
        return learning.prompt_instruction[:100]
    return learning.action[:100] if learning.action else ""
