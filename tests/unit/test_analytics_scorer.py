"""
DAG 6 — Unit tests: analytics/scorer.py

Dekt:
  - compute_score() retourneert PerformanceScore
  - Confidence multiplier thresholds: <100, 100-500, 500-2000, 2000-10k, >10k views
  - Lage views → score gedempd (composite < raw_composite)
  - Hoge views → score ongedempd (multiplier = 1.0)
  - _scale_to_100() - beneden baseline → 0-50
  - _scale_to_100() - op baseline → 50
  - _scale_to_100() - op good → 75
  - _scale_to_100() - op great → 90
  - _scale_to_100() - boven great → 90-100 (logaritmisch)
  - _scale_to_100() - waarde 0 → 0
  - _percentile_rank() - lege history → 50.0
  - _percentile_rank() - score hoger dan alles → 100.0
  - _percentile_rank() - score lager dan alles → 0.0
  - update_benchmark() - voegt score toe aan history
  - update_benchmark() - history gecapped op 100
  - update_benchmark() - avg_composite_score bijgewerkt
  - update_benchmark() - best_score bijgewerkt
  - Benchmark aanwezig → percentile_rank ingevuld
  - Benchmark afwezig → percentile_rank is None
"""

import pytest

from analytics.models import AppBenchmark, LearningConfidence, NormalizedMetrics, RawTikTokMetrics
from analytics.scorer import (
    _confidence_multiplier,
    _percentile_rank,
    _scale_to_100,
    compute_score,
    update_benchmark,
)
from analytics.normalizer import normalize


# ── Helpers ───────────────────────────────────────────────────────────

def _make_raw(views: int = 5000, **kwargs) -> RawTikTokMetrics:
    defaults = dict(
        post_id="p", campaign_id="c", app_id="a",
        views=views, likes=150, comments=25, shares=50, saves=80,
        avg_watch_time_sec=25.0, video_duration_sec=45,
        reach=4500, impressions=6000, profile_visits=60,
    )
    defaults.update(kwargs)
    return RawTikTokMetrics(**defaults)


def _make_normalized(**kwargs) -> NormalizedMetrics:
    defaults = dict(
        engagement_rate=0.06, like_rate=0.035, comment_rate=0.006,
        share_rate=0.012, save_rate=0.018, profile_visit_rate=0.012,
        avg_watch_time_pct=0.55, completion_rate=0.50,
        reach_rate=0.75, amplification_rate=0.011,
    )
    defaults.update(kwargs)
    return NormalizedMetrics(**defaults)


# ── compute_score ──────────────────────────────────────────────────────

class TestComputeScore:
    def test_retourneert_performance_score(self):
        from analytics.models import PerformanceScore
        raw = _make_raw()
        normalized = normalize(raw)
        result = compute_score(raw, normalized)
        assert isinstance(result, PerformanceScore)

    def test_composite_score_tussen_0_en_100(self):
        raw = _make_raw()
        normalized = normalize(raw)
        result = compute_score(raw, normalized)
        assert 0.0 <= result.composite_score <= 100.0

    def test_alle_subscores_aanwezig(self):
        raw = _make_raw()
        normalized = normalize(raw)
        result = compute_score(raw, normalized)
        assert result.retention_score >= 0
        assert result.engagement_score >= 0
        assert result.virality_score >= 0
        assert result.reach_score >= 0
        assert result.profile_score >= 0

    def test_hours_measured_van_raw_overgenomen(self):
        raw = _make_raw()
        raw.hours_since_publish = 48.0
        normalized = normalize(raw)
        result = compute_score(raw, normalized)
        assert result.hours_measured == 48.0

    def test_zonder_benchmark_geen_percentile(self):
        raw = _make_raw()
        normalized = normalize(raw)
        result = compute_score(raw, normalized, benchmark=None)
        assert result.percentile_rank is None

    def test_met_benchmark_heeft_percentile(self):
        raw = _make_raw()
        normalized = normalize(raw)
        benchmark = AppBenchmark(app_id="a", score_history=[20.0, 40.0, 60.0, 80.0])
        result = compute_score(raw, normalized, benchmark=benchmark)
        assert result.percentile_rank is not None

    def test_lege_benchmark_history_geen_percentile(self):
        raw = _make_raw()
        normalized = normalize(raw)
        benchmark = AppBenchmark(app_id="a", score_history=[])
        result = compute_score(raw, normalized, benchmark=benchmark)
        assert result.percentile_rank is None


# ── _confidence_multiplier ────────────────────────────────────────────

