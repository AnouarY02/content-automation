"""
Composite Performance Scorer

SCOREMODEL ONTWERP
──────────────────
Gewichten (totaal = 100%):
  35% Retention     → avg_watch_time_pct + completion_rate
  25% Engagement    → like + comment + share + save rates
  20% Virality      → share_rate + amplification_rate + save_rate
  15% Reach         → absolute views (genormaliseerd vs. benchmark)
   5% Intent        → profile_visit_rate (sterkste conversie-signaal)

BIAS-PREVENTIE
──────────────
Probleem: Een post met 10 views en 3 likes heeft een 30% like-rate
maar dat zegt niets statistisch.

Oplossing: Confidence Multiplier op basis van views:
  views < 100    → multiplier 0.40  (te weinig data)
  views 100-500  → multiplier 0.70
  views 500-2000 → multiplier 0.85
  views 2000-10k → multiplier 0.95
  views > 10k    → multiplier 1.00

Formule:
  raw_score = gewogen_som(subscores)
  final_score = raw_score * confidence_multiplier

BENCHMARKING
────────────
Reach score wordt genormaliseerd t.o.v. de app's eigen historiek.
Zo vergelijk je een kleine niche-app niet met een viral account.

REFERENTIEWAARDEN (TikTok gemiddelden 2025/2026):
  avg_engagement_rate: 0.035 (3.5%)
  avg_completion_rate: 0.40
  avg_watch_time_pct:  0.45
  good_share_rate:     0.005 (0.5%)
  good_save_rate:      0.008 (0.8%)
"""

import math
from analytics.models import (
    NormalizedMetrics,
    RawTikTokMetrics,
    PerformanceScore,
    AppBenchmark,
    LearningConfidence,
)

# ──────────────────────────────────────────────
# PLATFORM BENCHMARKS (TikTok 2025/2026)
# ──────────────────────────────────────────────

TIKTOK_BENCHMARKS = {
    # Engagement benchmarks (platform gemiddelden)
    "avg_engagement_rate": 0.035,
    "good_engagement_rate": 0.070,   # Top 25%
    "great_engagement_rate": 0.120,  # Top 10%

    # Watch time benchmarks
    "avg_completion_rate": 0.40,
    "good_completion_rate": 0.60,
    "great_completion_rate": 0.80,

    # Share/save benchmarks
    "avg_share_rate": 0.004,
    "good_share_rate": 0.010,
    "avg_save_rate": 0.007,
    "good_save_rate": 0.015,

    # Absolute views referentie (voor reach score)
    "baseline_views": 1000,          # Wat we als "normaal" beschouwen voor een nieuwe account
}

# Scoregewichten
WEIGHTS = {
    "retention": 0.35,
    "engagement": 0.25,
    "virality": 0.20,
    "reach": 0.15,
    "intent": 0.05,
}


def compute_score(
    raw: RawTikTokMetrics,
    normalized: NormalizedMetrics,
    benchmark: AppBenchmark | None = None,
) -> PerformanceScore:
    """
    Bereken composite performance score (0-100).

    Args:
        raw: Ruwe metrics
        normalized: Genormaliseerde ratios
        benchmark: Historische benchmark van de app (optioneel)

    Returns:
        PerformanceScore met alle subscores en confidence
    """
    # ── Sub-scores (elk 0-100) ──

    retention_score = _retention_score(normalized)
    engagement_score = _engagement_score(normalized)
    virality_score = _virality_score(normalized)
    reach_score = _reach_score(raw, benchmark)
    intent_score = _intent_score(normalized)

    # ── Gewogen composite ──
    raw_composite = (
        retention_score * WEIGHTS["retention"] +
        engagement_score * WEIGHTS["engagement"] +
        virality_score * WEIGHTS["virality"] +
        reach_score * WEIGHTS["reach"] +
        intent_score * WEIGHTS["intent"]
    )

    # ── Confidence multiplier ──
    multiplier, confidence_level = _confidence_multiplier(raw.views)

    composite = round(raw_composite * multiplier, 1)

    # ── Percentile rank (als benchmark beschikbaar) ──
    percentile = None
    if benchmark and benchmark.score_history:
        percentile = _percentile_rank(composite, benchmark.score_history)

    return PerformanceScore(
        composite_score=composite,
        retention_score=round(retention_score, 1),
        engagement_score=round(engagement_score, 1),
        virality_score=round(virality_score, 1),
        reach_score=round(reach_score, 1),
        profile_score=round(intent_score, 1),
        confidence_multiplier=round(multiplier, 3),
        confidence_level=confidence_level,
        data_points=1,
        hours_measured=raw.hours_since_publish,
        percentile_rank=percentile,
    )


def _retention_score(n: NormalizedMetrics) -> float:
    """
    Score op basis van hoe lang mensen kijken.
    Gewicht: avg_watch_time (60%) + completion_rate (40%)
    """
    b = TIKTOK_BENCHMARKS
    watch_score = _scale_to_100(
        value=n.avg_watch_time_pct,
        baseline=b["avg_completion_rate"],
        good=b["good_completion_rate"],
        great=b["great_completion_rate"],
    )
    completion_score = _scale_to_100(
        value=n.completion_rate,
        baseline=b["avg_completion_rate"],
        good=b["good_completion_rate"],
        great=b["great_completion_rate"],
    )
    return watch_score * 0.60 + completion_score * 0.40


