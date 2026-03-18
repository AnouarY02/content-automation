"""
DAG 4 — Quality gate blocking: ExperimentService.score_experiment()

Dekt:
  - Alle varianten passen → experiment.status = PENDING
  - Één variant faalt → experiment.status = QUALITY_FAIL
  - quality_score wordt opgeslagen op variant (als dict)
  - performance (benchmark) wordt opgeslagen op variant
  - passed_quality=False → select_variant() gooit ValueError
  - passed_quality=True → select_variant() slaagt
  - Blokkering via composite score (< COMPOSITE_BLOCK)
  - Onbekend variant_id bij select_variant → ValueError
  - Onbekend experiment_id bij select_variant → ValueError
  - quality_score dict bevat 'passed' key
  - Benchmark pre_publish flag wordt gezet
"""

import unittest.mock as mock

import pytest

import experiments.experiment_store as store_module
from experiments.experiment_store import ExperimentStore
from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    Hypothesis, Variant, VariantSpec,
)
from backend.services.experiment_service import ExperimentService


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    store_dir = tmp_path / "experiments"
    store_dir.mkdir()
    monkeypatch.setattr(store_module, "STORE_DIR",  store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", store_dir / "_index.json")
    return store_dir


# ── Helpers ───────────────────────────────────────────────────────────

def _make_quality_score(passed: bool, composite: float = 80.0) -> dict:
    return {
        "passed":           passed,
        "composite_score":  composite,
        "blockers":         [] if passed else [f"hook_strength: 30 < 40"],
        "warnings":         [],
        "variant_id":       "var_x",
        "hook_strength":    {"score": 80 if passed else 30, "rationale": "test", "flags": []},
        "clarity":          {"score": 80, "rationale": "test", "flags": []},
        "brand_fit":        {"score": 80, "rationale": "test", "flags": []},
        "retention_potential": {"score": 80, "rationale": "test", "flags": []},
    }


def _make_benchmark_mock(confidence: float = 0.5):
    m = mock.MagicMock()
    m.similarity_to_top_performers = 0.7
    m.predicted_performance_band   = "top_25%"
    m.prediction_confidence        = confidence
    m.matching_patterns            = ["Directe opening"]
    return m


def _make_variant(variant_id: str, exp_id: str, label: str = "control") -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=exp_id,
        label=label,
        spec=VariantSpec(
            label=label,
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value="curiosity_gap" if label == "control" else "bold_claim",
        ),
        idea={}, script={}, caption={},
    )


def _make_experiment(
    exp_id: str = "exp_gate001",
    app_id: str = "app_qg",
    num_variants: int = 2,
) -> Experiment:
    variants = [
        _make_variant(f"var_{i}", exp_id, "control" if i == 0 else "challenger_A")
        for i in range(num_variants)
    ]
    exp = Experiment(
        experiment_id=exp_id,
        campaign_id="camp_test",
        app_id=app_id,
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test hypothese",
        ),
        variants=variants,
        status=ExperimentStatus.GENERATING,
    )
    ExperimentStore().save(exp)
    return exp


def _mock_scorer_and_benchmarker(passed_map: dict[str, bool]):
    """
    passed_map: {variant_id: passed}
    Retourneert (mock_scorer_cls, mock_bench_cls) context managers.
    """
    def score_side_effect(variant, brand_memory, top_performers):
        passed = passed_map.get(variant.variant_id, True)
        score_mock = mock.MagicMock()
        score_mock.passed   = passed
        score_mock.blockers = [] if passed else ["hook_strength: 30 < 40"]
        score_mock.model_dump.return_value = _make_quality_score(passed)
        return score_mock

    mock_scorer_instance     = mock.MagicMock()
    mock_scorer_instance.score_variant.side_effect = score_side_effect
    mock_benchmarker_instance = mock.MagicMock()
    mock_benchmarker_instance.benchmark_variant.return_value = _make_benchmark_mock()

    return (
        mock.patch("quality.scorer.AssetQualityScorer",  return_value=mock_scorer_instance),
        mock.patch("quality.benchmarker.Benchmarker",    return_value=mock_benchmarker_instance),
    )


# ── Alle varianten passen ─────────────────────────────────────────────

