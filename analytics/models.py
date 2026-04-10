"""
Analytics Datamodellen — AY Marketing OS

Bevat alle Pydantic modellen voor:
- Ruwe TikTok metrics
- Genormaliseerde metrics
- Campaign performance scores
- Experiment tags
- Leerpunten (learnings)
- Learning memory

Voorbeeld JSON-structuren staan als docstrings bij elk model.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ──────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────

class Platform(str, Enum):
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"
    LINKEDIN = "linkedin"


class ContentFormat(str, Enum):
    PROBLEM_SOLUTION = "problem-solution"
    BEFORE_AFTER = "before-after"
    TUTORIAL = "tutorial"
    SOCIAL_PROOF = "social-proof"
    TREND = "trend"
    TALKING_HEAD = "talking-head"
    TEXT_ON_SCREEN = "text-on-screen"


class VideoType(str, Enum):
    SCREEN_DEMO = "screen_demo"
    TALKING_HEAD = "talking_head"
    TEXT_ON_SCREEN = "text_on_screen"
    MIXED = "mixed"


class LearningConfidence(str, Enum):
    LOW = "low"        # < 5 datapunten
    MEDIUM = "medium"  # 5-20 datapunten
    HIGH = "high"      # > 20 datapunten


# ──────────────────────────────────────────────
# RUWE METRICS (direct van TikTok API)
# ──────────────────────────────────────────────

class RawTikTokMetrics(BaseModel):
    """
    Ruwe metrics zoals ze van de TikTok API komen.

    Voorbeeld:
    {
      "post_id": "7380123456789",
      "video_id": "7380123456789",
      "fetched_at": "2026-03-10T09:00:00",
      "hours_since_publish": 24,
      "views": 8420,
      "likes": 312,
      "comments": 28,
      "shares": 45,
      "saves": 67,
      "profile_visits": 134,
      "watch_time_total_sec": 189450,
      "avg_watch_time_sec": 22.5,
      "video_duration_sec": 45,
      "reach": 7890,
      "impressions": 9100
    }
    """
    post_id: str
    campaign_id: str
    app_id: str
    platform: Platform = Platform.TIKTOK

    # Basis metrics
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    profile_visits: int = 0

    # Watch time
    watch_time_total_sec: float = 0.0
    avg_watch_time_sec: float = 0.0
    video_duration_sec: int = 45

    # Bereik
    reach: int = 0
    impressions: int = 0

    # Meta
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    hours_since_publish: float = 24.0
    published_at: datetime | None = None

    # Experiment tags (optioneel — voor A/B tracking)
    experiment_id: str | None = None
    experiment_variant: str | None = None


# ──────────────────────────────────────────────
# GENORMALISEERDE METRICS
# ──────────────────────────────────────────────

class NormalizedMetrics(BaseModel):
    """
    Berekende ratios — alle waarden tussen 0.0 en 1.0 of als percentage.

    Voorbeeld:
    {
      "completion_rate": 0.51,
      "engagement_rate": 0.053,
      "share_rate": 0.0053,
      "save_rate": 0.0080,
      "like_rate": 0.037,
      "comment_rate": 0.0033,
      "hook_retention_pct": 0.72,
      "avg_watch_time_pct": 0.51
    }
    """
    # Ratio's (t.o.v. views)
    engagement_rate: float = 0.0        # (likes+comments+shares+saves) / views
    like_rate: float = 0.0              # likes / views
    comment_rate: float = 0.0           # comments / views
    share_rate: float = 0.0             # shares / views
    save_rate: float = 0.0              # saves / views
    profile_visit_rate: float = 0.0     # profile_visits / views

    # Watch time
    avg_watch_time_pct: float = 0.0     # avg_watch_time / video_duration
    completion_rate: float = 0.0        # geschat via avg_watch_time

    # Distributie
    reach_rate: float = 0.0             # reach / impressions (hogere = beter voor alg.)

    # Virality proxy
    amplification_rate: float = 0.0    # shares / reach


# ──────────────────────────────────────────────
# EXPERIMENT TAGS
# ──────────────────────────────────────────────

class ExperimentTags(BaseModel):
    """
    Tags die worden meegestuurd bij content-generatie voor A/B tracking.

    Voorbeeld:
    {
      "hook_type": "question",
      "hook_text_length": "short",
      "video_type": "screen_demo",
      "content_format": "problem-solution",
      "cta_type": "link_in_bio",
      "caption_style": "energetic",
      "posting_hour": 18,
      "posting_day": "tuesday",
      "voice_style": "energetic",
      "has_text_overlay": true,
      "music_type": "trending",
      "experiment_id": "exp_hook_ab_001",
      "variant": "A"
    }
    """
    hook_type: str = "statement"        # question | statement | shock | story | tip
    hook_text_length: str = "medium"    # short (<5w) | medium (5-10w) | long (>10w)
    video_type: VideoType = VideoType.SCREEN_DEMO
    content_format: ContentFormat = ContentFormat.PROBLEM_SOLUTION
    cta_type: str = "link_in_bio"       # link_in_bio | comment | follow | share | save
    caption_style: str = "informative"  # energetic | informative | story | minimal
    posting_hour: int = 18              # 0-23
    posting_day: str = "monday"         # monday t/m sunday
    voice_style: str = "neutral"        # energetic | calm | authoritative | conversational
    has_text_overlay: bool = True
    has_background_music: bool = True
    video_duration_bucket: str = "medium"  # short (<20s) | medium (20-45s) | long (>45s)
    experiment_id: str | None = None
    variant: str | None = None          # A | B | C


# ──────────────────────────────────────────────
# CAMPAIGN PERFORMANCE SCORE
# ──────────────────────────────────────────────

class PerformanceScore(BaseModel):
    """
    Composite score per campagne/post.

    Formule:
      score = (
        0.35 * retention_score +    # Completion rate / avg watch time
        0.25 * engagement_score +   # Engagement rate
        0.20 * virality_score +     # Share + save rate
        0.15 * reach_score +        # Absolute views (genormaliseerd)
        0.05 * profile_score        # Profile visits rate
      ) * confidence_multiplier

    Voorbeeld:
    {
      "composite_score": 71.4,
      "retention_score": 68.0,
      "engagement_score": 75.2,
      "virality_score": 58.0,
      "reach_score": 80.0,
      "profile_score": 45.0,
      "confidence_multiplier": 0.95,
      "confidence_level": "medium",
      "data_points": 1,
      "hours_measured": 24,
      "percentile_rank": null
    }
    """
    composite_score: float = 0.0        # 0-100
    retention_score: float = 0.0        # 0-100
    engagement_score: float = 0.0       # 0-100
    virality_score: float = 0.0         # 0-100
    reach_score: float = 0.0            # 0-100
    profile_score: float = 0.0          # 0-100
    confidence_multiplier: float = 1.0  # 0-1 — dempt score bij weinig data
    confidence_level: LearningConfidence = LearningConfidence.LOW
    data_points: int = 1                # Hoeveel meetpunten (1=eenmalig, >1=over tijd)
    hours_measured: float = 24.0
    percentile_rank: float | None = None  # Positie t.o.v. andere posts van dezelfde app


# ──────────────────────────────────────────────
# VOLLEDIGE POST ANALYSE
# ──────────────────────────────────────────────

class PostAnalysis(BaseModel):
    """
    Compleet analyseprofiel van één gepubliceerde post.

    Combineert: raw metrics + normalized + score + experiment tags + LLM inzichten
    """
    id: str = Field(default_factory=lambda: str(uuid4())[:12])
    campaign_id: str
    app_id: str
    post_id: str                        # TikTok post ID
    platform: Platform = Platform.TIKTOK

    raw: RawTikTokMetrics
    normalized: NormalizedMetrics
    score: PerformanceScore
    tags: ExperimentTags

    # LLM-gegenereerde inzichten (van AnalystAgent)
    llm_insights: dict[str, Any] = {}

    # Metadata
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    analysis_version: str = "1.0"


# ──────────────────────────────────────────────
# LEERPUNTEN (LEARNINGS)
# ──────────────────────────────────────────────

class LearningEntry(BaseModel):
    """
    Één concreet geleerde les uit performance-analyse.

    Voorbeeld:
    {
      "id": "learn_001",
      "app_id": "app_001",
      "category": "hook",
      "type": "positive",
      "finding": "Question-hooks presteren 34% beter dan statement-hooks bij deze doelgroep",
      "evidence": {
        "question_hooks_avg_score": 74.2,
        "statement_hooks_avg_score": 55.4,
        "sample_size": 8
      },
      "action": "Geef voorkeur aan question-hooks in idea_generator",
      "confidence": "medium",
      "derived_from_posts": ["post_abc", "post_def"],
      "created_at": "2026-03-10",
      "times_confirmed": 1,
      "expires_after_days": 60
    }
    """
    id: str = Field(default_factory=lambda: f"learn_{str(uuid4())[:8]}")
    app_id: str
    platform: Platform = Platform.TIKTOK

    # Classificatie
    category: str  # hook | video_type | content_format | cta | caption | timing | duration
    type: str       # positive | negative | neutral

    # Inhoud
    finding: str            # Beschrijving van wat geleerd is
    evidence: dict          # Kwantitatief bewijs
    action: str             # Concrete actie die agents moeten ondernemen
    prompt_instruction: str = ""  # Directe tekst voor in prompts

    # Kwaliteit
    confidence: LearningConfidence = LearningConfidence.LOW
    derived_from_posts: list[str] = []
    sample_size: int = 1
    times_confirmed: int = 1    # Hoe vaak bevestigd door nieuwe data

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_confirmed_at: datetime = Field(default_factory=datetime.utcnow)
    expires_after_days: int = 90  # Learnings vervallen — markt verandert


class LearningStore(BaseModel):
    """
    Alle learnings voor één app, gecumuleerd over tijd.

    Opslaglocatie: data/analytics/learnings/{app_id}/learnings_cumulative.json
    """
    app_id: str
    platform: Platform = Platform.TIKTOK
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    total_posts_analyzed: int = 0
    learnings: list[LearningEntry] = []

    def active_learnings(self, max_age_days: int = 90) -> list[LearningEntry]:
        """Geeft alleen niet-vervallen learnings terug."""
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        return [
            l for l in self.learnings
            if l.created_at > cutoff or l.times_confirmed > 3
        ]

    def by_category(self, category: str) -> list[LearningEntry]:
        return [l for l in self.active_learnings() if l.category == category]

    def top_positive(self, n: int = 5) -> list[LearningEntry]:
        positives = [l for l in self.active_learnings() if l.type == "positive"]
        return sorted(positives, key=lambda x: x.times_confirmed, reverse=True)[:n]

    def top_negative(self, n: int = 5) -> list[LearningEntry]:
        negatives = [l for l in self.active_learnings() if l.type == "negative"]
        return sorted(negatives, key=lambda x: x.times_confirmed, reverse=True)[:n]


# ──────────────────────────────────────────────
# APP BENCHMARK
# ──────────────────────────────────────────────

class AppBenchmark(BaseModel):
    """
    Doorlopende benchmark-statistieken per app.
    Gebruikt voor relatieve scoring (percentile_rank).

    Opslaglocatie: data/analytics/per_app/{app_id}_benchmark.json
    """
    app_id: str
    platform: Platform = Platform.TIKTOK
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    total_posts: int = 0

    # Gemiddelden
    avg_views: float = 0.0
    avg_engagement_rate: float = 0.0
    avg_completion_rate: float = 0.0
    avg_composite_score: float = 0.0

    # Distributies (voor percentile berekening)
    score_history: list[float] = []        # max 100 waarden bewaren
    views_history: list[int] = []          # max 100 waarden

    # Beste en slechtste
    best_score: float = 0.0
    worst_score: float = 100.0
    best_post_id: str | None = None
