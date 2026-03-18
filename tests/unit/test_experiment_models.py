"""
DAG 1 — Unit tests: experiments/models.py

Dekt:
  - Alle enums en hun geldige waarden
  - Model-constructie minimaal en volledig
  - Properties: is_control, passed_quality, view_count
  - Experiment hulpmethoden: get_variant, get_control, get_challengers
  - any_quality_blocked / all_quality_scored
  - is_active / is_finished
  - Hypothesis.as_prompt_context
  - Serialization roundtrip (model_dump / model_dump_json)
  - Edge cases: lege varianten, None velden
"""

import json
from datetime import datetime

import pytest

from experiments.models import (
    CaptionStyle,
    CtaType,
    Experiment,
    ExperimentDimension,
    ExperimentStatus,
    Hypothesis,
    HookType,
    Variant,
    VariantPerformanceComparison,
    VariantSpec,
)


# ── Enum tests ────────────────────────────────────────────────────────

class TestExperimentDimension:
    def test_all_values_accessible(self):
        assert ExperimentDimension.HOOK_TYPE.value == "hook_type"
        assert ExperimentDimension.CTA_TYPE.value == "cta_type"
        assert ExperimentDimension.CAPTION_STYLE.value == "caption_style"
        assert ExperimentDimension.VIDEO_FORMAT.value == "video_format"
        assert ExperimentDimension.POSTING_WINDOW.value == "posting_window"

    def test_enum_count(self):
        assert len(ExperimentDimension) == 5

    def test_from_string(self):
        assert ExperimentDimension("hook_type") == ExperimentDimension.HOOK_TYPE

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ExperimentDimension("invalid_dim")


class TestHookType:
    def test_all_values(self):
        expected = {"bold_claim", "curiosity_gap", "question", "social_proof", "contrast", "tutorial"}
        assert {h.value for h in HookType} == expected

    def test_enum_count(self):
        assert len(HookType) == 6


class TestCtaType:
    def test_all_values(self):
        expected = {"soft", "hard", "social", "retention"}
        assert {c.value for c in CtaType} == expected


class TestCaptionStyle:
    def test_all_values(self):
        expected = {"minimal", "storytelling", "list", "question", "hook_repeat"}
        assert {s.value for s in CaptionStyle} == expected


class TestExperimentStatus:
    def test_all_statuses(self):
        expected = {
            "generating", "quality_fail", "pending", "selected",
            "published", "measuring", "concluded", "inconclusive",
        }
        assert {s.value for s in ExperimentStatus} == expected

    def test_enum_count(self):
        assert len(ExperimentStatus) == 8


# ── Hypothesis tests ──────────────────────────────────────────────────

class TestHypothesis:
    def test_minimal_construction(self):
        h = Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test rationale",
        )
        assert h.dimension == ExperimentDimension.HOOK_TYPE
        assert h.supporting_evidence == []

    def test_full_construction(self):
        h = Hypothesis(
            dimension=ExperimentDimension.CTA_TYPE,
            control_value="soft",
            challenger_value="hard",
            rationale="Hard CTA presteert beter",
            supporting_evidence=["camp_001: hard, score=85"],
        )
        assert len(h.supporting_evidence) == 1

    def test_as_prompt_context_contains_dimension(self):
        h = Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test",
        )
        ctx = h.as_prompt_context()
        assert "hook_type" in ctx
        assert "curiosity_gap" in ctx
        assert "bold_claim" in ctx
        assert "Test" in ctx

    def test_as_prompt_context_is_string(self):
        h = Hypothesis(
            dimension=ExperimentDimension.CAPTION_STYLE,
            control_value="minimal",
            challenger_value="storytelling",
            rationale="X",
        )
        assert isinstance(h.as_prompt_context(), str)
        assert len(h.as_prompt_context()) > 0


# ── VariantSpec tests ─────────────────────────────────────────────────

