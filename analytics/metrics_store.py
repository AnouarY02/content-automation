"""
Metrics Store — lezen en schrijven van analytics data naar schijf.

Opslagstructuur:
  data/analytics/raw/{app_id}/{post_id}_{hours}h_raw.json
  data/analytics/scored/{app_id}/{post_id}_scored.json
  data/analytics/learnings/{app_id}/learnings_cumulative.json
  data/analytics/per_app/{app_id}_benchmark.json
"""

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from analytics.models import (
    AppBenchmark,
    LearningStore,
    NormalizedMetrics,
    PerformanceScore,
    PostAnalysis,
    Platform,
)

ROOT = Path(__file__).parent.parent
ANALYTICS_DIR = ROOT / "data" / "analytics"


class MetricsStore:
    """Persistentie-laag voor alle analytics data."""

    def save_post_analysis(self, analysis: PostAnalysis) -> Path:
        scored_dir = ANALYTICS_DIR / "scored" / analysis.app_id
        scored_dir.mkdir(parents=True, exist_ok=True)
        path = scored_dir / f"{analysis.post_id}_scored.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(analysis.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        logger.debug(f"[MetricsStore] PostAnalysis opgeslagen: {path.name}")
        return path

    def load_post_analysis(self, app_id: str, post_id: str) -> PostAnalysis | None:
        path = ANALYTICS_DIR / "scored" / app_id / f"{post_id}_scored.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            return PostAnalysis(**json.load(f))

    def load_all_post_analyses(self, app_id: str, limit: int = 50) -> list[PostAnalysis]:
        """Laad de meest recente gescoorde posts voor een app."""
        scored_dir = ANALYTICS_DIR / "scored" / app_id
        if not scored_dir.exists():
            return []
        paths = sorted(scored_dir.glob("*_scored.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        analyses = []
        for path in paths[:limit]:
            try:
                with open(path, encoding="utf-8") as f:
                    analyses.append(PostAnalysis(**json.load(f)))
            except Exception as e:
                logger.warning(f"[MetricsStore] Kan {path.name} niet laden: {e}")
        return analyses

    def save_learning_store(self, store: LearningStore) -> Path:
        learn_dir = ANALYTICS_DIR / "learnings" / store.app_id
        learn_dir.mkdir(parents=True, exist_ok=True)
        path = learn_dir / "learnings_cumulative.json"
        store.last_updated = datetime.utcnow()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"[MetricsStore] LearningStore opgeslagen: {len(store.learnings)} learnings")
        return path

    def load_learning_store(self, app_id: str, platform: Platform = Platform.TIKTOK) -> LearningStore:
        path = ANALYTICS_DIR / "learnings" / app_id / "learnings_cumulative.json"
        if not path.exists():
            return LearningStore(app_id=app_id, platform=platform)
        with open(path, encoding="utf-8") as f:
            return LearningStore(**json.load(f))

    def save_benchmark(self, benchmark: AppBenchmark) -> Path:
        per_app_dir = ANALYTICS_DIR / "per_app"
        per_app_dir.mkdir(parents=True, exist_ok=True)
        path = per_app_dir / f"{benchmark.app_id}_benchmark.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(benchmark.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
        return path

    def load_benchmark(self, app_id: str) -> AppBenchmark:
        path = ANALYTICS_DIR / "per_app" / f"{app_id}_benchmark.json"
        if not path.exists():
            return AppBenchmark(app_id=app_id)
        with open(path, encoding="utf-8") as f:
            return AppBenchmark(**json.load(f))

    def get_recent_posts_for_analysis(
        self,
        app_id: str,
        min_hours_since_publish: float = 24.0,
        max_posts: int = 20,
    ) -> list[PostAnalysis]:
        """
        Haal posts op die oud genoeg zijn voor analyse.
        Filtert op min_hours zodat we niet analyseren vóór data stabiel is.
        """
        all_analyses = self.load_all_post_analyses(app_id, limit=max_posts * 3)
        filtered = [
            a for a in all_analyses
            if a.raw.hours_since_publish >= min_hours_since_publish
        ]
        return filtered[:max_posts]
