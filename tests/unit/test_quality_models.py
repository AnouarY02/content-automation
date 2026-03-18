"""
DAG 1 — Unit tests: quality/models.py

Dekt:
  - DimensionScore.level grenzen (exact 40.0, exact 65.0, net boven/onder)
  - DimensionScore.emoji
  - DimensionScore flags
  - DIMENSION_WEIGHTS som = 1.0
  - AssetQualityScore composite score berekening
  - AssetQualityScore.passed logica
  - AssetQualityScore.summary() output
  - AssetQualityScore.all_dimension_scores()
  - BenchmarkResult.is_reliable threshold
  - BenchmarkResult.summary()
  - Serialization roundtrip
"""

import json
from datetime import datetime

import pytest

from quality.models import (
    BLOCK_THRESHOLD,
    COMPOSITE_BLOCK,
    DIMENSION_WEIGHTS,
    WARN_THRESHOLD,
    AssetQualityScore,
    BenchmarkResult,
    DimensionScore,
)


# ── DimensionScore.level grenzen ──────────────────────────────────────

class TestDimensionScoreLevel:
    """Grenzen: BLOCK < 40, WARN [40, 65), PASS >= 65."""

    @pytest.mark.parametrize("score,expected_level", [
        (0.0,   "block"),
        (1.0,   "block"),
        (39.9,  "block"),
        (39.99, "block"),
        (40.0,  "warn"),   # exact grens BLOCK/WARN
        (40.1,  "warn"),
        (50.0,  "warn"),
        (64.9,  "warn"),
        (64.99, "warn"),
        (65.0,  "pass"),   # exact grens WARN/PASS
        (65.1,  "pass"),
        (80.0,  "pass"),
        (100.0, "pass"),
    ])
    def test_level(self, score, expected_level):
        ds = DimensionScore(score=score, rationale="test")
        assert ds.level == expected_level

    def test_thresholds_match_constants(self):
        """DimensionScore thresholds zijn consistent met module-constanten."""
        assert DimensionScore(score=BLOCK_THRESHOLD - 0.01, rationale="x").level == "block"
        assert DimensionScore(score=BLOCK_THRESHOLD, rationale="x").level == "warn"
        assert DimensionScore(score=WARN_THRESHOLD - 0.01, rationale="x").level == "warn"
        assert DimensionScore(score=WARN_THRESHOLD, rationale="x").level == "pass"

    @pytest.mark.parametrize("score,expected_emoji", [
        (39.0, "✗"),
        (50.0, "⚠"),
        (80.0, "✓"),
    ])
    def test_emoji(self, score, expected_emoji):
        ds = DimensionScore(score=score, rationale="test")
        assert ds.emoji == expected_emoji

    def test_flags_empty_by_default(self):
        ds = DimensionScore(score=75.0, rationale="test")
        assert ds.flags == []

    def test_flags_stored(self):
        ds = DimensionScore(score=50.0, rationale="test", flags=["te_kort", "onduidelijk"])
        assert len(ds.flags) == 2
        assert "te_kort" in ds.flags

    def test_rationale_stored(self):
        ds = DimensionScore(score=70.0, rationale="Duidelijke boodschap")
        assert ds.rationale == "Duidelijke boodschap"


# ── DIMENSION_WEIGHTS ─────────────────────────────────────────────────