class TestVariantSpec:
    def test_default_variant_id_generated(self):
        spec = VariantSpec(
            label="control",
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value="bold_claim",
        )
        assert spec.variant_id.startswith("var_")
        assert len(spec.variant_id) > 4

    def test_unique_ids_generated(self):
        specs = [
            VariantSpec(label="control", dimension=ExperimentDimension.HOOK_TYPE, dimension_value="x")
            for _ in range(10)
        ]
        ids = [s.variant_id for s in specs]
        assert len(set(ids)) == 10

    def test_custom_variant_id(self):
        spec = VariantSpec(
            variant_id="var_custom_01",
            label="challenger_A",
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value="question",
        )
        assert spec.variant_id == "var_custom_01"

    def test_empty_changes_from_control(self):
        spec = VariantSpec(
            label="control",
            dimension=ExperimentDimension.CTA_TYPE,
            dimension_value="soft",
        )
        assert spec.changes_from_control == []
        assert spec.generation_instruction == ""


# ── Variant tests ─────────────────────────────────────────────────────

class TestVariant:
    def _make_variant(self, label="control", quality_score=None, performance=None) -> Variant:
        return Variant(
            variant_id="var_ctrl_001",
            experiment_id="exp_test0001",
            label=label,
            spec=VariantSpec(
                label=label,
                dimension=ExperimentDimension.HOOK_TYPE,
                dimension_value="curiosity_gap",
            ),
            idea={"title": "Test"},
            script={"scenes": []},
            caption={"caption": "Test caption"},
            quality_score=quality_score,
            performance=performance,
        )

    def test_is_control_true(self):
        assert self._make_variant(label="control").is_control is True

    def test_is_control_false_for_challenger(self):
        assert self._make_variant(label="challenger_A").is_control is False

    def test_passed_quality_none_score_returns_true(self):
        """Nog niet gescoord → niet geblokkeerd."""
        v = self._make_variant(quality_score=None)
        assert v.passed_quality is True

    def test_passed_quality_passed_true(self):
        v = self._make_variant(quality_score={"passed": True, "composite_score": 80})
        assert v.passed_quality is True

    def test_passed_quality_passed_false(self):
        v = self._make_variant(quality_score={"passed": False, "composite_score": 30})
        assert v.passed_quality is False

    def test_passed_quality_missing_passed_key_defaults_true(self):
        v = self._make_variant(quality_score={"composite_score": 80})
        assert v.passed_quality is True

    def test_view_count_no_performance(self):
        v = self._make_variant(performance=None)
        assert v.view_count == 0

    def test_view_count_empty_performance(self):
        v = self._make_variant(performance={})
        assert v.view_count == 0

    def test_view_count_play_count(self):
        v = self._make_variant(performance={"play_count": 5000})
        assert v.view_count == 5000

    def test_view_count_views_fallback(self):
        v = self._make_variant(performance={"views": 3200})
        assert v.view_count == 3200

    def test_view_count_play_count_takes_priority(self):
        v = self._make_variant(performance={"play_count": 5000, "views": 3200})
        assert v.view_count == 5000

    def test_view_count_zero_play_count(self):
        v = self._make_variant(performance={"play_count": 0})
        assert v.view_count == 0

    def test_optional_fields_default_none(self):
        v = self._make_variant()
        assert v.video_path is None
        assert v.tiktok_post_id is None
        assert v.performance is None
        assert v.performance_score is None

    def test_created_at_auto_set(self):
        v = self._make_variant()
        assert isinstance(v.created_at, datetime)


# ── Experiment tests ──────────────────────────────────────────────────