def _engagement_score(n: NormalizedMetrics) -> float:
    """
    Score op basis van interactierate.
    Likes tellen minder zwaar dan comments/shares/saves.
    """
    b = TIKTOK_BENCHMARKS

    # Kwaliteitsgewichten: saves > shares > comments > likes
    weighted_er = (
        n.like_rate * 0.20 +
        n.comment_rate * 0.25 +
        n.share_rate * 0.25 +
        n.save_rate * 0.30
    )
    # Normaliseer t.o.v. platform benchmark
    avg_weighted = (
        b["avg_engagement_rate"] * 0.20 +  # proxies
        b["avg_engagement_rate"] * 0.006 +
        b["avg_share_rate"] * 0.25 +
        b["avg_save_rate"] * 0.30
    )
    return _scale_to_100(
        value=weighted_er,
        baseline=b["avg_engagement_rate"] * 0.25,
        good=b["good_engagement_rate"] * 0.25,
        great=b["great_engagement_rate"] * 0.25,
    )


def _virality_score(n: NormalizedMetrics) -> float:
    """
    Score voor virality-potentie.
    Shares + saves zijn de sterkste signalen — TikTok algoritme beloont dit direct.
    """
    b = TIKTOK_BENCHMARKS
    share_component = _scale_to_100(
        value=n.share_rate,
        baseline=b["avg_share_rate"],
        good=b["good_share_rate"],
        great=b["good_share_rate"] * 2,
    )
    save_component = _scale_to_100(
        value=n.save_rate,
        baseline=b["avg_save_rate"],
        good=b["good_save_rate"],
        great=b["good_save_rate"] * 2,
    )
    amp_component = _scale_to_100(
        value=n.amplification_rate,
        baseline=0.003,
        good=0.008,
        great=0.020,
    )
    return share_component * 0.35 + save_component * 0.35 + amp_component * 0.30


def _reach_score(raw: RawTikTokMetrics, benchmark: AppBenchmark | None) -> float:
    """
    Score voor absoluut bereik, genormaliseerd t.o.v. de app's eigen historiek.
    Voorkomt dat een niche-app altijd laag scoort t.o.v. grote accounts.
    """
    views = raw.views

    if benchmark and benchmark.avg_views > 100:
        # Gebruik app-specifieke baseline
        baseline = benchmark.avg_views * 0.5
        good = benchmark.avg_views * 1.5
        great = benchmark.avg_views * 3.0
    else:
        # Gebruik platform baseline voor nieuwe accounts
        b = TIKTOK_BENCHMARKS
        baseline = b["baseline_views"] * 0.3
        good = b["baseline_views"]
        great = b["baseline_views"] * 5.0

    return _scale_to_100(value=views, baseline=baseline, good=good, great=great)


def _intent_score(n: NormalizedMetrics) -> float:
    """
    Score voor conversie-intentie (profiel bezoeken).
    Sterk signaal: kijker wil meer weten over de app.
    """
    return _scale_to_100(
        value=n.profile_visit_rate,
        baseline=0.005,
        good=0.020,
        great=0.050,
    )


def _scale_to_100(value: float, baseline: float, good: float, great: float) -> float:
    """
    Schaal een waarde naar 0-100 met drie ankerpunten.

    Hieronder baseline → 0-50 (lineair)
    Baseline → good    → 50-75 (lineair)
    Good → great       → 75-90 (lineair)
    Boven great        → 90-100 (logaritmisch)
    """
    if value <= 0:
        return 0.0
    if value < baseline:
        return min(50.0, (value / baseline) * 50.0)
    elif value < good:
        return 50.0 + ((value - baseline) / (good - baseline)) * 25.0
    elif value < great:
        return 75.0 + ((value - good) / (great - good)) * 15.0
    else:
        # Logaritmisch boven great — voorkomt extreme scores
        overshoot = value / great
        return min(100.0, 90.0 + math.log(overshoot + 1) * 8.0)


def _confidence_multiplier(views: int) -> tuple[float, LearningConfidence]:
    """
    Dempfactor op basis van sample-grootte (views).

    Laag views → lage confidence → score gedempd.
    Dit voorkomt dat een post met 20 views en 10 likes (50% like-rate)
    een hogere score krijgt dan een post met 5000 views en 5% like-rate.
    """
    if views < 100:
        return 0.40, LearningConfidence.LOW
    elif views < 500:
        return 0.70, LearningConfidence.LOW
    elif views < 2000:
        return 0.85, LearningConfidence.MEDIUM
    elif views < 10000:
        return 0.95, LearningConfidence.MEDIUM
    else:
        return 1.00, LearningConfidence.HIGH


def _percentile_rank(score: float, history: list[float]) -> float:
    """
    Bereken percentiel-positie t.o.v. historische scores.
    100 = beste ooit, 0 = slechtste ooit.
    """
    if not history:
        return 50.0
    below = sum(1 for s in history if s < score)
    return round((below / len(history)) * 100, 1)


def update_benchmark(
    benchmark: AppBenchmark,
    new_score: float,
    new_views: int,
) -> AppBenchmark:
    """
    Update de rolling benchmark van een app met nieuwe data.
    Bewaart max 100 historische waarden (sliding window).
    """
    benchmark.total_posts += 1

    # Update score history (max 100)
    benchmark.score_history.append(new_score)
    if len(benchmark.score_history) > 100:
        benchmark.score_history = benchmark.score_history[-100:]

    benchmark.views_history.append(new_views)
    if len(benchmark.views_history) > 100:
        benchmark.views_history = benchmark.views_history[-100:]

    # Update gemiddelden
    benchmark.avg_composite_score = sum(benchmark.score_history) / len(benchmark.score_history)
    benchmark.avg_views = sum(benchmark.views_history) / len(benchmark.views_history)

    # Update best/worst
    if new_score > benchmark.best_score:
        benchmark.best_score = new_score
    if new_score < benchmark.worst_score:
        benchmark.worst_score = new_score

    from datetime import datetime
    benchmark.last_updated = datetime.utcnow()
    return benchmark
