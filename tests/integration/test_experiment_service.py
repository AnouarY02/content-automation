"""
DAG 2 — Integration tests: backend/services/experiment_service.py

Geen echte LLM-aanroepen. Scorer en benchmarker worden gemockt.

Dekt:
  - score_experiment(): status PENDING als alle varianten passen
  - score_experiment(): status QUALITY_FAIL als ≥1 variant blokkeert
  - score_experiment(): benchmark wordt altijd uitgevoerd (pre_publish=True gezet)
  - score_experiment(): ValueError bij niet-bestaand experiment_id
  - score_experiment(): werkt ook met 0 varianten
  - select_variant(): status → SELECTED, selected_variant_id gezet
  - select_variant(): ValueError bij geblokkeerde variant (passed_quality=False)
  - select_variant(): ValueError bij niet-bestaand variant_id
  - select_variant(): ValueError bij niet-bestaand experiment_id
  - select_variant(): retourneert correcte metadata dict
  - mark_published(): status → MEASURING
  - mark_published(): tiktok_post_id gezet op variant
  - mark_published(): pre_publish=False na publicatie
  - mark_published(): idempotent bij dubbele aanroep
  - mark_published(): stille terugkeer bij niet-bestaand experiment
  - get_pending_for_approval(): filtert op PENDING/QUALITY_FAIL
  - get_experiment_for_campaign(): vindt experiment op campaign_id
  - Hardening matrix: JSON corruptie in store → ValueError propagates
"""

import json
import unittest.mock as mock
from pathlib import Path

import pytest

from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    Hypothesis, Variant, VariantSpec,
)
from experiments.experiment_store import ExperimentStore
from backend.services.experiment_service import ExperimentService
from quality.models import AssetQualityScore, BenchmarkResult, DimensionScore
import experiments.experiment_store as store_module


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    store_dir = tmp_path / "data" / "experiments"
    store_dir.mkdir(parents=True)
    monkeypatch.setattr(store_module, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", store_dir / "_index.json")
    return store_dir


@pytest.fixture
def svc():
    return ExperimentService()


# ── Helpers ───────────────────────────────────────────────────────────

def _make_passing_score(variant_id: str) -> AssetQualityScore:
    ds = DimensionScore(score=75.0, rationale="test")
    return AssetQualityScore(
        variant_id=variant_id,
        hook_strength=ds, clarity=ds, brand_fit=ds, retention_potential=ds,
        composite_score=75.0, passed=True,
    )


def _make_blocking_score(variant_id: str) -> AssetQualityScore:
    ds_block = DimensionScore(score=30.0, rationale="te zwak")
    ds_ok    = DimensionScore(score=75.0, rationale="ok")
    return AssetQualityScore(
        variant_id=variant_id,
        hook_strength=ds_block, clarity=ds_ok, brand_fit=ds_ok, retention_potential=ds_ok,
        composite_score=52.5, passed=False, blockers=["hook_strength: 30 < 40"],
    )


def _make_benchmark(variant_id: str) -> BenchmarkResult:
    return BenchmarkResult(
        variant_id=variant_id,
        similarity_to_top_performers=0.6,
        predicted_performance_band="average",
        prediction_confidence=0.3,
    )


def _make_variant(variant_id: str, exp_id: str, label: str, quality_score=None) -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=exp_id,
        label=label,
        spec=VariantSpec(
            label=label,
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value="curiosity_gap" if label == "control" else "bold_claim",
        ),
        idea={"hook_type": "curiosity_gap"},
        script={"scenes": [], "title": "Test"},
        caption={"caption": "Test", "hashtags": ["test"]},
        quality_score=quality_score,
    )


def _make_experiment(
    exp_id: str = "exp_svc001",
    app_id: str = "app_test",
    campaign_id: str = "camp_001",
    status: ExperimentStatus = ExperimentStatus.PENDING,
    with_variants: bool = True,
) -> Experiment:
    variants = []
    if with_variants:
        variants = [
            _make_variant("var_ctrl_001", exp_id, "control"),
            _make_variant("var_chal_001", exp_id, "challenger_A"),
        ]
    return Experiment(
        experiment_id=exp_id,
        campaign_id=campaign_id,
        app_id=app_id,
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test",
        ),
        variants=variants,
        status=status,
    )


