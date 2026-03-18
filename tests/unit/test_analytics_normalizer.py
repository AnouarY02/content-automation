"""
DAG 6 — Unit tests: analytics/normalizer.py

Dekt:
  - normalize() retourneert NormalizedMetrics
  - engagement_rate = (likes+comments+shares+saves) / views
  - like_rate = likes / views
  - share_rate en save_rate correct
  - avg_watch_time_pct = avg_watch_time / video_duration (capped op 1.0)
  - completion_rate hoog (>= 0.75 * duration → * 1.12)
  - completion_rate midden (>= 0.50 → * 0.90)
  - completion_rate laag (< 0.50 → * 0.70)
  - views=0 → geen deling door nul (views default 1)
  - reach_rate = reach / impressions
  - amplification_rate = shares / reach als reach > 0
  - amplification_rate fallback op share_rate als reach=0
  - alle waarden afgerond (niet oneindig precies)
"""

import pytest

from analytics.models import RawTikTokMetrics
from analytics.normalizer import normalize


def _make_raw(
    views: int = 1000,
    likes: int = 50,
    comments: int = 10,
    shares: int = 20,
    saves: int = 30,
    profile_visits: int = 15,
    avg_watch_time_sec: float = 22.5,
    video_duration_sec: int = 45,
    reach: int = 900,
    impressions: int = 1100,
) -> RawTikTokMetrics:
    return RawTikTokMetrics(
        post_id="p_test",
        campaign_id="c_test",
        app_id="app_test",
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        profile_visits=profile_visits,
        avg_watch_time_sec=avg_watch_time_sec,
        video_duration_sec=video_duration_sec,
        reach=reach,
        impressions=impressions,
    )


class TestNormalizeRatios:
    def test_retourneert_normalized_metrics(self):
        from analytics.models import NormalizedMetrics
        result = normalize(_make_raw())
        assert isinstance(result, NormalizedMetrics)

    def test_engagement_rate_formule(self):
        raw = _make_raw(views=1000, likes=50, comments=10, shares=20, saves=30)
        result = normalize(raw)
        expected = (50 + 10 + 20 + 30) / 1000
        assert abs(result.engagement_rate - expected) < 0.0001

    def test_like_rate_formule(self):
        raw = _make_raw(views=1000, likes=50)
        result = normalize(raw)
        assert abs(result.like_rate - 0.05) < 0.0001

    def test_comment_rate_formule(self):
        raw = _make_raw(views=1000, comments=10)
        result = normalize(raw)
        assert abs(result.comment_rate - 0.01) < 0.0001

    def test_share_rate_formule(self):
        raw = _make_raw(views=1000, shares=20)
        result = normalize(raw)
        assert abs(result.share_rate - 0.02) < 0.0001

    def test_save_rate_formule(self):
        raw = _make_raw(views=1000, saves=30)
        result = normalize(raw)
        assert abs(result.save_rate - 0.03) < 0.0001

    def test_profile_visit_rate_formule(self):
        raw = _make_raw(views=1000, profile_visits=15)
        result = normalize(raw)
        assert abs(result.profile_visit_rate - 0.015) < 0.0001

    def test_reach_rate_formule(self):
        raw = _make_raw(reach=900, impressions=1100)
        result = normalize(raw)
        assert abs(result.reach_rate - 900/1100) < 0.0001


class TestNormalizeWatchTime:
    def test_avg_watch_time_pct_berekening(self):
        raw = _make_raw(avg_watch_time_sec=22.5, video_duration_sec=45)
        result = normalize(raw)
        assert abs(result.avg_watch_time_pct - 0.5) < 0.001

    def test_avg_watch_time_pct_max_1(self):
        """avg_watch_time > duration → pct gecapped op 1.0."""
        raw = _make_raw(avg_watch_time_sec=60.0, video_duration_sec=45)
        result = normalize(raw)
        assert result.avg_watch_time_pct <= 1.0

    def test_completion_rate_hoog_als_watch_time_hoog(self):
        """avg_watch_pct >= 0.75 → completion = min(pct * 1.12, 1.0)."""
        raw = _make_raw(avg_watch_time_sec=36.0, video_duration_sec=45)  # 80%
        result = normalize(raw)
        expected_pct = 36.0 / 45  # 0.80
        expected_completion = min(expected_pct * 1.12, 1.0)
        assert abs(result.completion_rate - expected_completion) < 0.001

    def test_completion_rate_midden_als_watch_time_midden(self):
        """avg_watch_pct in [0.50, 0.75) → completion = pct * 0.90."""
        raw = _make_raw(avg_watch_time_sec=27.0, video_duration_sec=45)  # 60%
        result = normalize(raw)
        expected_pct = 27.0 / 45  # 0.60
        expected_completion = expected_pct * 0.90
        assert abs(result.completion_rate - expected_completion) < 0.001

    def test_completion_rate_laag_als_watch_time_laag(self):
        """avg_watch_pct < 0.50 → completion = pct * 0.70."""
        raw = _make_raw(avg_watch_time_sec=18.0, video_duration_sec=45)  # 40%
        result = normalize(raw)
        expected_pct = 18.0 / 45  # 0.40
        expected_completion = expected_pct * 0.70
        assert abs(result.completion_rate - expected_completion) < 0.001


class TestNormalizeEdgeCases:
    def test_views_nul_geeft_geen_deling_door_nul(self):
        """Views=0 mag niet crashen — normalizer gebruikt max(views, 1)."""
        raw = _make_raw(views=0)
        result = normalize(raw)
        assert isinstance(result.engagement_rate, float)

    def test_geen_shares_amplification_rate_is_share_rate(self):
        """reach=0 → amplification_rate valt terug op share_rate."""
        raw = _make_raw(views=1000, shares=20, reach=0)
        result = normalize(raw)
        expected_share_rate = 20 / 1000
        assert abs(result.amplification_rate - expected_share_rate) < 0.0001

    def test_amplification_rate_als_reach_beschikbaar(self):
        """Normaal: amplification_rate = shares / reach."""
        raw = _make_raw(shares=20, reach=500)
        result = normalize(raw)
        expected = 20 / 500
        assert abs(result.amplification_rate - expected) < 0.0001

    def test_alle_nul_views_engagement_nul(self):
        raw = _make_raw(views=0, likes=0, comments=0, shares=0, saves=0)
        result = normalize(raw)
        assert result.engagement_rate == 0.0

    def test_resultaten_zijn_floats(self):
        result = normalize(_make_raw())
        assert isinstance(result.engagement_rate, float)
        assert isinstance(result.completion_rate, float)
