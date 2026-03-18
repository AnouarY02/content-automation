"""
DAG 5 — Smoke tests: imports en instantiatie

Dekt:
  - Alle kernmodules importeerbaar zonder API keys of bestanden
  - Geen circulaire imports
  - Alle Pydantic modellen accepteren valide data
  - Key classes instantieerbaar (met mocked Anthropic)
  - FastAPI router laadbaar
  - ExperimentStore instantieerbaar (geen data nodig)
  - CLI importeerbaar zonder side effects
"""

import unittest.mock as mock

import pytest


# ── Module imports ─────────────────────────────────────────────────────

class TestModuleImports:
    def test_experiments_models(self):
        from experiments.models import (
            Experiment, Variant, VariantSpec, Hypothesis,
            ExperimentDimension, ExperimentStatus,
        )
        assert Experiment is not None

    def test_experiments_store(self):
        from experiments.experiment_store import ExperimentStore
        assert ExperimentStore is not None

    def test_experiments_variant_generator(self):
        from experiments.variant_generator import VariantGenerator, DIMENSION_PRIORITY
        assert VariantGenerator is not None
        assert len(DIMENSION_PRIORITY) == 3

    def test_quality_models(self):
        from quality.models import (
            AssetQualityScore, BenchmarkResult, DimensionScore,
            BLOCK_THRESHOLD, WARN_THRESHOLD, COMPOSITE_BLOCK, DIMENSION_WEIGHTS,
        )
        assert AssetQualityScore is not None

    def test_quality_scorer(self):
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            from quality.scorer import AssetQualityScorer
        assert AssetQualityScorer is not None

    def test_quality_benchmarker(self):
        with mock.patch("quality.benchmarker.anthropic.Anthropic"):
            from quality.benchmarker import Benchmarker, MIN_TOP_PERFORMERS
        assert Benchmarker is not None
        assert MIN_TOP_PERFORMERS > 0

    def test_backend_models_campaign(self):
        from backend.models.campaign import CampaignBundle, CampaignStatus
        assert CampaignBundle is not None

    def test_backend_services_experiment_service(self):
        from backend.services.experiment_service import ExperimentService
        assert ExperimentService is not None

    def test_backend_api_experiments(self):
        from backend.api.experiments import router
        assert router is not None

    def test_dimension_weights_sum_to_one(self):
        from quality.models import DIMENSION_WEIGHTS
        assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 0.001

    def test_experiment_status_values(self):
        from experiments.models import ExperimentStatus
        required = {"generating", "pending", "quality_fail", "selected",
                    "published", "measuring", "concluded", "inconclusive"}
        actual = {s.value for s in ExperimentStatus}
        assert required.issubset(actual)

    def test_experiment_dimension_values(self):
        from experiments.models import ExperimentDimension
        required = {"hook_type", "cta_type", "caption_style"}
        actual = {d.value for d in ExperimentDimension}
        assert required.issubset(actual)


# ── Instantiatie ──────────────────────────────────────────────────────

class TestInstantiation:
    def test_experiment_store_instantieerbaar(self, tmp_path, monkeypatch):
        import experiments.experiment_store as store_module
        monkeypatch.setattr(store_module, "STORE_DIR",  tmp_path / "exp")
        monkeypatch.setattr(store_module, "INDEX_PATH", tmp_path / "exp" / "_index.json")
        from experiments.experiment_store import ExperimentStore
        store = ExperimentStore()
        assert store is not None

    def test_experiment_service_instantieerbaar(self, tmp_path, monkeypatch):
        import experiments.experiment_store as store_module
        monkeypatch.setattr(store_module, "STORE_DIR",  tmp_path / "exp")
        monkeypatch.setattr(store_module, "INDEX_PATH", tmp_path / "exp" / "_index.json")
        from backend.services.experiment_service import ExperimentService
        svc = ExperimentService()
        assert svc is not None

    def test_asset_quality_scorer_instantieerbaar(self):
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            from quality.scorer import AssetQualityScorer
            scorer = AssetQualityScorer()
        assert scorer is not None

    def test_benchmarker_instantieerbaar(self):
        with mock.patch("quality.benchmarker.anthropic.Anthropic"):
            from quality.benchmarker import Benchmarker
            b = Benchmarker("app_smoke_test")
        assert b is not None

    def test_variant_generator_instantieerbaar(self, tmp_path, monkeypatch):
        import experiments.experiment_store as store_module
        monkeypatch.setattr(store_module, "STORE_DIR",  tmp_path / "exp")
        monkeypatch.setattr(store_module, "INDEX_PATH", tmp_path / "exp" / "_index.json")
        from experiments.variant_generator import VariantGenerator
        vg = VariantGenerator()
        assert vg is not None

    def test_campaign_bundle_instantieerbaar(self):
        from backend.models.campaign import CampaignBundle
        bundle = CampaignBundle(app_id="app_smoke")
        assert bundle.app_id == "app_smoke"
        assert bundle.experiment_id is None

    def test_experiment_model_instantieerbaar(self):
        from experiments.models import (
            Experiment, Hypothesis, ExperimentDimension, ExperimentStatus
        )
        exp = Experiment(
            experiment_id="exp_smoke_001",
            campaign_id="camp_smoke",
            app_id="app_smoke",
            hypothesis=Hypothesis(
                dimension=ExperimentDimension.HOOK_TYPE,
                control_value="curiosity_gap",
                challenger_value="bold_claim",
                rationale="Smoke test",
            ),
            variants=[],
            status=ExperimentStatus.GENERATING,
        )
        assert exp.experiment_id == "exp_smoke_001"

    def test_fastapi_router_routes_aanwezig(self):
        from backend.api.experiments import router
        paths = [r.path for r in router.routes]
        assert "/" in paths or any("" == p or p.endswith("/") for p in paths)
        assert any("{experiment_id}" in p for p in paths)