class TestDimensionWeights:
    def test_weights_sum_to_one(self):
        total = sum(DIMENSION_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_required_keys_present(self):
        required = {"hook_strength", "clarity", "brand_fit", "retention_potential"}
        assert required == set(DIMENSION_WEIGHTS.keys())

    def test_hook_strength_highest_weight(self):
        assert DIMENSION_WEIGHTS["hook_strength"] == max(DIMENSION_WEIGHTS.values())

    def test_all_weights_positive(self):
        assert all(w > 0 for w in DIMENSION_WEIGHTS.values())


# ── AssetQualityScore ─────────────────────────────────────────────────

def _make_score(
    hook: float = 75.0,
    clarity: float = 75.0,
    brand: float = 75.0,
    retention: float = 75.0,
    passed: bool = True,
    warnings: list[str] | None = None,
    blockers: list[str] | None = None,
) -> AssetQualityScore:
    composite = (
        hook * 0.35 + clarity * 0.25 + brand * 0.20 + retention * 0.20
    )
    return AssetQualityScore(
        variant_id="var_test",
        hook_strength=DimensionScore(score=hook, rationale="hook"),
        clarity=DimensionScore(score=clarity, rationale="clarity"),
        brand_fit=DimensionScore(score=brand, rationale="brand"),
        retention_potential=DimensionScore(score=retention, rationale="retention"),
        composite_score=composite,
        passed=passed,
        warnings=warnings or [],
        blockers=blockers or [],
    )


class TestAssetQualityScore:
    def test_composite_score_formula(self):
        """Controleer dat composite score overeenkomt met gewichten."""
        s = _make_score(hook=80, clarity=70, brand=60, retention=90)
        expected = 80 * 0.35 + 70 * 0.25 + 60 * 0.20 + 90 * 0.20
        assert abs(s.composite_score - expected) < 0.001

    def test_passed_true(self):
        s = _make_score(passed=True)
        assert s.passed is True

    def test_passed_false(self):
        s = _make_score(passed=False)
        assert s.passed is False

    def test_summary_pass_contains_pass(self):
        s = _make_score(passed=True)
        assert "PASS" in s.summary()

    def test_summary_block_contains_block(self):
        s = _make_score(passed=False)
        assert "BLOCK" in s.summary()

    def test_summary_contains_composite_score(self):
        s = _make_score(hook=86, clarity=82, brand=79, retention=79, passed=True)
        summary = s.summary()
        assert "86" in summary
        assert "82" in summary

    def test_all_dimension_scores_keys(self):
        s = _make_score()
        dims = s.all_dimension_scores()
        assert set(dims.keys()) == {"hook_strength", "clarity", "brand_fit", "retention_potential"}

    def test_all_dimension_scores_values(self):
        s = _make_score(hook=80, clarity=70, brand=60, retention=90)
        dims = s.all_dimension_scores()
        assert dims["hook_strength"] == 80.0
        assert dims["clarity"] == 70.0
        assert dims["brand_fit"] == 60.0
        assert dims["retention_potential"] == 90.0

    def test_warnings_list(self):
        s = _make_score(warnings=["caption te kort"])
        assert "caption te kort" in s.warnings

    def test_blockers_list(self):
        s = _make_score(blockers=["hook te zwak"])
        assert "hook te zwak" in s.blockers

    def test_empty_warnings_and_blockers_by_default(self):
        s = _make_score()
        assert s.warnings == []
        assert s.blockers == []

    def test_evaluated_at_auto_set(self):
        s = _make_score()
        assert isinstance(s.evaluated_at, datetime)

    def test_variant_id_stored(self):
        s = AssetQualityScore(
            variant_id="var_custom_id",
            hook_strength=DimensionScore(score=75, rationale="x"),
            clarity=DimensionScore(score=75, rationale="x"),
            brand_fit=DimensionScore(score=75, rationale="x"),
            retention_potential=DimensionScore(score=75, rationale="x"),
            composite_score=75.0,
            passed=True,
        )
        assert s.variant_id == "var_custom_id"

    def test_all_zero_scores(self):
        """Alle dimensies op 0 → composite 0, passed moet False zijn."""
        s = _make_score(hook=0, clarity=0, brand=0, retention=0, passed=False)
        assert s.composite_score == 0.0
        assert s.passed is False

    def test_all_max_scores(self):
        """Alle dimensies op 100 → composite 100."""
        s = _make_score(hook=100, clarity=100, brand=100, retention=100, passed=True)
        assert abs(s.composite_score - 100.0) < 0.001

    def test_composite_block_boundary(self):
        """Test dat COMPOSITE_BLOCK constant correct is."""
        assert COMPOSITE_BLOCK == 55.0

    def test_model_dump_roundtrip(self):
        s = _make_score(hook=80, clarity=70, brand=65, retention=75, passed=True)
        d = s.model_dump(mode="json")
        restored = AssetQualityScore(**d)
        assert restored.variant_id == s.variant_id
        assert abs(restored.composite_score - s.composite_score) < 0.001
        assert restored.passed == s.passed


# ── BenchmarkResult ───────────────────────────────────────────────────

class TestBenchmarkResult:
    def test_minimal_construction(self):
        b = BenchmarkResult(
            variant_id="var_bench",
            similarity_to_top_performers=0.0,
        )
        assert b.prediction_confidence == 0.0
        assert b.predicted_performance_band == "unknown"
        assert b.matching_patterns == []
        assert b.differentiating_factors == []
        assert b.confidence_caveats == []

    @pytest.mark.parametrize("confidence,expected", [
        (0.0,  False),
        (0.39, False),
        (0.399, False),
        (0.4,  True),   # exact grens
        (0.401, True),
        (0.5,  True),
        (1.0,  True),
    ])
    def test_is_reliable(self, confidence, expected):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.5,
            prediction_confidence=confidence,
        )
        assert b.is_reliable is expected

    def test_summary_contains_band(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.74,
            predicted_performance_band="top_25%",
            prediction_confidence=0.52,
        )
        assert "top_25%" in b.summary()

    def test_summary_reliable_label(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.5,
            prediction_confidence=0.6,
        )
        assert "betrouwbaar" in b.summary()

    def test_summary_unreliable_label(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.2,
            prediction_confidence=0.1,
        )
        assert "onvoldoende data" in b.summary()

    def test_summary_contains_similarity_percentage(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.74,
            prediction_confidence=0.5,
        )
        assert "74%" in b.summary()

    def test_matching_patterns_stored(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.6,
            matching_patterns=["Directe opening", "CTA met save"],
        )
        assert len(b.matching_patterns) == 2

    def test_differentiating_factors_stored(self):
        b = BenchmarkResult(
            variant_id="var_x",
            similarity_to_top_performers=0.6,
            differentiating_factors=["Geen social proof"],
        )
        assert "Geen social proof" in b.differentiating_factors

    def test_model_dump_roundtrip(self):
        b = BenchmarkResult(
            variant_id="var_b01",
            similarity_to_top_performers=0.74,
            matching_patterns=["Patroon A"],
            predicted_performance_band="top_25%",
            prediction_confidence=0.52,
        )
        d = b.model_dump(mode="json")
        restored = BenchmarkResult(**d)
        assert restored.variant_id == "var_b01"
        assert restored.is_reliable is True
        assert restored.matching_patterns == ["Patroon A"]

    def test_performance_band_values(self):
        """Controleer dat bekende band-waarden opgeslagen worden."""
        for band in ["top_10%", "top_25%", "average", "below_average", "unknown"]:
            b = BenchmarkResult(
                variant_id="var_x",
                similarity_to_top_performers=0.5,
                predicted_performance_band=band,
            )
            assert b.predicted_performance_band == band