class TestExperiment:
    def _make_experiment(self, status=ExperimentStatus.PENDING) -> Experiment:
        ctrl = Variant(
            variant_id="var_ctrl_001",
            experiment_id="exp_test0001",
            label="control",
            spec=VariantSpec(label="control", dimension=ExperimentDimension.HOOK_TYPE,
                             dimension_value="curiosity_gap"),
            idea={}, script={}, caption={},
        )
        chal = Variant(
            variant_id="var_chal_001",
            experiment_id="exp_test0001",
            label="challenger_A",
            spec=VariantSpec(label="challenger_A", dimension=ExperimentDimension.HOOK_TYPE,
                             dimension_value="bold_claim"),
            idea={}, script={}, caption={},
        )
        return Experiment(
            experiment_id="exp_test0001",
            campaign_id="camp_test001",
            app_id="app_test",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.HOOK_TYPE,
                control_value="curiosity_gap",
                challenger_value="bold_claim",
                rationale="Test",
            ),
            variants=[ctrl, chal],
            status=status,
        )

    def test_default_experiment_id_generated(self):
        exp = self._make_experiment()
        assert exp.experiment_id.startswith("exp_")

    def test_custom_experiment_id(self):
        exp = self._make_experiment()
        assert exp.experiment_id == "exp_test0001"

    def test_get_variant_found(self):
        exp = self._make_experiment()
        v = exp.get_variant("var_ctrl_001")
        assert v is not None
        assert v.label == "control"

    def test_get_variant_not_found_returns_none(self):
        exp = self._make_experiment()
        assert exp.get_variant("var_nonexistent") is None

    def test_get_control(self):
        exp = self._make_experiment()
        ctrl = exp.get_control()
        assert ctrl is not None
        assert ctrl.label == "control"

    def test_get_challengers(self):
        exp = self._make_experiment()
        challengers = exp.get_challengers()
        assert len(challengers) == 1
        assert challengers[0].label == "challenger_A"

    def test_get_control_empty_variants(self):
        exp = self._make_experiment()
        exp.variants = []
        assert exp.get_control() is None

    def test_all_quality_scored_false_when_none(self):
        exp = self._make_experiment()
        assert exp.all_quality_scored() is False

    def test_all_quality_scored_true(self):
        exp = self._make_experiment()
        for v in exp.variants:
            v.quality_score = {"passed": True, "composite_score": 80}
        assert exp.all_quality_scored() is True

    def test_any_quality_blocked_false_no_scores(self):
        exp = self._make_experiment()
        assert exp.any_quality_blocked() is False

    def test_any_quality_blocked_true(self):
        exp = self._make_experiment()
        exp.variants[0].quality_score = {"passed": False, "composite_score": 30}
        assert exp.any_quality_blocked() is True

    def test_any_quality_blocked_false_all_passed(self):
        exp = self._make_experiment()
        for v in exp.variants:
            v.quality_score = {"passed": True, "composite_score": 80}
        assert exp.any_quality_blocked() is False

    def test_variants_with_performance_empty(self):
        exp = self._make_experiment()
        assert exp.variants_with_performance() == []

    def test_variants_with_performance_some(self):
        exp = self._make_experiment()
        exp.variants[0].performance = {"play_count": 5000}
        result = exp.variants_with_performance()
        assert len(result) == 1
        assert result[0].variant_id == "var_ctrl_001"

    @pytest.mark.parametrize("status,expected", [
        (ExperimentStatus.PUBLISHED, True),
        (ExperimentStatus.MEASURING, True),
        (ExperimentStatus.PENDING, False),
        (ExperimentStatus.CONCLUDED, False),
        (ExperimentStatus.GENERATING, False),
    ])
    def test_is_active(self, status, expected):
        exp = self._make_experiment(status=status)
        assert exp.is_active() is expected

    @pytest.mark.parametrize("status,expected", [
        (ExperimentStatus.CONCLUDED, True),
        (ExperimentStatus.INCONCLUSIVE, True),
        (ExperimentStatus.MEASURING, False),
        (ExperimentStatus.PENDING, False),
    ])
    def test_is_finished(self, status, expected):
        exp = self._make_experiment(status=status)
        assert exp.is_finished() is expected

    def test_empty_variants_list(self):
        exp = Experiment(
            experiment_id="exp_empty",
            campaign_id="camp_x",
            app_id="app_x",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.HOOK_TYPE,
                control_value="a", challenger_value="b", rationale="x",
            ),
        )
        assert exp.variants == []
        assert exp.get_control() is None
        assert exp.get_challengers() == []
        assert exp.any_quality_blocked() is False
        assert exp.all_quality_scored() is True  # vacuously True (all([]) == True)

    def test_default_status_is_generating(self):
        exp = Experiment(
            campaign_id="camp_x",
            app_id="app_x",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.HOOK_TYPE,
                control_value="a", challenger_value="b", rationale="x",
            ),
        )
        assert exp.status == ExperimentStatus.GENERATING

    def test_optional_conclusion_fields_default_none(self):
        exp = self._make_experiment()
        assert exp.selected_variant_id is None
        assert exp.winning_variant_id is None
        assert exp.causal_confidence is None
        assert exp.conclusion is None
        assert exp.concluded_at is None