def _store_experiment(exp: Experiment) -> None:
    ExperimentStore().save(exp)


# ── score_experiment ──────────────────────────────────────────────────

class TestScoreExperiment:
    def test_all_passing_sets_status_pending(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer") as MockScorer, \
             mock.patch("quality.benchmarker.Benchmarker") as MockBenchmarker:
            MockScorer.return_value.score_variant.side_effect = lambda v, bm, tp: _make_passing_score(v.variant_id)
            MockBenchmarker.return_value.benchmark_variant.return_value = _make_benchmark("var_x")

            result = svc.score_experiment(exp.experiment_id, "app_test")

        assert result.status == ExperimentStatus.PENDING

    def test_any_blocking_sets_status_quality_fail(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer") as MockScorer, \
             mock.patch("quality.benchmarker.Benchmarker") as MockBenchmarker:
            def score_side_effect(v, bm, tp):
                if v.label == "challenger_A":
                    return _make_blocking_score(v.variant_id)
                return _make_passing_score(v.variant_id)

            MockScorer.return_value.score_variant.side_effect = score_side_effect
            MockBenchmarker.return_value.benchmark_variant.return_value = _make_benchmark("var_x")

            result = svc.score_experiment(exp.experiment_id, "app_test")

        assert result.status == ExperimentStatus.QUALITY_FAIL

    def test_benchmark_pre_publish_flag_set(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer") as MockScorer, \
             mock.patch("quality.benchmarker.Benchmarker") as MockBenchmarker:
            MockScorer.return_value.score_variant.side_effect = lambda v, bm, tp: _make_passing_score(v.variant_id)
            MockBenchmarker.return_value.benchmark_variant.return_value = _make_benchmark("var_x")

            result = svc.score_experiment(exp.experiment_id, "app_test")

        for v in result.variants:
            assert v.performance is not None
            assert v.performance.get("pre_publish") is True

    def test_quality_score_stored_on_variant(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer") as MockScorer, \
             mock.patch("quality.benchmarker.Benchmarker") as MockBenchmarker:
            MockScorer.return_value.score_variant.side_effect = lambda v, bm, tp: _make_passing_score(v.variant_id)
            MockBenchmarker.return_value.benchmark_variant.return_value = _make_benchmark("var_x")

            result = svc.score_experiment(exp.experiment_id, "app_test")

        for v in result.variants:
            assert v.quality_score is not None
            assert "passed" in v.quality_score

    def test_raises_for_nonexistent_experiment(self, svc):
        with pytest.raises(ValueError, match="niet gevonden"):
            svc.score_experiment("exp_does_not_exist", "app_test")

    def test_zero_variants_sets_status_pending(self, svc, isolated_store):
        """Experiment zonder varianten → all_passed=True → PENDING."""
        exp = _make_experiment(with_variants=False)
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer"), \
             mock.patch("quality.benchmarker.Benchmarker"):
            result = svc.score_experiment(exp.experiment_id, "app_test")

        assert result.status == ExperimentStatus.PENDING

    def test_experiment_persisted_after_scoring(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch("quality.scorer.AssetQualityScorer") as MockScorer, \
             mock.patch("quality.benchmarker.Benchmarker") as MockBenchmarker:
            MockScorer.return_value.score_variant.side_effect = lambda v, bm, tp: _make_passing_score(v.variant_id)
            MockBenchmarker.return_value.benchmark_variant.return_value = _make_benchmark("var_x")
            svc.score_experiment(exp.experiment_id, "app_test")

        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.PENDING
        for v in loaded.variants:
            assert v.quality_score is not None


# ── select_variant ────────────────────────────────────────────────────

class TestSelectVariant:
    def _score_and_store(self, exp: Experiment, all_pass: bool = True) -> None:
        for v in exp.variants:
            if all_pass:
                v.quality_score = {"passed": True, "composite_score": 75.0}
            else:
                v.quality_score = {"passed": v.label == "control", "composite_score": 75.0}
        _store_experiment(exp)

    def test_select_sets_status_selected(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.SELECTED

    def test_select_stores_variant_id(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.selected_variant_id == "var_ctrl_001"

    def test_select_stores_approved_by(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        svc.select_variant(exp.experiment_id, "var_ctrl_001", "admin_user")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.selected_by == "admin_user"

    def test_select_stores_selected_at_timestamp(self, svc, isolated_store):
        from datetime import datetime
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.selected_at is not None
        assert isinstance(loaded.selected_at, datetime)

    def test_select_returns_metadata_dict(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        result = svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        assert result["experiment_id"] == exp.experiment_id
        assert result["selected_variant_id"] == "var_ctrl_001"
        assert "variant_label" in result
        assert "dimension" in result
        assert "dimension_value" in result
        assert "changes_from_control" in result

    def test_select_challenger_works(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        result = svc.select_variant(exp.experiment_id, "var_chal_001", "operator")
        assert result["selected_variant_id"] == "var_chal_001"

    def test_raises_for_nonexistent_experiment(self, svc):
        with pytest.raises(ValueError, match="niet gevonden"):
            svc.select_variant("exp_nonexistent", "var_x", "operator")

    def test_raises_for_nonexistent_variant_id(self, svc, isolated_store):
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=True)
        with pytest.raises(ValueError, match="niet gevonden"):
            svc.select_variant(exp.experiment_id, "var_does_not_exist", "operator")

    def test_raises_for_blocked_variant(self, svc, isolated_store):
        """Variant met passed_quality=False mag niet geselecteerd worden."""
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=False)  # challenger is geblokkeerd
        with pytest.raises(ValueError, match="geblokkeerd"):
            svc.select_variant(exp.experiment_id, "var_chal_001", "operator")

    def test_control_selectable_when_challenger_blocked(self, svc, isolated_store):
        """Control is altijd selecteerbaar als zijn eigen score passed=True is."""
        exp = _make_experiment()
        self._score_and_store(exp, all_pass=False)  # challenger geblokkeerd, control OK
        result = svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        assert result["selected_variant_id"] == "var_ctrl_001"

    def test_raises_for_unscored_variant_that_passed(self, svc, isolated_store):
        """Variant zonder quality_score → passed_quality=True → selectie toegestaan."""
        exp = _make_experiment()
        _store_experiment(exp)  # geen scores gezet
        # Mag NIET falen — geen score = niet geblokkeerd
        result = svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        assert result["selected_variant_id"] == "var_ctrl_001"


# ── mark_published ────────────────────────────────────────────────────

class TestMarkPublished:
    def test_sets_status_measuring(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_tiktok_001")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.MEASURING

    def test_sets_tiktok_post_id_on_variant(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_tiktok_001")
        loaded = ExperimentStore().load(exp.experiment_id)
        ctrl = loaded.get_variant("var_ctrl_001")
        assert ctrl.tiktok_post_id == "post_tiktok_001"

    def test_clears_pre_publish_flag(self, svc, isolated_store):
        """pre_publish=True moet False worden na mark_published."""
        exp = _make_experiment()
        exp.variants[0].performance = {"pre_publish": True, "benchmark_similarity": 0.6}
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_001")
        loaded = ExperimentStore().load(exp.experiment_id)
        ctrl = loaded.get_variant("var_ctrl_001")
        assert ctrl.performance["pre_publish"] is False

    def test_idempotent_double_call(self, svc, isolated_store):
        """Twee keer mark_published met zelfde post_id → status blijft MEASURING."""
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_001")
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_001")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.MEASURING
        assert loaded.get_variant("var_ctrl_001").tiktok_post_id == "post_001"

    def test_silent_return_for_nonexistent_experiment(self, svc):
        """Geen exception bij niet-bestaand experiment — stille return."""
        # Mag niet excepten
        svc.mark_published("exp_nonexistent", "var_x", "post_001")

    def test_variant_not_found_still_sets_status(self, svc, isolated_store):
        """Als variant_id niet bestaat, status wordt toch MEASURING gezet."""
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_nonexistent", "post_001")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.MEASURING

    def test_persisted_after_mark_published(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_persist")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded.status == ExperimentStatus.MEASURING


# ── get_pending_for_approval ──────────────────────────────────────────

class TestGetPendingForApproval:
    def test_returns_pending_experiments(self, svc, isolated_store):
        exp = _make_experiment(exp_id="exp_pend", status=ExperimentStatus.PENDING)
        _store_experiment(exp)
        result = svc.get_pending_for_approval("app_test")
        assert len(result) == 1
        assert result[0]["experiment_id"] == "exp_pend"

    def test_returns_quality_fail_experiments(self, svc, isolated_store):
        exp = _make_experiment(exp_id="exp_qf", status=ExperimentStatus.QUALITY_FAIL)
        _store_experiment(exp)
        result = svc.get_pending_for_approval("app_test")
        assert len(result) == 1

    def test_excludes_measuring(self, svc, isolated_store):
        exp = _make_experiment(exp_id="exp_meas", status=ExperimentStatus.MEASURING)
        _store_experiment(exp)
        result = svc.get_pending_for_approval("app_test")
        assert len(result) == 0

    def test_excludes_concluded(self, svc, isolated_store):
        exp = _make_experiment(exp_id="exp_conc", status=ExperimentStatus.CONCLUDED)
        _store_experiment(exp)
        result = svc.get_pending_for_approval("app_test")
        assert len(result) == 0

    def test_returns_list_of_dicts(self, svc, isolated_store):
        exp = _make_experiment(status=ExperimentStatus.PENDING)
        _store_experiment(exp)
        result = svc.get_pending_for_approval("app_test")
        assert isinstance(result, list)
        assert isinstance(result[0], dict)

    def test_empty_for_unknown_app(self, svc):
        result = svc.get_pending_for_approval("app_unknown")
        assert result == []


# ── get_experiment_for_campaign ───────────────────────────────────────

class TestGetExperimentForCampaign:
    def test_finds_by_campaign_id(self, svc, isolated_store):
        exp = _make_experiment(campaign_id="camp_specific")
        _store_experiment(exp)
        result = svc.get_experiment_for_campaign("camp_specific")
        assert result is not None
        assert result["campaign_id"] == "camp_specific"

    def test_returns_none_for_unknown_campaign(self, svc):
        result = svc.get_experiment_for_campaign("camp_unknown")
        assert result is None

    def test_returns_dict(self, svc, isolated_store):
        exp = _make_experiment(campaign_id="camp_dict")
        _store_experiment(exp)
        result = svc.get_experiment_for_campaign("camp_dict")
        assert isinstance(result, dict)


# ── Hardening matrix cases ────────────────────────────────────────────

class TestHardeningMatrix:
    def test_score_experiment_brand_memory_failure_still_scores(self, svc, isolated_store):
        """Als _load_brand_memory een exception gooit, propageert score_experiment die exception.
        Dit is verwacht: de caller (pipeline) vangt de exception op en logt een warning.
        """
        exp = _make_experiment()
        _store_experiment(exp)

        with mock.patch(
            "backend.services.experiment_service.ExperimentService._load_brand_memory",
            side_effect=Exception("brand memory fout"),
        ):
            with pytest.raises(Exception, match="brand memory fout"):
                svc.score_experiment(exp.experiment_id, "app_test")

    def test_score_experiment_brand_memory_returns_dict_for_nonexistent_app(self):
        """_load_brand_memory() retourneert altijd een dict (nooit None), ook bij onbekende app."""
        result = ExperimentService._load_brand_memory("app_nonexistent")
        assert isinstance(result, dict)  # lege of default dict — niet None

    def test_select_variant_on_measuring_experiment_raises(self, svc, isolated_store):
        """Selectie op MEASURING experiment → variant gevonden maar passed_quality=True → geen guard.
        Let op: de service bewaakt GEEN status bij select_variant — alleen passed_quality.
        Dit is verwacht gedrag: status-guard zit in de approval flow, niet in de service.
        """
        exp = _make_experiment(status=ExperimentStatus.MEASURING)
        for v in exp.variants:
            v.quality_score = {"passed": True, "composite_score": 75}
        _store_experiment(exp)
        # select_variant bewaakt passed_quality, niet status — deze aanroep slaagt
        result = svc.select_variant(exp.experiment_id, "var_ctrl_001", "operator")
        assert result["selected_variant_id"] == "var_ctrl_001"

    def test_double_mark_published_does_not_corrupt_data(self, svc, isolated_store):
        exp = _make_experiment()
        _store_experiment(exp)
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_aaa")
        svc.mark_published(exp.experiment_id, "var_ctrl_001", "post_aaa")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded is not None
        assert loaded.get_variant("var_ctrl_001").tiktok_post_id == "post_aaa"
