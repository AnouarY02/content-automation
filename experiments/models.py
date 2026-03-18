"""
Experiment Datamodellen — AY Marketing OS Content Quality & Experiment OS

Bevat alle Pydantic modellen voor:
- Experiment dimensies en enums
- Hypothese opzet
- Variant specificaties en content
- Experiment lifecycle
- Variant performance vergelijking
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────

class ExperimentDimension(str, Enum):
    """De variabele die getest wordt. Slechts één per experiment."""
    HOOK_TYPE      = "hook_type"
    CTA_TYPE       = "cta_type"
    CAPTION_STYLE  = "caption_style"
    VIDEO_FORMAT   = "video_format"
    POSTING_WINDOW = "posting_window"


class HookType(str, Enum):
    """
    Type opening in de eerste 2-3 seconden van de video.

    bold_claim    → directe, meetbare bewering. Geen intro.
    curiosity_gap → noem iets zonder het te onthullen.
    question      → specifieke vraag met herkenning voor doelgroep.
    social_proof  → getal, percentage, resultaat als opener.
    contrast      → "Terwijl X, doet Y dit..." — spanningsveld.
    tutorial      → directe belofte van waarde in stappen.
    """
    BOLD_CLAIM    = "bold_claim"
    CURIOSITY_GAP = "curiosity_gap"
    QUESTION      = "question"
    SOCIAL_PROOF  = "social_proof"
    CONTRAST      = "contrast"
    TUTORIAL      = "tutorial"


class CtaType(str, Enum):
    """
    Type call-to-action aan het einde van de video of in de caption.

    soft      → follow / like — laagste drempel.
    hard      → link / koop / meld je aan — conversie-gericht.
    social    → deel / duet / stitch — virality-gericht.
    retention → comment / bewaar voor later — engagement-gericht.
    """
    SOFT      = "soft"
    HARD      = "hard"
    SOCIAL    = "social"
    RETENTION = "retention"


class CaptionStyle(str, Enum):
    """
    Schrijfstijl van de caption (tekst onder de video).

    minimal      → max 1 zin + hashtags.
    storytelling → korte narrative die de video aanvult.
    list         → 3 concrete bullets.
    question     → eindigt met een vraag om comments te stimuleren.
    hook_repeat  → caption herhaalt of versterkt de video-hook.
    """
    MINIMAL      = "minimal"
    STORYTELLING = "storytelling"
    LIST         = "list"
    QUESTION     = "question"
    HOOK_REPEAT  = "hook_repeat"


class ExperimentStatus(str, Enum):
    """Lifecycle status van een experiment."""
    GENERATING   = "generating"    # Varianten worden aangemaakt
    QUALITY_FAIL = "quality_fail"  # ≥1 variant geblokkeerd door quality scorer
    PENDING      = "pending"       # Klaar voor operator review
    SELECTED     = "selected"      # Operator heeft variant gekozen
    PUBLISHED    = "published"     # Gekozen variant is live op TikTok
    MEASURING    = "measuring"     # Wachten op voldoende views
    CONCLUDED    = "concluded"     # Winnaar statistisch bepaald
    INCONCLUSIVE = "inconclusive"  # Onvoldoende data na MAX_WAIT_DAYS


# ──────────────────────────────────────────────
# HYPOTHESE
# ──────────────────────────────────────────────

class Hypothesis(BaseModel):
    """
    Expliciete formulering van wat getest wordt en waarom.

    Voorbeeld JSON:
    {
      "dimension": "hook_type",
      "control_value": "curiosity_gap",
      "challenger_value": "bold_claim",
      "rationale": "Top-3 performers gebruikten bold_claim hooks (gem. score 84).",
      "supporting_evidence": [
        "camp_top1: bold_claim, score=91, views=45200"
      ]
    }
    """
    dimension:            ExperimentDimension
    control_value:        str
    challenger_value:     str
    rationale:            str
    supporting_evidence:  list[str] = []

    def as_prompt_context(self) -> str:
        """Geformatteerde versie voor injectie in agent prompts."""
        return (
            f"EXPERIMENT: {self.dimension.value} | "
            f"CONTROLE={self.control_value} vs CHALLENGER={self.challenger_value}\n"
            f"Hypothese: {self.rationale}\n"
            f"Varieer UITSLUITEND op {self.dimension.value}. "
            f"Alle andere elementen blijven identiek aan het control script."
        )


# ──────────────────────────────────────────────
# VARIANT SPEC
# ──────────────────────────────────────────────

class VariantSpec(BaseModel):
    """
    Metadata die beschrijft wat een variant onderscheidt van de control.

    Voorbeeld JSON:
    {
      "variant_id": "var_a3f9b2c1",
      "label": "challenger_A",
      "dimension": "hook_type",
      "dimension_value": "bold_claim",
      "changes_from_control": [
        "Hook type: curiosity_gap → bold_claim",
        "Eerste 3 seconden volledig herschreven",
        "Openingszin is directe bewering i.p.v. vraag"
      ],
      "generation_instruction": "Gebruik bold_claim hook: begin met een..."
    }
    """
    variant_id:             str = Field(default_factory=lambda: f"var_{uuid4().hex[:8]}")
    label:                  str                   # "control" | "challenger_A" | "challenger_B"
    dimension:              ExperimentDimension
    dimension_value:        str                   # De specifieke waarde die getest wordt
    changes_from_control:   list[str] = []        # Leesbare diff voor operator
    generation_instruction: str = ""              # Exacte instructie aan agent


# ──────────────────────────────────────────────
# VARIANT
# ──────────────────────────────────────────────

class Variant(BaseModel):
    """
    Één volledig uitgewerkte content-variant binnen een experiment.

    Bevat zowel de spec (wat verschilt) als de volledige content
    (idea, script, caption) en lifecycle-velden (quality score, performance).

    Voorbeeld JSON:
    {
      "variant_id": "var_ctrl_001",
      "experiment_id": "exp_a3f9b2c1d4",
      "label": "control",
      "spec": { ... },
      "idea": { "title": "...", "hook": "..." },
      "script": { "scenes": [...] },
      "caption": { "caption": "...", "hashtags": [...] },
      "video_path": null,
      "created_at": "2026-03-09T14:00:00",
      "quality_score": null,
      "tiktok_post_id": null,
      "performance": null,
      "performance_score": null
    }
    """
    variant_id:        str
    experiment_id:     str
    label:             str
    spec:              VariantSpec

    # Content — kopie van relevante bundle-onderdelen
    idea:              dict
    script:            dict
    caption:           dict
    video_path:        Optional[str] = None

    # Lifecycle
    created_at:        datetime = Field(default_factory=datetime.utcnow)

    # Scores — ingevuld door quality/scorer.py na generatie
    quality_score:     Optional[dict] = None      # AssetQualityScore als dict

    # Post-publish — ingevuld na publicatie op TikTok
    tiktok_post_id:    Optional[str] = None
    performance:       Optional[dict] = None      # Raw performance metrics
    performance_score: Optional[float] = None     # Composite score (0-100)

    @property
    def is_control(self) -> bool:
        return self.label == "control"

    @property
    def passed_quality(self) -> bool:
        if self.quality_score is None:
            return True  # Nog niet gescoord — niet geblokkeerd
        return self.quality_score.get("passed", True)

    @property
    def view_count(self) -> int:
        if not self.performance:
            return 0
        return self.performance.get("play_count", self.performance.get("views", 0))


# ──────────────────────────────────────────────
# EXPERIMENT
# ──────────────────────────────────────────────

class Experiment(BaseModel):
    """
    Container voor een volledig A/B experiment.

    Één experiment test precies één dimensie met één control
    en maximaal twee challengers (MVP: één challenger).

    Opslaglocatie: data/experiments/{experiment_id}.json

    Voorbeeld JSON:
    {
      "experiment_id": "exp_a3f9b2c1d4",
      "campaign_id": "camp_abc123",
      "app_id": "myapp_001",
      "hypothesis": { ... },
      "variants": [ {...}, {...} ],
      "status": "measuring",
      "selected_variant_id": "var_chal_001",
      "selected_by": "operator",
      "winning_variant_id": null,
      "causal_confidence": null,
      "conclusion": null,
      "created_at": "2026-03-09T14:00:00",
      "concluded_at": null
    }
    """
    experiment_id:       str = Field(default_factory=lambda: f"exp_{uuid4().hex[:10]}")
    campaign_id:         str
    app_id:              str
    tenant_id:           str = "default"   # Tenant isolatie — "default" = backward compat
    hypothesis:          Hypothesis
    variants:            list[Variant] = []

    # Status
    status:              ExperimentStatus = ExperimentStatus.GENERATING

    # Operator beslissing
    selected_variant_id: Optional[str] = None
    selected_by:         Optional[str] = None
    selected_at:         Optional[datetime] = None

    # Conclusie — ingevuld door comparator.py
    winning_variant_id:  Optional[str] = None
    causal_confidence:   Optional[float] = None   # 0.0-1.0
    conclusion:          Optional[str] = None

    # Tijdstempels
    created_at:          datetime = Field(default_factory=datetime.utcnow)
    concluded_at:        Optional[datetime] = None

    # ── Hulpmethoden ──

    def get_variant(self, variant_id: str) -> Optional[Variant]:
        return next((v for v in self.variants if v.variant_id == variant_id), None)

    def get_control(self) -> Optional[Variant]:
        return next((v for v in self.variants if v.label == "control"), None)

    def get_challengers(self) -> list[Variant]:
        return [v for v in self.variants if v.label != "control"]

    def all_quality_scored(self) -> bool:
        """True als alle varianten een quality score hebben."""
        return all(v.quality_score is not None for v in self.variants)

    def any_quality_blocked(self) -> bool:
        """True als minstens één variant geblokkeerd is door de quality scorer."""
        return any(
            v.quality_score is not None and not v.quality_score.get("passed", True)
            for v in self.variants
        )

    def variants_with_performance(self) -> list[Variant]:
        """Geeft varianten met gemeten performance data terug."""
        return [v for v in self.variants if v.performance is not None]

    def dimension(self) -> ExperimentDimension:
        return self.hypothesis.dimension

    def is_active(self) -> bool:
        return self.status in (
            ExperimentStatus.PUBLISHED,
            ExperimentStatus.MEASURING,
        )

    def is_finished(self) -> bool:
        return self.status in (
            ExperimentStatus.CONCLUDED,
            ExperimentStatus.INCONCLUSIVE,
        )


# ──────────────────────────────────────────────
# VARIANT PERFORMANCE VERGELIJKING
# ──────────────────────────────────────────────

class VariantPerformanceComparison(BaseModel):
    """
    Post-publish vergelijking tussen varianten binnen één experiment.
    Gegenereerd door experiments/comparator.py.

    Voorbeeld JSON:
    {
      "experiment_id": "exp_a3f9b2c1d4",
      "dimension": "hook_type",
      "winner_variant_id": "var_chal_001",
      "winner_label": "challenger_A",
      "winner_dimension_value": "bold_claim",
      "loser_variant_id": "var_ctrl_001",
      "loser_label": "control",
      "loser_dimension_value": "curiosity_gap",
      "winner_score": 81.5,
      "loser_score": 68.2,
      "score_delta": 13.3,
      "winner_views": 8400,
      "loser_views": 7200,
      "causal_confidence": 0.71,
      "conclusion": "bold_claim presteert significant beter dan curiosity_gap",
      "concluded_at": "2026-03-16T09:00:00",
      "sufficient_data": true
    }
    """
    experiment_id:          str
    dimension:              ExperimentDimension

    winner_variant_id:      Optional[str] = None
    winner_label:           Optional[str] = None
    winner_dimension_value: Optional[str] = None
    loser_variant_id:       Optional[str] = None
    loser_label:            Optional[str] = None
    loser_dimension_value:  Optional[str] = None

    winner_score:           Optional[float] = None
    loser_score:            Optional[float] = None
    score_delta:            Optional[float] = None

    winner_views:           Optional[int] = None
    loser_views:            Optional[int] = None

    # Statistische betrouwbaarheid
    causal_confidence:      float = 0.0           # 0.0-1.0
    sufficient_data:        bool = False

    conclusion:             Optional[str] = None
    concluded_at:           datetime = Field(default_factory=datetime.utcnow)

    @property
    def has_winner(self) -> bool:
        return self.winner_variant_id is not None and self.sufficient_data
