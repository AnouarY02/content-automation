"""
DAG 4 — Volledige experiment lifecycle

Dekt de state machine:
  GENERATING → PENDING (na score_experiment, alles passen)
  PENDING → SELECTED (na select_variant)
  SELECTED → MEASURING (na mark_published)
  MEASURING → CONCLUDED (handmatig via store, voor comparator)

Per transitie:
  - Status correct bijgewerkt
  - Persistentie in store (reload toont juiste status)
  - Relevante velden gevuld (selected_variant_id, selected_by, tiktok_post_id)

Extra:
  - get_concluded_dimensions() telt geconcludeerde experimenten
  - get_winning_values() retourneert winnende dimensie-waarden
  - get_pending_experiments() filtert op PENDING en QUALITY_FAIL
  - get_measuring_experiments() filtert op MEASURING
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

def _make_variant(variant_id: str, exp_id: str, label: str, hook_type: str) -> Variant:
    return Variant(
        variant_id=variant_id,
        experiment_id=exp_id,
        label=label,
        spec=VariantSpec(
            label=label,
            dimension=ExperimentDimension.HOOK_TYPE,
            dimension_value=hook_type,
        ),
        idea={}, script={}, caption={},
    )


def _make_experiment(
    exp_id: str,
    app_id: str = "app_lc",
    status: ExperimentStatus = ExperimentStatus.GENERATING,
) -> Experiment:
    exp = Experiment(
        experiment_id=exp_id,
        campaign_id="camp_lc_001",
        app_id=app_id,
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Lifecycle test",
        ),
        variants=[
            _make_variant("var_ctrl", exp_id, "control",     "curiosity_gap"),
            _make_variant("var_chal", exp_id, "challenger_A", "bold_claim"),
        ],
        status=status,
    )
    ExperimentStore().save(exp)
    return exp


def _score_all_passing(exp_id: str, app_id: str = "app_lc"):
    """Hulpmethode: score_experiment met alle varianten passend."""
    def score_side_effect(variant, brand_memory, top_performers):
        score_mock = mock.MagicMock()
        score_mock.passed   = True
        score_mock.blockers = []
        score_mock.model_dump.return_value = {
            "passed": True, "composite_score": 78.0, "blockers": [], "warnings": [],
            "variant_id": variant.variant_id,
            "hook_strength":       {"score": 80, "rationale": "ok", "flags": []},
            "clarity":             {"score": 78, "rationale": "ok", "flags": []},
            "brand_fit":           {"score": 75, "rationale": "ok", "flags": []},
            "retention_potential": {"score": 80, "rationale": "ok", "flags": []},
        }
        return score_mock

    mock_scorer     = mock.MagicMock()
    mock_scorer.score_variant.side_effect = score_side_effect
    mock_benchmarker = mock.MagicMock()
    mock_benchmarker.benchmark_variant.return_value = mock.MagicMock(
        similarity_to_top_performers=0.7,
        predicted_performance_band="top_25%",
        prediction_confidence=0.5,
        matching_patterns=[],
    )

    with (
        mock.patch("quality.scorer.AssetQualityScorer",  return_value=mock_scorer),
        mock.patch("quality.benchmarker.Benchmarker",    return_value=mock_benchmarker),
    ):
        ExperimentService().score_experiment(exp_id, app_id)


# ── GENERATING → PENDING ──────────────────────────────────────────────

class TestGeneratingToPending:
    def test_initieel_status_generating(self):
        exp = _make_experiment("exp_lc_g001")
        assert exp.status == ExperimentStatus.GENERATING

    def test_na_scoring_status_pending(self):
        _make_experiment("exp_lc_g002")
        _score_all_passing("exp_lc_g002")
        reloaded = ExperimentStore().load("exp_lc_g002")
        assert reloaded.status == ExperimentStatus.PENDING

    def test_quality_score_op_elke_variant(self):
        _make_experiment("exp_lc_g003")
        _score_all_passing("exp_lc_g003")
        reloaded = ExperimentStore().load("exp_lc_g003")
        for variant in reloaded.variants:
            assert variant.quality_score is not None

    def test_experiment_in_pending_experiments_na_scoring(self):
        _make_experiment("exp_lc_g004")
        _score_all_passing("exp_lc_g004")
        pending = ExperimentStore().get_pending_experiments("app_lc")
        ids = [e.experiment_id for e in pending]
        assert "exp_lc_g004" in ids


# ── PENDING → SELECTED ────────────────────────────────────────────────

class TestPendingToSelected:
    def _prepare(self, exp_id: str) -> Experiment:
        _make_experiment(exp_id)
        _score_all_passing(exp_id)
        return ExperimentStore().load(exp_id)

    def test_na_select_status_selected(self):
        self._prepare("exp_lc_s001")
        ExperimentService().select_variant("exp_lc_s001", "var_ctrl", "operator")
        reloaded = ExperimentStore().load("exp_lc_s001")
        assert reloaded.status == ExperimentStatus.SELECTED

    def test_selected_variant_id_opgeslagen(self):
        self._prepare("exp_lc_s002")
        ExperimentService().select_variant("exp_lc_s002", "var_ctrl", "operator")
        reloaded = ExperimentStore().load("exp_lc_s002")
        assert reloaded.selected_variant_id == "var_ctrl"

    def test_selected_by_opgeslagen(self):
        self._prepare("exp_lc_s003")
        ExperimentService().select_variant("exp_lc_s003", "var_chal", "gebruiker_anouar")
        reloaded = ExperimentStore().load("exp_lc_s003")
        assert reloaded.selected_by == "gebruiker_anouar"

    def test_selected_at_ingevuld(self):
        self._prepare("exp_lc_s004")
        ExperimentService().select_variant("exp_lc_s004", "var_ctrl", "operator")
        reloaded = ExperimentStore().load("exp_lc_s004")
        assert reloaded.selected_at is not None

    def test_select_challenger_mogelijk(self):
        self._prepare("exp_lc_s005")
        result = ExperimentService().select_variant("exp_lc_s005", "var_chal", "operator")
        assert result["selected_variant_id"] == "var_chal"

    def test_niet_in_pending_na_select(self):
        self._prepare("exp_lc_s006")
        ExperimentService().select_variant("exp_lc_s006", "var_ctrl", "operator")
        pending = ExperimentStore().get_pending_experiments("app_lc")
        ids = [e.experiment_id for e in pending]
        assert "exp_lc_s006" not in ids


# ── SELECTED → MEASURING ──────────────────────────────────────────────

class TestSelectedToMeasuring:
    def _prepare(self, exp_id: str) -> Experiment:
        _make_experiment(exp_id)
        _score_all_passing(exp_id)
        ExperimentService().select_variant(exp_id, "var_ctrl", "operator")
        return ExperimentStore().load(exp_id)

    def test_na_publish_status_measuring(self):
        self._prepare("exp_lc_m001")
        ExperimentService().mark_published("exp_lc_m001", "var_ctrl", "tiktok_post_001")
        reloaded = ExperimentStore().load("exp_lc_m001")
        assert reloaded.status == ExperimentStatus.MEASURING

    def test_tiktok_post_id_opgeslagen(self):
        self._prepare("exp_lc_m002")
        ExperimentService().mark_published("exp_lc_m002", "var_ctrl", "tiktok_post_xyz")
        reloaded = ExperimentStore().load("exp_lc_m002")
        ctrl = reloaded.get_variant("var_ctrl")
        assert ctrl.tiktok_post_id == "tiktok_post_xyz"

    def test_pre_publish_flag_verwijderd_na_publish(self):
        self._prepare("exp_lc_m003")
        ExperimentService().mark_published("exp_lc_m003", "var_ctrl", "post_abc")
        reloaded = ExperimentStore().load("exp_lc_m003")
        ctrl = reloaded.get_variant("var_ctrl")
        if ctrl.performance:
            assert ctrl.performance.get("pre_publish") is False

    def test_measuring_in_get_measuring_experiments(self):
        self._prepare("exp_lc_m004")
        ExperimentService().mark_published("exp_lc_m004", "var_ctrl", "post_m4")
        measuring = ExperimentStore().get_measuring_experiments()
        ids = [e.experiment_id for e in measuring]
        assert "exp_lc_m004" in ids

    def test_mark_published_onbekend_experiment_stille_skip(self):
        """Onbekend experiment_id → geen exception (silent skip)."""
        ExperimentService().mark_published("exp_bestaat_niet", "var_x", "post_x")


# ── MEASURING → CONCLUDED ─────────────────────────────────────────────

class TestMeasuringToConcluded:
    def _prepare_measuring(self, exp_id: str) -> Experiment:
        _make_experiment(exp_id)
        _score_all_passing(exp_id)
        ExperimentService().select_variant(exp_id, "var_ctrl", "operator")
        ExperimentService().mark_published(exp_id, "var_ctrl", f"post_{exp_id}")
        return ExperimentStore().load(exp_id)

    def _conclude(self, exp_id: str, winning_variant_id: str):
        """Simuleert comparator die experiment concludeert."""
        store = ExperimentStore()
        exp = store.load(exp_id)
        exp.status             = ExperimentStatus.CONCLUDED
        exp.winning_variant_id = winning_variant_id
        exp.causal_confidence  = 0.78
        exp.conclusion         = "bold_claim presteert beter (+12% views)"
        store.save(exp)

    def test_na_conclude_status_concluded(self):
        self._prepare_measuring("exp_lc_c001")
        self._conclude("exp_lc_c001", "var_ctrl")
        reloaded = ExperimentStore().load("exp_lc_c001")
        assert reloaded.status == ExperimentStatus.CONCLUDED

    def test_winning_variant_id_opgeslagen(self):
        self._prepare_measuring("exp_lc_c002")
        self._conclude("exp_lc_c002", "var_chal")
        reloaded = ExperimentStore().load("exp_lc_c002")
        assert reloaded.winning_variant_id == "var_chal"

    def test_causal_confidence_opgeslagen(self):
        self._prepare_measuring("exp_lc_c003")
        self._conclude("exp_lc_c003", "var_ctrl")
        reloaded = ExperimentStore().load("exp_lc_c003")
        assert reloaded.causal_confidence is not None
        assert reloaded.causal_confidence > 0

    def test_get_concluded_dimensions_telt_correct(self):
        self._prepare_measuring("exp_lc_c004")
        self._conclude("exp_lc_c004", "var_ctrl")
        counts = ExperimentStore().get_concluded_dimensions("app_lc")
        assert counts.get("hook_type", 0) >= 1

    def test_get_winning_values_bevat_winnaar(self):
        self._prepare_measuring("exp_lc_c005")
        self._conclude("exp_lc_c005", "var_ctrl")  # ctrl = curiosity_gap
        winners = ExperimentStore().get_winning_values("app_lc", ExperimentDimension.HOOK_TYPE)
        assert "curiosity_gap" in winners

    def test_meerdere_concluded_tellen_mee(self):
        for i in range(3):
            exp_id = f"exp_lc_multi_{i:03d}"
            _make_experiment(exp_id, app_id="app_multi")
            _score_all_passing(exp_id, app_id="app_multi")
            ExperimentService().select_variant(exp_id, "var_ctrl", "operator")
            ExperimentService().mark_published(exp_id, "var_ctrl", f"post_{i}")
            self._conclude(exp_id, "var_ctrl")
        counts = ExperimentStore().get_concluded_dimensions("app_multi")
        assert counts.get("hook_type", 0) == 3

    def test_inconclusive_status_ook_afgerond(self):
        """INCONCLUSIVE is ook een eindstatus (net als CONCLUDED)."""
        self._prepare_measuring("exp_lc_inc001")
        store = ExperimentStore()
        exp = store.load("exp_lc_inc001")
        exp.status = ExperimentStatus.INCONCLUSIVE
        store.save(exp)
        reloaded = ExperimentStore().load("exp_lc_inc001")
        assert reloaded.status == ExperimentStatus.INCONCLUSIVE
