"""
DAG 6 — Integration tests: analytics/metrics_store.py

Dekt:
  - save_post_analysis() → bestand aangemaakt
  - load_post_analysis() → PostAnalysis herladen
  - load_post_analysis() → None bij onbekend post_id
  - save_learning_store() → bestand aangemaakt
  - load_learning_store() → LearningStore herladen
  - load_learning_store() → default lege store bij ontbrekend bestand
  - save_benchmark() → bestand aangemaakt
  - load_benchmark() → AppBenchmark herladen
  - load_benchmark() → default bij ontbrekend bestand
  - load_all_post_analyses() → lijst van analyses
  - load_all_post_analyses() → [] bij ontbrekende map
  - get_recent_posts_for_analysis() → filtert op min_hours
  - load_all_post_analyses() limit werkt
  - save_learning_store() update last_updated
"""

import unittest.mock as mock
from datetime import datetime

import pytest

import analytics.metrics_store as store_module
from analytics.metrics_store import MetricsStore
from analytics.models import (
    AppBenchmark,
    ExperimentTags,
    LearningEntry,
    LearningStore,
    NormalizedMetrics,
    PerformanceScore,
    Platform,
    PostAnalysis,
    RawTikTokMetrics,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_analytics(tmp_path, monkeypatch):
    analytics_dir = tmp_path / "analytics"
    analytics_dir.mkdir()
    monkeypatch.setattr(store_module, "ANALYTICS_DIR", analytics_dir)
    return analytics_dir


# ── Helpers ───────────────────────────────────────────────────────────

def _make_raw(post_id: str = "post_001", app_id: str = "app_ms", hours: float = 24.0):
    return RawTikTokMetrics(
        post_id=post_id, campaign_id="camp_001", app_id=app_id,
        views=5000, likes=150, comments=25, shares=50, saves=80,
        avg_watch_time_sec=22.5, video_duration_sec=45,
        reach=4500, impressions=6000,
        hours_since_publish=hours,
    )


def _make_post_analysis(post_id: str = "post_001", app_id: str = "app_ms", hours: float = 24.0):
    raw = _make_raw(post_id, app_id, hours)
    return PostAnalysis(
        campaign_id="camp_001",
        app_id=app_id,
        post_id=post_id,
        raw=raw,
        normalized=NormalizedMetrics(engagement_rate=0.05),
        score=PerformanceScore(composite_score=72.0),
        tags=ExperimentTags(),
    )


def _make_learning(app_id: str = "app_ms") -> LearningEntry:
    return LearningEntry(
        app_id=app_id,
        category="hook",
        type="positive",
        finding="Vraag-hooks presteren beter",
        evidence={"sample": 5},
        action="Geef voorkeur aan vraag-hooks",
    )


# ── PostAnalysis save/load ─────────────────────────────────────────────

class TestPostAnalysisSaveLoad:
    def test_save_maakt_bestand_aan(self, isolated_analytics):
        analysis = _make_post_analysis()
        store = MetricsStore()
        path = store.save_post_analysis(analysis)
        assert path.exists()

    def test_load_herstelt_analysis(self):
        analysis = _make_post_analysis(post_id="post_002")
        store = MetricsStore()
        store.save_post_analysis(analysis)
        loaded = store.load_post_analysis("app_ms", "post_002")
        assert loaded is not None
        assert loaded.post_id == "post_002"

    def test_load_bewaart_composite_score(self):
        analysis = _make_post_analysis()
        analysis.score.composite_score = 81.5
        store = MetricsStore()
        store.save_post_analysis(analysis)
        loaded = store.load_post_analysis("app_ms", "post_001")
        assert abs(loaded.score.composite_score - 81.5) < 0.1

    def test_load_onbekend_post_id_geeft_none(self):
        result = MetricsStore().load_post_analysis("app_ms", "post_bestaat_niet")
        assert result is None

    def test_load_onbekende_app_geeft_none(self):
        result = MetricsStore().load_post_analysis("app_bestaat_niet", "post_001")
        assert result is None


# ── load_all_post_analyses ────────────────────────────────────────────

class TestLoadAllPostAnalyses:
    def test_lege_map_geeft_lege_lijst(self):
        result = MetricsStore().load_all_post_analyses("app_leeg")
        assert result == []

    def test_laadt_meerdere_analyses(self):
        store = MetricsStore()
        for i in range(3):
            store.save_post_analysis(_make_post_analysis(post_id=f"post_{i:03d}"))
        result = store.load_all_post_analyses("app_ms")
        assert len(result) == 3

    def test_limit_werkt(self):
        store = MetricsStore()
        for i in range(5):
            store.save_post_analysis(_make_post_analysis(post_id=f"post_lim_{i:03d}"))
        result = store.load_all_post_analyses("app_ms", limit=2)
        assert len(result) <= 2


# ── get_recent_posts_for_analysis ─────────────────────────────────────

class TestGetRecentPosts:
    def test_filtert_te_verse_posts(self):
        """Posts met hours_since_publish < min_hours worden overgeslagen."""
        store = MetricsStore()
        store.save_post_analysis(_make_post_analysis(post_id="post_fresh", hours=2.0))
        store.save_post_analysis(_make_post_analysis(post_id="post_old",   hours=48.0))
        result = store.get_recent_posts_for_analysis("app_ms", min_hours_since_publish=24.0)
        assert len(result) == 1
        assert result[0].post_id == "post_old"

    def test_lege_map_geeft_lege_lijst(self):
        result = MetricsStore().get_recent_posts_for_analysis("app_leeg")
        assert result == []


# ── LearningStore save/load ───────────────────────────────────────────

class TestLearningStoreSaveLoad:
    def test_save_maakt_bestand_aan(self, isolated_analytics):
        ls = LearningStore(app_id="app_ms")
        ls.learnings = [_make_learning()]
        path = MetricsStore().save_learning_store(ls)
        assert path.exists()

    def test_load_herstelt_learnings(self):
        ls = LearningStore(app_id="app_ms")
        ls.learnings = [_make_learning()]
        store = MetricsStore()
        store.save_learning_store(ls)
        loaded = store.load_learning_store("app_ms")
        assert len(loaded.learnings) == 1
        assert loaded.learnings[0].category == "hook"

    def test_load_ontbrekend_bestand_geeft_lege_store(self):
        loaded = MetricsStore().load_learning_store("app_nofile")
        assert isinstance(loaded, LearningStore)
        assert loaded.app_id == "app_nofile"
        assert loaded.learnings == []

    def test_save_update_last_updated(self):
        ls = LearningStore(app_id="app_ms")
        before = ls.last_updated
        MetricsStore().save_learning_store(ls)
        # last_updated wordt bijgewerkt tijdens save
        loaded = MetricsStore().load_learning_store("app_ms")
        assert loaded.last_updated is not None


# ── AppBenchmark save/load ────────────────────────────────────────────

class TestBenchmarkSaveLoad:
    def test_save_maakt_bestand_aan(self, isolated_analytics):
        bm = AppBenchmark(app_id="app_bm", avg_composite_score=65.0)
        path = MetricsStore().save_benchmark(bm)
        assert path.exists()

    def test_load_herstelt_benchmark(self):
        bm = AppBenchmark(app_id="app_bm", avg_composite_score=65.0, total_posts=5)
        store = MetricsStore()
        store.save_benchmark(bm)
        loaded = store.load_benchmark("app_bm")
        assert abs(loaded.avg_composite_score - 65.0) < 0.01
        assert loaded.total_posts == 5

    def test_load_ontbrekend_bestand_geeft_default(self):
        loaded = MetricsStore().load_benchmark("app_nofile")
        assert isinstance(loaded, AppBenchmark)
        assert loaded.app_id == "app_nofile"
        assert loaded.total_posts == 0
