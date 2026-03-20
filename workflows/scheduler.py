"""
Scheduler — tijdgestuurde feedback runs via APScheduler

Start via: python workflows/scheduler.py
Of via deployment/start_system.bat (wordt dan tegelijk met backend gestart)

GEPLANDE TAKEN:
  Elk uur:     run_due_checks()       → analytics van gepubliceerde posts ophalen
  Elke dag:    daily_digest()         → samenvatting in logs
  Elke week:   weekly_analysis()      → volledige patroon-analyse per app
  Elke maand:  monthly_cleanup()      → vervallen learnings opruimen

CIRCUIT BREAKER:
  Als run_due_checks() 3x op rij faalt → pauze 6 uur
"""

import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from backend.repository.factory import get_app_repo

ROOT = Path(__file__).parent.parent

# Circuit breaker state
_consecutive_failures = 0
_MAX_FAILURES = 3


def load_active_app_ids() -> list[str]:
    """Laad alle actieve apps uit de registry."""
    try:
        apps = get_app_repo(tenant_id="default").list_apps()
        return [app["id"] for app in apps if app.get("active", True)]
    except Exception as e:
        logger.error(f"[Scheduler] Kan app registry niet laden: {e}")
        return []


def job_hourly_checks():
    """Elk uur — check of er analytics-metingen gedaan moeten worden."""
    global _consecutive_failures
    from workflows.feedback_loop import run_due_checks

    if _consecutive_failures >= _MAX_FAILURES:
        logger.warning(f"[Scheduler] Circuit breaker actief — {_consecutive_failures} opeenvolgende fouten. Wacht...")
        return

    try:
        apps = load_active_app_ids()
        for app_id in apps:
            result = run_due_checks(app_id=app_id)
            if result.get("checks_run", 0) > 0:
                logger.info(f"[Scheduler] {result['checks_run']} checks uitgevoerd voor {app_id}")

        _consecutive_failures = 0  # Reset bij succes

    except Exception as e:
        _consecutive_failures += 1
        logger.error(f"[Scheduler] Hourly check mislukt ({_consecutive_failures}/{_MAX_FAILURES}): {e}")


def job_daily_digest():
    """Elke dag om 08:00 — samenvatting in logs."""
    from workflows.feedback_loop import get_learning_summary

    logger.info("\n" + "="*50)
    logger.info("DAGELIJKSE SAMENVATTING")
    logger.info("="*50)

    apps = load_active_app_ids()
    for app_id in apps:
        try:
            summary = get_learning_summary(app_id)
            logger.info(
                f"\nApp: {app_id}"
                f"\n  Posts geanalyseerd: {summary['total_posts_analyzed']}"
                f"\n  Actieve learnings: {summary['active_learnings']} "
                f"(HIGH={summary['high_confidence']}, MED={summary['medium_confidence']})"
                f"\n  Gemiddelde score: {summary['benchmark']['avg_score']}"
                f"\n  Gemiddelde views: {summary['benchmark']['avg_views']:,}"
            )
        except Exception as e:
            logger.error(f"[Scheduler] Digest fout voor {app_id}: {e}")


def job_weekly_analysis():
    """Elke maandag om 09:00 — volledige wekelijkse patroon-analyse."""
    from workflows.feedback_loop import run_weekly_analysis

    logger.info("[Scheduler] Start wekelijkse analyses...")
    apps = load_active_app_ids()
    for app_id in apps:
        try:
            result = run_weekly_analysis(app_id)
            logger.success(
                f"[Scheduler] Wekelijkse analyse {app_id}: "
                f"{result.get('learnings_generated', 0)} nieuwe leerpunten"
            )
        except Exception as e:
            logger.error(f"[Scheduler] Wekelijkse analyse mislukt voor {app_id}: {e}")


def job_produce_content(slot: str = "morning"):
    """
    Produceer content voor alle actieve apps en publiceer (of stuur ter goedkeuring).

    Slots: morning (07:00), afternoon (13:00), evening (19:00)
    Gecontroleerd via DAILY_POSTS_PER_APP (standaard 2 — morning + evening).
    """
    from workflows.campaign_pipeline import run_pipeline

    daily_posts = int(os.getenv("DAILY_POSTS_PER_APP", "2"))

    # Slot-filter: met 2 posts/dag alleen morning + evening, met 3 ook afternoon
    active_slots = {
        2: {"morning", "evening"},
        3: {"morning", "afternoon", "evening"},
    }.get(daily_posts, {"morning", "evening"})

    if slot not in active_slots:
        return  # Dit slot is niet actief vandaag

    apps = load_active_app_ids()
    logger.info(f"[Scheduler] Content productie gestart — slot={slot}, apps={len(apps)}")

    for app_id in apps:
        try:
            logger.info(f"[Scheduler] Produceer content voor {app_id} ({slot})")
            bundle = run_pipeline(
                app_id=app_id,
                platform="tiktok",
                on_progress=lambda msg: logger.info(f"  {msg}"),
            )
            logger.success(
                f"[Scheduler] {app_id} — campagne klaar: {bundle.id} "
                f"(status={bundle.status})"
            )
        except Exception as e:
            logger.error(f"[Scheduler] Content productie mislukt voor {app_id}: {e}")