class TestAlleVariantenPassen:
    def test_status_pending_na_scoring(self):
        exp = _make_experiment("exp_pass001")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_pass001", "app_qg")
        assert result.status == ExperimentStatus.PENDING

    def test_quality_score_op_variant_opgeslagen(self):
        exp = _make_experiment("exp_pass002")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_pass002", "app_qg")
        for variant in result.variants:
            assert variant.quality_score is not None
            assert "passed" in variant.quality_score

    def test_benchmark_op_variant_opgeslagen(self):
        exp = _make_experiment("exp_pass003")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_pass003", "app_qg")
        for variant in result.variants:
            assert variant.performance is not None
            assert "benchmark_similarity" in variant.performance

    def test_benchmark_pre_publish_flag_gezet(self):
        exp = _make_experiment("exp_pass004")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_pass004", "app_qg")
        for variant in result.variants:
            assert variant.performance.get("pre_publish") is True

    def test_passed_quality_true_na_scoring(self):
        exp = _make_experiment("exp_pass005")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_pass005", "app_qg")
        for variant in result.variants:
            assert variant.passed_quality is True

    def test_status_persistent_in_store(self):
        exp = _make_experiment("exp_pass006")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": True}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            svc.score_experiment("exp_pass006", "app_qg")
        reloaded = ExperimentStore().load("exp_pass006")
        assert reloaded.status == ExperimentStatus.PENDING


# ── Eén of meer varianten falen ───────────────────────────────────────

class TestVariantFaalt:
    def test_status_quality_fail_bij_een_fout(self):
        exp = _make_experiment("exp_fail001")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": False}  # challenger faalt
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_fail001", "app_qg")
        assert result.status == ExperimentStatus.QUALITY_FAIL

    def test_geblokkeerde_variant_passed_quality_false(self):
        exp = _make_experiment("exp_fail002")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": True, "var_1": False}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_fail002", "app_qg")
        blocked = [v for v in result.variants if not v.passed_quality]
        assert len(blocked) >= 1

    def test_quality_fail_persistent_in_store(self):
        exp = _make_experiment("exp_fail003")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": False, "var_1": False}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            svc.score_experiment("exp_fail003", "app_qg")
        reloaded = ExperimentStore().load("exp_fail003")
        assert reloaded.status == ExperimentStatus.QUALITY_FAIL

    def test_beide_falen_status_quality_fail(self):
        exp = _make_experiment("exp_fail004")
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(
            {"var_0": False, "var_1": False}
        )
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            result = svc.score_experiment("exp_fail004", "app_qg")
        assert result.status == ExperimentStatus.QUALITY_FAIL


# ── select_variant guards ─────────────────────────────────────────────

class TestSelectVariantGuards:
    def _score_experiment(self, exp_id: str, passed_map: dict):
        scorer_patch, bench_patch = _mock_scorer_and_benchmarker(passed_map)
        with scorer_patch, bench_patch:
            svc = ExperimentService()
            svc.score_experiment(exp_id, "app_qg")

    def test_select_gescoorde_passende_variant_slaagt(self):
        exp = _make_experiment("exp_sel_ok001")
        self._score_experiment("exp_sel_ok001", {"var_0": True, "var_1": True})
        svc = ExperimentService()
        result = svc.select_variant("exp_sel_ok001", "var_0", "operator")
        assert result["selected_variant_id"] == "var_0"

    def test_select_geblokkeerde_variant_gooit_valueerror(self):
        exp = _make_experiment("exp_sel_blk001")
        self._score_experiment("exp_sel_blk001", {"var_0": True, "var_1": False})
        svc = ExperimentService()
        with pytest.raises(ValueError, match="geblokkeerd"):
            svc.select_variant("exp_sel_blk001", "var_1", "operator")

    def test_select_onbekend_variant_id_gooit_valueerror(self):
        exp = _make_experiment("exp_sel_unk001")
        self._score_experiment("exp_sel_unk001", {"var_0": True, "var_1": True})
        svc = ExperimentService()
        with pytest.raises(ValueError):
            svc.select_variant("exp_sel_unk001", "var_bestaat_niet", "operator")

    def test_select_onbekend_experiment_gooit_valueerror(self):
        svc = ExperimentService()
        with pytest.raises(ValueError):
            svc.select_variant("exp_bestaat_nooit", "var_0", "operator")

    def test_select_zet_status_selected(self):
        exp = _make_experiment("exp_sel_st001")
        self._score_experiment("exp_sel_st001", {"var_0": True, "var_1": True})
        svc = ExperimentService()
        svc.select_variant("exp_sel_st001", "var_0", "operator")
        reloaded = ExperimentStore().load("exp_sel_st001")
        assert reloaded.status == ExperimentStatus.SELECTED
