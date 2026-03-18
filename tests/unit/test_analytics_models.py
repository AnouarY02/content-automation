"""
DAG 6 — Unit tests: analytics/models.py

Dekt:
  - RawTikTokMetrics instantiatie en defaults
  - NormalizedMetrics instantiatie
  - PerformanceScore composite en confidence fields
  - ExperimentTags defaults
  - LearningEntry fields
  - LearningStore.active_learnings() — filtert op max_age_days
  - LearningStore.active_learnings() — behoudt often-confirmed learnings
  - LearningStore.by_category() — filtert op categorie
  - LearningStore.top_positive() — sorteert op times_confirmed
  - LearningStore.top_negative() — sorteert op times_confirmed
  - LearningStore.top_positive() max n
  - AppBenchmark defaults en velden
"""

from datetime import datetime, timedelta

import pytest

from analytics.models import (
    AppBenchmark,
    ContentFormat,
    ExperimentTags,
    LearningConfidence,
    LearningEntry,
    LearningStore,
    NormalizedMetrics,
    PerformanceScore,
    Platform,
    RawTikTokMetrics,
    VideoType,
)


# ── RawTikTokMetrics ──────────────────────────────────────────────────

class TestRawTikTokMetrics:
    def test_instantiatie_minimaal(self):
        raw = RawTikTokMetrics(post_id="post_001", campaign_id="camp_001", app_id="app_001")
        assert raw.post_id == "post_001"
        assert raw.views == 0

    def test_platform_default_tiktok(self):
        raw = RawTikTokMetrics(post_id="p", campaign_id="c", app_id="a")
        assert raw.platform == Platform.TIKTOK

    def test_video_duration_default_45(self):
        raw = RawTikTokMetrics(post_id="p", campaign_id="c", app_id="a")
        assert raw.video_duration_sec == 45

    def test_experiment_fields_default_none(self):
        raw = RawTikTokMetrics(post_id="p", campaign_id="c", app_id="a")
        assert raw.experiment_id is None
        assert raw.experiment_variant is None

    def test_alle_metrics_velden(self):
        raw = RawTikTokMetrics(
            post_id="p", campaign_id="c", app_id="a",
            views=5000, likes=200, comments=30, shares=50, saves=80,
            watch_time_total_sec=112500.0, avg_watch_time_sec=22.5,
            video_duration_sec=45, reach=4800, impressions=6000,
        )
        assert raw.views == 5000
        assert raw.avg_watch_time_sec == 22.5


# ── NormalizedMetrics ─────────────────────────────────────────────────

class TestNormalizedMetrics:
    def test_defaults_zijn_nul(self):
        n = NormalizedMetrics()
        assert n.engagement_rate == 0.0
        assert n.completion_rate == 0.0

    def test_waarden_instelbaar(self):
        n = NormalizedMetrics(engagement_rate=0.05, completion_rate=0.55)
        assert abs(n.engagement_rate - 0.05) < 0.001


# ── PerformanceScore ──────────────────────────────────────────────────

class TestPerformanceScore:
    def test_instantiatie_defaults(self):
        score = PerformanceScore()
        assert score.composite_score == 0.0
        assert score.confidence_level == LearningConfidence.LOW

    def test_alle_subscores_instelbaar(self):
        score = PerformanceScore(
            composite_score=72.5,
            retention_score=68.0,
            engagement_score=75.0,
            virality_score=58.0,
            reach_score=80.0,
            profile_score=45.0,
        )
        assert score.composite_score == 72.5
        assert score.retention_score == 68.0

    def test_percentile_rank_default_none(self):
        score = PerformanceScore()
        assert score.percentile_rank is None


# ── ExperimentTags ────────────────────────────────────────────────────

class TestExperimentTags:
    def test_defaults(self):
        tags = ExperimentTags()
        assert tags.hook_type == "statement"
        assert tags.has_text_overlay is True
        assert tags.posting_hour == 18

    def test_video_type_default(self):
        tags = ExperimentTags()
        assert tags.video_type == VideoType.SCREEN_DEMO

    def test_experiment_fields_none_default(self):
        tags = ExperimentTags()
        assert tags.experiment_id is None
        assert tags.variant is None


