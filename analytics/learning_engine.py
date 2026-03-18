"""
Learning Engine

Aggregeert alle analytische componenten tot één werkende feedback cyclus.
Dit is de centrale coordinator die:
  1. Metrics ophaalt en normaliseert
  2. Scores berekent
  3. Analyses uitvoert
  4. Learnings injecteert

Gebruik:
  engine = LearningEngine()
  engine.run_cycle(app_id="app_001")
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from analytics import normalizer, scorer
from analytics.feedback_injector import inject_learnings
from analytics.metrics_store import MetricsStore
from analytics.models import (
    ExperimentTags,
    PostAnalysis,
    Platform,
)
from channels.tiktok.analytics_fetcher import TikTokAnalyticsFetcher
from agents.analyst_agent import AnalystAgent

ROOT = Path(__file__).parent.parent


class LearningEngine:
    """
    Centrale coordinator van de analytics feedback loop.
    """

    def __init__(self):
        self.metrics_store = MetricsStore()
        self.fetcher = TikTokAnalyticsFetcher()
        self.analyst = AnalystAgent()

    def run_cycle(
        self,
        app_id: str,
        platform: Platform = Platform.TIKTOK,
        force_reanalyze: bool = False,
    ) -> dict:
        """
        Voer één volledige feedback cyclus uit.

        Stap 1: Laad gepubliceerde posts die nog geanalyseerd moeten worden
        Stap 2: Fetch metrics van API
        Stap 3: Normaliseer + score
        Stap 4: Sla op
        Stap 5: Analyseer patronen via AnalystAgent
        Stap 6: Injecteer learnings terug in systeem

        Returns:
            Samenvatting van de cyclus
        """
        logger.info(f"\n{'='*50}")
        logger.info(f"[LearningEngine] Start feedback cyclus voor {app_id}")
        logger.info(f"{'='*50}")

        results = {
            "app_id": app_id,
            "run_at": datetime.utcnow().isoformat(),
            "new_posts_analyzed": 0,
            "total_posts_in_store": 0,
            "analysis_performed": False,
            "learnings_generated": 0,
            "errors": [],
        }

        # Stap 1: Laad posts die metrics nodig hebben
        pending_posts = self._load_pending_posts(app_id)
        logger.info(f"[LearningEngine] {len(pending_posts)} posts wachten op metrics")

        # Stap 2 & 3: Fetch + normalize + score per post
        for post_info in pending_posts:
            try:
                analysis = self._process_post(post_info, app_id, platform)
                if analysis:
                    self.metrics_store.save_post_analysis(analysis)
                    self._update_benchmark(analysis)
                    results["new_posts_analyzed"] += 1
            except Exception as e:
                error_msg = f"Fout bij post {post_info.get('post_id', '?')}: {e}"
                logger.error(f"[LearningEngine] {error_msg}")
                results["errors"].append(error_msg)

        # Stap 4: Laad alle geanalyseerde posts voor patroon-analyse
        all_analyses = self.metrics_store.get_recent_posts_for_analysis(
            app_id, min_hours_since_publish=24.0, max_posts=30
        )
        results["total_posts_in_store"] = len(all_analyses)

        if len(all_analyses) < 1:
            logger.info(f"[LearningEngine] Geen posts beschikbaar voor analyse — cyclus klaar")
            return results

        # Stap 5: Analyst Agent
        if len(all_analyses) >= 3 or force_reanalyze:
            logger.info(f"[LearningEngine] Start patroon-analyse over {len(all_analyses)} posts...")
            try:
                existing_store = self.metrics_store.load_learning_store(app_id)
                analysis_result = self.analyst.run(
                    app_id=app_id,
                    post_analyses=all_analyses,
                    existing_store=existing_store,
                )

                if analysis_result:
                    results["analysis_performed"] = True
                    learnings = analysis_result.get("learnings", [])
                    results["learnings_generated"] = len(learnings)

                    # Stap 6: Injecteer learnings
                    injection_results = inject_learnings(
                        app_id=app_id,
                        learnings=learnings,
                        brand_memory_updates=analysis_result.get("brand_memory_updates", {}),
                        platform=platform,
                    )

                    results["injection_summary"] = injection_results
                    self._save_analysis_report(app_id, analysis_result)
                    logger.success(f"[LearningEngine] {len(learnings)} leerpunten gegenereerd en geïnjecteerd")

            except Exception as e:
                error_msg = f"Analyse mislukt: {e}"
                logger.error(f"[LearningEngine] {error_msg}")
                results["errors"].append(error_msg)
        else:
            logger.info(
                f"[LearningEngine] {len(all_analyses)} posts beschikbaar — "
                f"wacht op min. 3 voor patroon-analyse"
            )

        logger.info(f"[LearningEngine] Cyclus klaar: {results}")
        return results

    def process_single_post(
        self,
        post_id: str,
        campaign_id: str,
        app_id: str,
        published_at: datetime,
        experiment_tags: ExperimentTags | None = None,
    ) -> PostAnalysis | None:
        """
        Verwerk één post handmatig (bijv. direct na publicatie plannen).
        """
        post_info = {
            "post_id": post_id,
            "campaign_id": campaign_id,
            "published_at": published_at,
            "experiment_tags": experiment_tags,
        }
        analysis = self._process_post(post_info, app_id, Platform.TIKTOK)
        if analysis:
            self.metrics_store.save_post_analysis(analysis)
            self._update_benchmark(analysis)
        return analysis

    def _process_post(
        self,
        post_info: dict,
        app_id: str,
        platform: Platform,
    ) -> PostAnalysis | None:
        """
        Haal metrics op, normaliseer en bereken score voor één post.
        """
        post_id = post_info["post_id"]
        campaign_id = post_info.get("campaign_id", "unknown")
        published_at = post_info.get("published_at", datetime.utcnow())

        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at)

        # Fetch raw metrics
        raw = self.fetcher.fetch(
            post_id=post_id,
            campaign_id=campaign_id,
            app_id=app_id,
            published_at=published_at,
            experiment_id=post_info.get("experiment_id"),
            experiment_variant=post_info.get("experiment_variant"),
        )

        # Normaliseer
        normalized = normalizer.normalize(raw)

        # Benchmark laden voor relatieve scoring
        benchmark = self.metrics_store.load_benchmark(app_id)

        # Score berekenen
        score = scorer.compute_score(raw, normalized, benchmark)

        # Experiment tags laden of default gebruiken
        tags = post_info.get("experiment_tags") or _default_experiment_tags(post_info)

        analysis = PostAnalysis(
            campaign_id=campaign_id,
            app_id=app_id,
            post_id=post_id,
            platform=platform,
            raw=raw,
            normalized=normalized,
            score=score,
            tags=tags,
        )

        logger.info(
            f"[LearningEngine] Post {post_id}: "
            f"score={score.composite_score:.1f} | "
            f"views={raw.views:,} | "
            f"confidence={score.confidence_level}"
        )
        return analysis

    def _update_benchmark(self, analysis: PostAnalysis) -> None:
        """Update de rolling benchmark van de app."""
        benchmark = self.metrics_store.load_benchmark(analysis.app_id)
        benchmark = scorer.update_benchmark(
            benchmark,
            new_score=analysis.score.composite_score,
            new_views=analysis.raw.views,
        )
        self.metrics_store.save_benchmark(benchmark)

    def _load_pending_posts(self, app_id: str) -> list[dict]:
        """
        Laad gepubliceerde posts uit campagne-data die nog geen metrics hebben.
        """
        campaigns_dir = ROOT / "data" / "campaigns"
        pending = []

        for path in campaigns_dir.glob("*.json"):
            try:
                with open(path, encoding="utf-8") as f:
                    bundle = json.load(f)

                if bundle.get("app_id") != app_id:
                    continue
                if bundle.get("status") != "published":
                    continue
                if not bundle.get("post_id"):
                    continue

                # Check of er al een analyse is
                existing = self.metrics_store.load_post_analysis(
                    app_id, bundle["post_id"]
                )
                if existing:
                    # Check of een nieuwe meting nodig is (48u later)
                    hours = existing.raw.hours_since_publish
                    if hours < 48:
                        pending.append({
                            "post_id": bundle["post_id"],
                            "campaign_id": bundle["id"],
                            "published_at": bundle.get("published_at"),
                        })
                else:
                    pending.append({
                        "post_id": bundle["post_id"],
                        "campaign_id": bundle["id"],
                        "published_at": bundle.get("published_at"),
                    })

            except Exception as e:
                logger.warning(f"[LearningEngine] Kan {path.name} niet verwerken: {e}")

        return pending

    def _save_analysis_report(self, app_id: str, analysis_result: dict) -> None:
        """Sla het analyse-rapport op als wekelijks bestand."""
        from datetime import date
        week = date.today().strftime("%Y_W%W")
        report_dir = ROOT / "data" / "analytics" / "learnings" / app_id
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"learnings_{week}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(analysis_result, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[LearningEngine] Rapport opgeslagen: {path.name}")


def _default_experiment_tags(post_info: dict) -> ExperimentTags:
    """Maak default experiment tags als geen tags beschikbaar zijn."""
    return ExperimentTags(
        hook_type="statement",
        content_format="problem-solution",
        video_type="screen_demo",
        cta_type="link_in_bio",
    )
