"""
Quality Datamodellen — Content Quality & Experiment OS

Bevat Pydantic modellen voor:
- DimensionScore: score op één kwaliteitsdimensie
- AssetQualityScore: composite score over alle 4 dimensies
- BenchmarkResult: vergelijking met historische top-performers
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# DIMENSIE SCORE
# ──────────────────────────────────────────────

class DimensionScore(BaseModel):
    """
    Score op één kwaliteitsdimensie (0-100).

    Voorbeeld JSON:
    {
      "score": 78.0,
      "rationale": "Directe claim stopt scrollen, duidelijke belofte",
      "flags": []
    }
    """
    score:     float        # 0-100
    rationale: str          # Korte motivatie van de score (max ~100 tekens)
    flags:     list[str] = []  # Optionele waarschuwingen

    @property
    def level(self) -> str:
        """PASS / WARN / BLOCK op basis van drempelwaarden."""
        if self.score < 40:
            return "block"
        if self.score < 65:
            return "warn"
        return "pass"

    @property
    def emoji(self) -> str:
        return {"pass": "✓", "warn": "⚠", "block": "✗"}.get(self.level, "?")


# ──────────────────────────────────────────────
# ASSET QUALITY SCORE
# ──────────────────────────────────────────────

# Gewichten voor de composite score
DIMENSION_WEIGHTS: dict[str, float] = {
    "hook_strength":       0.35,  # Stopt scrollen — hoogste impact
    "clarity":             0.25,  # Begrijpt kijker de boodschap binnen 10s?
    "brand_fit":           0.20,  # Past bij merkstem en brand memory?
    "retention_potential": 0.20,  # Reden om tot het einde te kijken?
}

BLOCK_THRESHOLD     = 40.0   # Elke dimensie onder dit → BLOCK
WARN_THRESHOLD      = 65.0   # Elke dimensie onder dit → WARN
COMPOSITE_BLOCK     = 55.0   # Composite onder dit → BLOCK (ook bij passing dims)


class AssetQualityScore(BaseModel):
    """
    Composite kwaliteitsscore van één content-variant.
    Gegenereerd door quality/scorer.py vóór de approval.

    Beslissingslogica:
      - passed=False als composite < 55 of enige dimensie < 40
      - warnings als enige dimensie in [40, 65)
      - passed=True als alle checks slagen

    Voorbeeld JSON:
    {
      "variant_id": "var_chal_001",
      "hook_strength":       {"score": 86, "rationale": "...", "flags": []},
      "clarity":             {"score": 82, "rationale": "...", "flags": []},
      "brand_fit":           {"score": 79, "rationale": "...", "flags": []},
      "retention_potential": {"score": 79, "rationale": "...", "flags": []},
      "composite_score": 82.2,
      "passed": true,
      "warnings": [],
      "blockers": [],
      "evaluated_at": "2026-03-09T14:05:00"
    }
    """
    variant_id:          str
    hook_strength:       DimensionScore
    clarity:             DimensionScore
    brand_fit:           DimensionScore
    retention_potential: DimensionScore
    composite_score:     float
    passed:              bool
    warnings:            list[str] = []
    blockers:            list[str] = []
    evaluated_at:        datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        """Eénregelige samenvatting voor logging en UI."""
        status = "✓ PASS" if self.passed else "✗ BLOCK"
        return (
            f"{status} | Composite: {self.composite_score:.1f} | "
            f"Hook: {self.hook_strength.score:.0f} | "
            f"Clarity: {self.clarity.score:.0f} | "
            f"Brand: {self.brand_fit.score:.0f} | "
            f"Retention: {self.retention_potential.score:.0f}"
        )

    def all_dimension_scores(self) -> dict[str, float]:
        return {
            "hook_strength":       self.hook_strength.score,
            "clarity":             self.clarity.score,
            "brand_fit":           self.brand_fit.score,
            "retention_potential": self.retention_potential.score,
        }


# ──────────────────────────────────────────────
# BENCHMARK RESULT
# ──────────────────────────────────────────────

class BenchmarkResult(BaseModel):
    """
    Vergelijking van een nieuwe variant met historische top-performers.
    Gegenereerd door quality/benchmarker.py.

    NIET bedoeld voor causaliteitsclaims — alleen patroonherkenning.
    Confidence is altijd laag als er minder dan MIN_TOP_PERFORMERS beschikbaar zijn.

    Voorbeeld JSON:
    {
      "variant_id": "var_chal_001",
      "similarity_to_top_performers": 0.74,
      "matching_patterns": [
        "Directe opening met getal/statistiek",
        "CTA aan het einde met save-instructie"
      ],
      "differentiating_factors": [
        "Geen social proof element in de hook"
      ],
      "predicted_performance_band": "top_25%",
      "prediction_confidence": 0.52,
      "confidence_caveats": []
    }
    """
    variant_id:                   str
    similarity_to_top_performers: float        # 0.0-1.0
    matching_patterns:            list[str] = []
    differentiating_factors:      list[str] = []
    predicted_performance_band:   str = "unknown"  # top_10% | top_25% | average | below_average | unknown
    prediction_confidence:        float = 0.0      # 0.0-1.0
    confidence_caveats:           list[str] = []

    @property
    def is_reliable(self) -> bool:
        """True als de benchmark voldoende data heeft voor een bruikbare voorspelling."""
        return self.prediction_confidence >= 0.4

    def summary(self) -> str:
        reliability = "betrouwbaar" if self.is_reliable else "onvoldoende data"
        return (
            f"Benchmark: {self.predicted_performance_band} | "
            f"Gelijkenis: {self.similarity_to_top_performers:.0%} | "
            f"Conf: {self.prediction_confidence:.0%} ({reliability})"
        )