class TestConfidenceMultiplier:
    def test_views_onder_100_multiplier_040(self):
        mult, conf = _confidence_multiplier(50)
        assert mult == 0.40
        assert conf == LearningConfidence.LOW

    def test_views_100_multiplier_070(self):
        mult, conf = _confidence_multiplier(100)
        assert mult == 0.70
        assert conf == LearningConfidence.LOW

    def test_views_499_multiplier_070(self):
        mult, conf = _confidence_multiplier(499)
        assert mult == 0.70

    def test_views_500_multiplier_085(self):
        mult, conf = _confidence_multiplier(500)
        assert mult == 0.85
        assert conf == LearningConfidence.MEDIUM

    def test_views_1999_multiplier_085(self):
        mult, conf = _confidence_multiplier(1999)
        assert mult == 0.85

    def test_views_2000_multiplier_095(self):
        mult, conf = _confidence_multiplier(2000)
        assert mult == 0.95
        assert conf == LearningConfidence.MEDIUM

    def test_views_9999_multiplier_095(self):
        mult, conf = _confidence_multiplier(9999)
        assert mult == 0.95

    def test_views_10000_multiplier_100(self):
        mult, conf = _confidence_multiplier(10000)
        assert mult == 1.00
        assert conf == LearningConfidence.HIGH

    def test_views_100000_multiplier_100(self):
        mult, conf = _confidence_multiplier(100000)
        assert mult == 1.00
        assert conf == LearningConfidence.HIGH

    def test_lage_views_dempt_score(self):
        """Score met 50 views moet lager zijn dan zelfde score met 50000 views."""
        raw_low  = _make_raw(views=50)
        raw_high = _make_raw(views=50000)
        norm_low  = normalize(raw_low)
        norm_high = normalize(raw_high)
        score_low  = compute_score(raw_low,  norm_low)
        score_high = compute_score(raw_high, norm_high)
        assert score_low.composite_score < score_high.composite_score


# ── _scale_to_100 ─────────────────────────────────────────────────────

class TestScaleTo100:
    BASELINE = 0.10
    GOOD     = 0.30
    GREAT    = 0.60

    def _scale(self, value: float) -> float:
        return _scale_to_100(value, self.BASELINE, self.GOOD, self.GREAT)

    def test_waarde_nul_geeft_nul(self):
        assert self._scale(0.0) == 0.0

    def test_negatief_geeft_nul(self):
        assert self._scale(-0.5) == 0.0

    def test_beneden_baseline_geeft_max_50(self):
        result = self._scale(self.BASELINE * 0.5)
        assert 0.0 < result <= 50.0

    def test_op_baseline_geeft_50(self):
        assert abs(self._scale(self.BASELINE) - 50.0) < 0.01

    def test_op_good_geeft_75(self):
        assert abs(self._scale(self.GOOD) - 75.0) < 0.01

    def test_net_onder_great_geeft_bijna_90(self):
        """Waarde net onder great zit aan het einde van de lineaire zone (~90)."""
        result = self._scale(self.GREAT * 0.9999)
        assert 89.0 < result <= 90.0

    def test_boven_great_max_100(self):
        result = self._scale(self.GREAT * 10)
        assert 90.0 < result <= 100.0

    def test_monotoon_stijgend(self):
        """Hogere waarden → hogere score."""
        scores = [self._scale(v) for v in [0.0, 0.05, 0.10, 0.30, 0.60, 1.0]]
        assert scores == sorted(scores)


# ── _percentile_rank ──────────────────────────────────────────────────

class TestPercentileRank:
    def test_lege_history_geeft_50(self):
        assert _percentile_rank(75.0, []) == 50.0

    def test_hoger_dan_alles_geeft_hoog(self):
        result = _percentile_rank(100.0, [20.0, 40.0, 60.0, 80.0])
        assert result == 100.0

    def test_lager_dan_alles_geeft_0(self):
        result = _percentile_rank(0.0, [20.0, 40.0, 60.0, 80.0])
        assert result == 0.0

    def test_midden_in_history(self):
        result = _percentile_rank(50.0, [20.0, 40.0, 60.0, 80.0])
        # 2 scores < 50 (20, 40) → 2/4 * 100 = 50.0
        assert result == 50.0


# ── update_benchmark ──────────────────────────────────────────────────

class TestUpdateBenchmark:
    def test_voegt_score_toe_aan_history(self):
        bm = AppBenchmark(app_id="a")
        bm = update_benchmark(bm, new_score=72.0, new_views=5000)
        assert 72.0 in bm.score_history

    def test_total_posts_verhoogd(self):
        bm = AppBenchmark(app_id="a")
        bm = update_benchmark(bm, new_score=72.0, new_views=5000)
        assert bm.total_posts == 1

    def test_avg_composite_score_bijgewerkt(self):
        bm = AppBenchmark(app_id="a")
        bm = update_benchmark(bm, new_score=60.0, new_views=1000)
        bm = update_benchmark(bm, new_score=80.0, new_views=2000)
        assert abs(bm.avg_composite_score - 70.0) < 0.01

    def test_best_score_bijgewerkt(self):
        bm = AppBenchmark(app_id="a")
        bm = update_benchmark(bm, new_score=60.0, new_views=1000)
        bm = update_benchmark(bm, new_score=90.0, new_views=5000)
        assert bm.best_score == 90.0

    def test_history_gecapped_op_100(self):
        bm = AppBenchmark(app_id="a")
        for i in range(110):
            bm = update_benchmark(bm, new_score=float(i), new_views=1000)
        assert len(bm.score_history) <= 100
        assert len(bm.views_history) <= 100