# ── LearningStore methodes ────────────────────────────────────────────

def _make_learning(
    app_id: str = "app_lrn",
    category: str = "hook",
    type_: str = "positive",
    times_confirmed: int = 1,
    age_days: int = 0,
) -> LearningEntry:
    created = datetime.utcnow() - timedelta(days=age_days)
    return LearningEntry(
        app_id=app_id,
        category=category,
        type=type_,
        finding=f"Test finding ({category})",
        evidence={"sample_size": 5},
        action="Test actie",
        times_confirmed=times_confirmed,
        created_at=created,
        last_confirmed_at=created,
    )


class TestLearningStoreActiveLearnings:
    def test_verse_learnings_altijd_actief(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [_make_learning(age_days=0)]
        assert len(store.active_learnings()) == 1

    def test_te_oude_learning_uitgesloten(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [_make_learning(age_days=100)]  # > 90 days default
        result = store.active_learnings(max_age_days=90)
        assert len(result) == 0

    def test_oud_maar_often_confirmed_wordt_bewaard(self):
        store = LearningStore(app_id="app_test")
        old_but_confirmed = _make_learning(age_days=100, times_confirmed=4)
        store.learnings = [old_but_confirmed]
        result = store.active_learnings(max_age_days=90)
        assert len(result) == 1  # times_confirmed > 3 → bewaard

    def test_mix_actief_en_oud(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(age_days=10),   # actief
            _make_learning(age_days=100),  # te oud, times_confirmed=1
            _make_learning(age_days=5),    # actief
        ]
        result = store.active_learnings()
        assert len(result) == 2

    def test_lege_store_geeft_lege_lijst(self):
        store = LearningStore(app_id="app_empty")
        assert store.active_learnings() == []


class TestLearningStoreByCategory:
    def test_filtert_op_categorie(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(category="hook"),
            _make_learning(category="cta"),
            _make_learning(category="hook"),
        ]
        result = store.by_category("hook")
        assert len(result) == 2
        assert all(l.category == "hook" for l in result)

    def test_onbekende_categorie_geeft_lege_lijst(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [_make_learning(category="hook")]
        assert store.by_category("bestaat_niet") == []


class TestLearningStoreTopPositive:
    def test_sorteert_op_times_confirmed(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(type_="positive", times_confirmed=1),
            _make_learning(type_="positive", times_confirmed=5),
            _make_learning(type_="positive", times_confirmed=3),
        ]
        result = store.top_positive()
        assert result[0].times_confirmed == 5
        assert result[1].times_confirmed == 3

    def test_negeert_negative_learnings(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(type_="negative", times_confirmed=10),
            _make_learning(type_="positive", times_confirmed=2),
        ]
        result = store.top_positive()
        assert len(result) == 1
        assert result[0].type == "positive"

    def test_max_n_resultaten(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [_make_learning(type_="positive") for _ in range(10)]
        result = store.top_positive(n=3)
        assert len(result) == 3

    def test_lege_store_geeft_lege_lijst(self):
        store = LearningStore(app_id="app_empty")
        assert store.top_positive() == []


class TestLearningStoreTopNegative:
    def test_sorteert_negatief_op_times_confirmed(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(type_="negative", times_confirmed=2),
            _make_learning(type_="negative", times_confirmed=7),
        ]
        result = store.top_negative()
        assert result[0].times_confirmed == 7

    def test_negeert_positive_learnings(self):
        store = LearningStore(app_id="app_test")
        store.learnings = [
            _make_learning(type_="positive", times_confirmed=10),
            _make_learning(type_="negative", times_confirmed=1),
        ]
        result = store.top_negative()
        assert len(result) == 1
        assert result[0].type == "negative"


# ── AppBenchmark ──────────────────────────────────────────────────────

class TestAppBenchmark:
    def test_defaults(self):
        b = AppBenchmark(app_id="app_bm")
        assert b.total_posts == 0
        assert b.avg_views == 0.0
        assert b.score_history == []

    def test_lege_histories(self):
        b = AppBenchmark(app_id="app_bm")
        assert b.views_history == []
        assert b.best_post_id is None