def job_monthly_cleanup():
    """Eerste dag van de maand — ruim vervallen learnings op."""
    from analytics.metrics_store import MetricsStore

    logger.info("[Scheduler] Start maandelijkse opruiming...")
    ms = MetricsStore()
    apps = load_active_app_ids()

    for app_id in apps:
        try:
            store = ms.load_learning_store(app_id)
            before = len(store.learnings)
            store.learnings = store.active_learnings(max_age_days=90)
            after = len(store.learnings)
            ms.save_learning_store(store)
            logger.info(f"[Scheduler] {app_id}: {before - after} vervallen learnings verwijderd")
        except Exception as e:
            logger.error(f"[Scheduler] Cleanup fout voor {app_id}: {e}")


def job_refresh_tiktok_token():
    """Vernieuw het TikTok access token dagelijks via het refresh token."""
    try:
        from channels.tiktok.publisher import _refresh_access_token
        new_token = _refresh_access_token()
        if new_token:
            logger.success("[Scheduler] TikTok token vernieuwd")
        else:
            logger.warning("[Scheduler] TikTok token refresh mislukt — handmatig vernieuwen via dashboard")
    except Exception as e:
        logger.error(f"[Scheduler] TikTok token refresh fout: {e}")


def start():
    """Start de scheduler — blokkerend (draait in eigen process)."""
    scheduler = BlockingScheduler(timezone="Europe/Amsterdam")

    # Content productie — 3 dagelijkse slots (morning/afternoon/evening)
    # DAILY_POSTS_PER_APP bepaalt hoeveel slots actief zijn (2 of 3)
    for slot_name, slot_hour in [("morning", 7), ("afternoon", 13), ("evening", 19)]:
        scheduler.add_job(
            job_produce_content,
            trigger=CronTrigger(hour=slot_hour, minute=0),
            id=f"produce_{slot_name}",
            name=f"Content productie ({slot_name})",
            kwargs={"slot": slot_name},
            replace_existing=True,
        )

    # Elk uur analytics ophalen
    scheduler.add_job(
        job_hourly_checks,
        trigger=IntervalTrigger(hours=1),
        id="hourly_checks",
        name="Analytics checks",
        replace_existing=True,
    )

    # Dagelijkse samenvatting om 08:00
    scheduler.add_job(
        job_daily_digest,
        trigger=CronTrigger(hour=8, minute=0),
        id="daily_digest",
        name="Dagelijkse samenvatting",
        replace_existing=True,
    )

    # Wekelijkse analyse elke maandag 09:00
    scheduler.add_job(
        job_weekly_analysis,
        trigger=CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_analysis",
        name="Wekelijkse patroon-analyse",
        replace_existing=True,
    )

    # TikTok token auto-refresh — dagelijks 06:00 (voor content productie)
    scheduler.add_job(
        job_refresh_tiktok_token,
        trigger=CronTrigger(hour=6, minute=0),
        id="tiktok_token_refresh",
        name="TikTok token vernieuwen",
        replace_existing=True,
    )

    # Maandelijkse opruiming — eerste dag van de maand 03:00
    scheduler.add_job(
        job_monthly_cleanup,
        trigger=CronTrigger(day=1, hour=3, minute=0),
        id="monthly_cleanup",
        name="Maandelijkse opruiming",
        replace_existing=True,
    )

    # Graceful shutdown op Ctrl+C
    def shutdown(sig, frame):
        logger.info("[Scheduler] Afsluiten...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("="*50)
    logger.info("AY Marketing OS — Scheduler gestart")
    logger.info(f"Timezone: Europe/Amsterdam")
    logger.info(f"Geplande taken: {len(scheduler.get_jobs())}")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")
    logger.info("="*50)

    scheduler.start()


def _write_pid_file() -> None:
    """Schrijf PID naar bestand — gebruikt door health checker."""
    pid_file = ROOT / "data" / "health" / "scheduler.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))


def _remove_pid_file() -> None:
    pid_file = ROOT / "data" / "health" / "scheduler.pid"
    if pid_file.exists():
        pid_file.unlink()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    # Stel logging in
    from observability.logger import setup_logging
    setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))

    _write_pid_file()
    try:
        start()
    finally:
        _remove_pid_file()
