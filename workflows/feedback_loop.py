"""
Feedback Loop Orchestrator

Verbindt de campaign_pipeline met de analytics laag.
Dit bestand integreert de twee helften van het systeem:
  - campaign_pipeline.py  → content genereren + publiceren
  - learning_engine.py    → leren van wat gepubliceerd is

INTEGRATIEPUNTEN MET BESTAANDE CODE:

1. Na publicatie in approval_service.py:
   feedback_loop.schedule_post_check(post_id, campaign_id, app_id, published_at)

2. Wekelijkse batch-analyse via scheduler.py

3. Handmatige trigger via CLI:
   python cli.py run-feedback --app app_001

VEILIGHEIDSMECHANISMEN (sectie 7):

a) MINIMUM DATA VEREISTEN
   - Minder dan 3 posts → analyse uitvoeren maar confidence=LOW voor alles
   - Learnings met LOW confidence worden NIET in brand memory geschreven
   - Learnings met LOW confidence worden WEL bewaard in learning_store voor accumulatie

b) OVERFITTING PREVENTIE
   - Learnings vervallen na 90 dagen (markt verandert)
   - Score-history window: max 100 posts (sliding window, geen eeuwigdurende bias)
   - Benchmark reset trigger: als avg_views > 10x baseline → herbereken baseline

c) VERKEERDE CONCLUSIES VOORKOMEN
   - Alle LLM-output wordt gevalideerd in analyst_agent._validate_and_enrich_learnings()
   - Confidence wordt gedegradeerd als sample_size < drempelwaarden
   - Learnings zonder kwantitatief evidence worden gefilterd
   - Brand memory writes: alleen HIGH confidence (bevestigd door meerdere datapunten)

d) MENSELIJK TOEZICHT
   - Wekelijks rapport gegenereerd en beschikbaar in Control Center
   - Grote brand memory wijzigingen worden gelogd in approval_history.log
   - Gebruiker kan via CLI learnings inzien en handmatig terugdraaien

e) CIRCUIT BREAKER
   - Als 3+ opeenvolgende API-calls mislukken → pause tot volgende dag
   - Als scoring vreemde patronen detecteert (bijv. alle posts score 0) → alarm + skip
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from analytics.learning_engine import LearningEngine
from analytics.models import ExperimentTags, Platform

ROOT = Path(__file__).parent.parent
SCHEDULED_CHECKS_FILE = ROOT / "data" / "analytics" / "scheduled_checks.json"


# ──────────────────────────────────────────────
# POST-PUBLICATIE REGISTRATIE
# ──────────────────────────────────────────────

def schedule_post_check(
    post_id: str,
    campaign_id: str,
    app_id: str,
    published_at: datetime,
    platform: str = "tiktok",
    experiment_tags: ExperimentTags | None = None,
    predicted_viral_score: float | None = None,
    predicted_realness_score: float | None = None,
) -> None:
    """
    Registreer een gepubliceerde post voor toekomstige analytics.
    Roep dit aan vanuit approval_service._publish_now() direct na succesvolle publicatie.

    Planningstrategie:
      24u later:  Eerste check (vroege performance)
      48u later:  Tweede check (geconsolideerd)
      7d later:   Derde check (long-tail, optioneel)
    """
    checks = _load_scheduled_checks()

    entry = {
        "post_id": post_id,
        "campaign_id": campaign_id,
        "app_id": app_id,
        "published_at": published_at.isoformat(),
        "check_at_24h": (published_at + timedelta(hours=24)).isoformat(),
        "check_at_48h": (published_at + timedelta(hours=48)).isoformat(),
        "check_at_7d": (published_at + timedelta(days=7)).isoformat(),
        "checked_24h": False,
        "checked_48h": False,
        "checked_7d": False,
        "platform": platform,
        "experiment_tags": experiment_tags.model_dump() if experiment_tags else None,
        "predicted_viral_score": predicted_viral_score,
        "predicted_realness_score": predicted_realness_score,
        "registered_at": datetime.utcnow().isoformat(),
    }

    # Voorkom duplicaten
    existing_ids = [c["post_id"] for c in checks]
    if post_id not in existing_ids:
        checks.append(entry)
        _save_scheduled_checks(checks)
        logger.info(f"[FeedbackLoop] Post {post_id} ingepland voor analytics checks")


def run_due_checks(app_id: str | None = None) -> dict:
    """
    Voer alle checks uit die nu uitgevoerd moeten worden.
    Roep dit periodiek aan (bijv. elk uur via scheduler).

    Args:
        app_id: Optioneel — filter op specifieke app

    Returns:
        Samenvatting van uitgevoerde checks
    """
    now = datetime.utcnow()
    checks = _load_scheduled_checks()
    engine = LearningEngine()

    results = {"checks_run": 0, "errors": []}

    for check in checks:
        if app_id and check["app_id"] != app_id:
            continue

        check_app_id = check["app_id"]
        post_id = check["post_id"]
        published_at = datetime.fromisoformat(check["published_at"])

        from analytics.models import Platform as AnalyticsPlatform
        platform_str = check.get("platform", "tiktok")
        try:
            platform = AnalyticsPlatform(platform_str)
        except ValueError:
            platform = AnalyticsPlatform.TIKTOK

        experiment_tags = None
        if check.get("experiment_tags"):
            experiment_tags = ExperimentTags(**check["experiment_tags"])

        predicted_viral = check.get("predicted_viral_score")
        predicted_realness = check.get("predicted_realness_score")

        _check_kwargs = dict(
            post_id=post_id,
            campaign_id=check["campaign_id"],
            app_id=check_app_id,
            published_at=published_at,
            platform=platform,
            experiment_tags=experiment_tags,
            predicted_viral_score=predicted_viral,
            predicted_realness_score=predicted_realness,
        )

        # 24u check
        if not check["checked_24h"] and now >= datetime.fromisoformat(check["check_at_24h"]):
            try:
                engine.process_single_post(**_check_kwargs)
                check["checked_24h"] = True
                results["checks_run"] += 1
                logger.info(f"[FeedbackLoop] 24u check klaar voor {post_id}")
            except Exception as e:
                results["errors"].append(f"24h check {post_id}: {e}")

        # 48u check
        elif check["checked_24h"] and not check["checked_48h"] and now >= datetime.fromisoformat(check["check_at_48h"]):
            try:
                engine.process_single_post(**_check_kwargs)
                check["checked_48h"] = True
                results["checks_run"] += 1
                logger.info(f"[FeedbackLoop] 48u check klaar voor {post_id}")
            except Exception as e:
                results["errors"].append(f"48h check {post_id}: {e}")

        # 7d check
        elif check["checked_48h"] and not check["checked_7d"] and now >= datetime.fromisoformat(check["check_at_7d"]):
            try:
                engine.process_single_post(**_check_kwargs)
                check["checked_7d"] = True
                results["checks_run"] += 1
                logger.info(f"[FeedbackLoop] 7d check klaar voor {post_id}")
            except Exception as e:
                results["errors"].append(f"7d check {post_id}: {e}")

    _save_scheduled_checks(checks)

    # Na elke batch checks: run volledige analyse voor apps met nieuwe data
    if results["checks_run"] > 0:
        affected_apps = list(set(
            c["app_id"] for c in checks
            if app_id is None or c["app_id"] == app_id
        ))
        for affected_app in affected_apps:
            try:
                engine.run_cycle(affected_app)
            except Exception as e:
                results["errors"].append(f"Cyclus {affected_app}: {e}")

    return results


def run_weekly_analysis(app_id: str) -> dict:
    """
    Voer een volledige wekelijkse analyse uit.
    Analyseert alle beschikbare data en genereert een uitgebreid rapport.
    """
    logger.info(f"[FeedbackLoop] Start wekelijkse analyse voor {app_id}")
    engine = LearningEngine()
    return engine.run_cycle(app_id, force_reanalyze=True)


def get_learning_summary(app_id: str) -> dict:
    """
    Geef een samenvatting van alle actieve learnings voor een app.
    Gebruikt door de Control Center UI en CLI.
    """
    from analytics.metrics_store import MetricsStore
    ms = MetricsStore()
    learning_store = ms.load_learning_store(app_id)
    benchmark = ms.load_benchmark(app_id)

    active = learning_store.active_learnings()
    return {
        "app_id": app_id,
        "total_posts_analyzed": learning_store.total_posts_analyzed,
        "active_learnings": len(active),
        "high_confidence": len([l for l in active if l.confidence.value == "high"]),
        "medium_confidence": len([l for l in active if l.confidence.value == "medium"]),
        "benchmark": {
            "avg_score": round(benchmark.avg_composite_score, 1),
            "avg_views": int(benchmark.avg_views),
            "total_posts": benchmark.total_posts,
            "best_score": round(benchmark.best_score, 1),
        },
        "top_learnings": [
            {"category": l.category, "finding": l.finding, "confidence": l.confidence}
            for l in learning_store.top_positive(5)
        ],
        "avoid_learnings": [
            {"category": l.category, "finding": l.finding, "confidence": l.confidence}
            for l in learning_store.top_negative(3)
        ],
    }


# ──────────────────────────────────────────────
# PERSISTENTIE
# ──────────────────────────────────────────────

def _load_scheduled_checks() -> list[dict]:
    SCHEDULED_CHECKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SCHEDULED_CHECKS_FILE.exists():
        return []
    with open(SCHEDULED_CHECKS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_scheduled_checks(checks: list[dict]) -> None:
    with open(SCHEDULED_CHECKS_FILE, "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=2, default=str)