# ── Serialization roundtrip ───────────────────────────────────────────

class TestSerialization:
    def test_hypothesis_model_dump(self):
        h = Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test",
            supporting_evidence=["evidence_1"],
        )
        d = h.model_dump(mode="json")
        assert d["dimension"] == "hook_type"
        assert d["supporting_evidence"] == ["evidence_1"]

    def test_experiment_model_dump_json_roundtrip(self):
        exp = Experiment(
            experiment_id="exp_serial01",
            campaign_id="camp_s",
            app_id="app_s",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.CTA_TYPE,
                control_value="soft",
                challenger_value="hard",
                rationale="Serialization test",
            ),
            status=ExperimentStatus.MEASURING,
        )
        raw = exp.model_dump_json()
        restored = Experiment(**json.loads(raw))
        assert restored.experiment_id == exp.experiment_id
        assert restored.status == ExperimentStatus.MEASURING
        assert restored.hypothesis.dimension == ExperimentDimension.CTA_TYPE

    def test_variant_with_quality_score_roundtrip(self):
        v = Variant(
            variant_id="var_s01",
            experiment_id="exp_s01",
            label="control",
            spec=VariantSpec(label="control", dimension=ExperimentDimension.HOOK_TYPE,
                             dimension_value="bold_claim"),
            idea={}, script={}, caption={},
            quality_score={"passed": True, "composite_score": 80.5},
            performance={"play_count": 4500},
        )
        d = v.model_dump(mode="json")
        restored = Variant(**d)
        assert restored.quality_score["passed"] is True
        assert restored.view_count == 4500

    def test_datetime_fields_serialize_to_string(self):
        exp = Experiment(
            campaign_id="camp_x",
            app_id="app_x",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.HOOK_TYPE,
                control_value="a", challenger_value="b", rationale="x",
            ),
        )
        d = exp.model_dump(mode="json")
        assert isinstance(d["created_at"], str)


# ── VariantPerformanceComparison tests ────────────────────────────────

class TestVariantPerformanceComparison:
    def test_minimal_construction(self):
        cmp = VariantPerformanceComparison(
            experiment_id="exp_x",
            dimension=ExperimentDimension.HOOK_TYPE,
        )
        assert cmp.has_winner is False
        assert cmp.sufficient_data is False
        assert cmp.causal_confidence == 0.0

    def test_has_winner_false_without_sufficient_data(self):
        cmp = VariantPerformanceComparison(
            experiment_id="exp_x",
            dimension=ExperimentDimension.HOOK_TYPE,
            winner_variant_id="var_x",
            sufficient_data=False,
        )
        assert cmp.has_winner is False

    def test_has_winner_true(self):
        cmp = VariantPerformanceComparison(
            experiment_id="exp_x",
            dimension=ExperimentDimension.HOOK_TYPE,
            winner_variant_id="var_x",
            sufficient_data=True,
        )
        assert cmp.has_winner is True

    def test_concluded_at_auto_set(self):
        cmp = VariantPerformanceComparison(
            experiment_id="exp_x",
            dimension=ExperimentDimension.HOOK_TYPE,
        )
        assert isinstance(cmp.concluded_at, datetime)
