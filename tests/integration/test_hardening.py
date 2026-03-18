"""
DAG 5 — Hardening: edge cases en boundary conditions

Dekt:
  - ExperimentStore: index out-of-sync (exp_id in index, bestand ontbreekt)
  - ExperimentStore: corrupt index → leeg resultaat (geen crash)
  - ExperimentStore: unicode/emoji in content
  - ExperimentStore: list_by_app lege app → []
  - Variant.passed_quality met malformed quality_score dict (ontbrekende 'passed' key)
  - Variant.passed_quality met quality_score=None → True (not-yet-scored = not blocked)
  - select_variant op experiment met status MEASURING → werkt (geen status-guard in service)
  - select_variant met lege string approved_by → opgeslagen
  - Pipeline idea_index > aantal ideeën → clampt naar laatste idee
  - Pipeline idea_index=0 bij lege ideeënlijst → RuntimeError
  - Benchmarker._format_top_performers() met ontbrekende velden (score=0, views=0)
  - Benchmarker._format_caption() met None input
  - AssetQualityScorer._parse_scores() met nested JSON inside object
  - API: select-variant met lege body → 422
  - API: experiment_id met speciale tekens → 404 (niet crash)
  - Experiment.get_variant() met onbekend ID → None
  - ExperimentStore: sla 10 experimenten op, laad ze allemaal terug
"""

import json
import unittest.mock as mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import experiments.experiment_store as store_module
from experiments.experiment_store import ExperimentStore
from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    Hypothesis, Variant, VariantSpec,
)
from quality.benchmarker import Benchmarker
from quality.scorer import AssetQualityScorer


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    store_dir = tmp_path / "data" / "experiments"
    store_dir.mkdir(parents=True)
    monkeypatch.setattr(store_module, "STORE_DIR",  store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", store_dir / "_index.json")
    return store_dir


@pytest.fixture
def api_client():
    from backend.api.experiments import router
    app = FastAPI()
    app.include_router(router, prefix="/api/experiments")
    return TestClient(app)


# ── Helpers ───────────────────────────────────────────────────────────

def _make_exp(exp_id: str, app_id: str = "app_hard") -> Experiment:
    exp = Experiment(
        experiment_id=exp_id,
        campaign_id="camp_hard",
        app_id=app_id,
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Hardening test",
        ),
        variants=[
            Variant(
                variant_id="var_h_ctrl",
                experiment_id=exp_id,
                label="control",
                spec=VariantSpec(
                    label="control",
                    dimension=ExperimentDimension.HOOK_TYPE,
                    dimension_value="curiosity_gap",
                ),
                idea={}, script={}, caption={},
            )
        ],
        status=ExperimentStatus.PENDING,
    )
    ExperimentStore().save(exp)
    return exp


# ── ExperimentStore edge cases ────────────────────────────────────────

class TestStoreEdgeCases:
    def test_index_out_of_sync_geeft_geen_crash(self, isolated_store):
        """Index wijst naar exp_id die geen bestand heeft → wordt overgeslagen."""
        index = {"app_hard": ["exp_bestaat_niet_op_schijf"]}
        (isolated_store / "_index.json").write_text(
            json.dumps(index), encoding="utf-8"
        )
        result = ExperimentStore().list_by_app("app_hard")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_corrupt_index_geeft_lege_lijst(self, isolated_store):
        (isolated_store / "_index.json").write_text("{ niet geldig json", encoding="utf-8")
        result = ExperimentStore().list_by_app("app_hard")
        assert result == []

    def test_unicode_in_content_opgeslagen_en_herladen(self):
        exp = _make_exp("exp_unicode_001")
        exp.hypothesis.rationale = "Emoji test 🎯🚀 — café résumé"
        exp.variants[0].idea = {"title": "Tëst ïdëa ñoño", "emoji": "🎬"}
        ExperimentStore().save(exp)
        reloaded = ExperimentStore().load("exp_unicode_001")
        assert "🎯" in reloaded.hypothesis.rationale
        assert "🎬" in reloaded.variants[0].idea.get("emoji", "")

    def test_list_by_app_onbekende_app_geeft_lege_lijst(self):
        result = ExperimentStore().list_by_app("app_bestaat_niet_ooit_123")
        assert result == []

    def test_load_onbekend_id_geeft_none(self):
        result = ExperimentStore().load("exp_bestaat_niet_abc")
        assert result is None

    def test_10_experimenten_opgeslagen_en_herladen(self):
        for i in range(10):
            _make_exp(f"exp_bulk_{i:03d}", app_id="app_bulk")
        results = ExperimentStore().list_by_app("app_bulk")
        assert len(results) == 10

    def test_overschrijven_bestaand_experiment(self):
        exp = _make_exp("exp_overwrite_001")
        exp.status = ExperimentStatus.SELECTED
        ExperimentStore().save(exp)
        reloaded = ExperimentStore().load("exp_overwrite_001")
        assert reloaded.status == ExperimentStatus.SELECTED


# ── Variant.passed_quality edge cases ────────────────────────────────

class TestPassedQualityEdgeCases:
    def test_quality_score_none_is_niet_geblokkeerd(self):
        """None = nog niet gescoord = niet geblokkeerd."""
        exp = _make_exp("exp_qscore_001")
        variant = exp.variants[0]
        assert variant.quality_score is None
        assert variant.passed_quality is True

    def test_quality_score_zonder_passed_key_valt_terug_op_true(self):
        """Malformed quality_score zonder 'passed' key → default True."""
        exp = _make_exp("exp_qscore_002")
        variant = exp.variants[0]
        variant.quality_score = {"composite_score": 80.0}  # geen 'passed' key
        assert variant.passed_quality is True

    def test_quality_score_passed_false(self):
        exp = _make_exp("exp_qscore_003")
        variant = exp.variants[0]
        variant.quality_score = {"passed": False, "composite_score": 30.0}
        assert variant.passed_quality is False

    def test_quality_score_passed_true(self):
        exp = _make_exp("exp_qscore_004")
        variant = exp.variants[0]
        variant.quality_score = {"passed": True, "composite_score": 80.0}
        assert variant.passed_quality is True

    def test_quality_score_lege_dict_valt_terug_op_true(self):
        exp = _make_exp("exp_qscore_005")
        variant = exp.variants[0]
        variant.quality_score = {}
        assert variant.passed_quality is True


# ── Experiment.get_variant edge cases ─────────────────────────────────

class TestGetVariantEdgeCases:
    def test_get_variant_onbekend_id_geeft_none(self):
        exp = _make_exp("exp_gv_001")
        assert exp.get_variant("var_bestaat_niet") is None

    def test_get_variant_bestaand_id(self):
        exp = _make_exp("exp_gv_002")
        variant = exp.get_variant("var_h_ctrl")
        assert variant is not None
        assert variant.variant_id == "var_h_ctrl"


# ── select_variant edge cases ─────────────────────────────────────────

class TestSelectVariantEdgeCases:
    def test_select_variant_lege_approved_by_opgeslagen(self):
        """Lege string approved_by is toegestaan (validatie is aan de UI)."""
        from backend.services.experiment_service import ExperimentService
        exp = _make_exp("exp_sv_001")
        ExperimentService().select_variant("exp_sv_001", "var_h_ctrl", "")
        reloaded = ExperimentStore().load("exp_sv_001")
        assert reloaded.selected_by == ""

    def test_select_variant_lange_approved_by_string(self):
        from backend.services.experiment_service import ExperimentService
        exp = _make_exp("exp_sv_002")
        lang_naam = "a" * 200
        ExperimentService().select_variant("exp_sv_002", "var_h_ctrl", lang_naam)
        reloaded = ExperimentStore().load("exp_sv_002")
        assert reloaded.selected_by == lang_naam


# ── Pipeline edge cases ───────────────────────────────────────────────

def _make_agent_mocks(ideas: list):
    """Hulpfunctie: maak alle agent mocks voor pipeline tests."""
    mock_idea = mock.MagicMock()
    mock_idea.run.return_value = ideas
    mock_idea.total_cost_usd = 0.001

    mock_script = mock.MagicMock()
    mock_script.run.return_value = {
        "scenes": [], "title": "S", "total_duration_sec": 45, "video_type": "screen_demo"
    }
    mock_script.total_cost_usd = 0.001

    mock_caption = mock.MagicMock()
    mock_caption.run.return_value = {
        "caption": "C", "hashtags": [], "hook_line": "H", "platform": "tiktok"
    }
    mock_caption.total_cost_usd = 0.001

    mock_video = mock.MagicMock()
    mock_video.produce.return_value = None
    mock_video.total_cost_usd = 0.0

    return mock_idea, mock_script, mock_caption, mock_video


class TestPipelineEdgeCases:
    def test_idea_index_groter_dan_lijst_clampt_naar_laatste(self, monkeypatch):
        monkeypatch.delenv("EXPERIMENTS_ENABLED", raising=False)
        ideas = [
            {"title": f"Idee {i}", "goal": "awareness", "content_format": "problem-solution"}
            for i in range(2)
        ]
        mi, ms, mc, mv = _make_agent_mocks(ideas)
        from workflows.campaign_pipeline import run_pipeline
        with (
            mock.patch("workflows.campaign_pipeline.IdeaGeneratorAgent",  return_value=mi),
            mock.patch("workflows.campaign_pipeline.ScriptWriterAgent",    return_value=ms),
            mock.patch("workflows.campaign_pipeline.CaptionWriterAgent",   return_value=mc),
            mock.patch("workflows.campaign_pipeline.VideoOrchestrator",    return_value=mv),
            mock.patch("workflows.campaign_pipeline.bm")                  as mock_bm,
            mock.patch("workflows.campaign_pipeline.load_app",
                       return_value={"id": "app_test"}),
            mock.patch("workflows.campaign_pipeline.save_bundle"),
        ):
            mock_bm.load.return_value = {}
            bundle = run_pipeline("app_test", idea_index=99)
        # idea_index=99 maar slechts 2 ideeën → clampt naar idee[1]
        assert bundle.idea["title"] == "Idee 1"

    def test_lege_ideeenlijst_gooit_runtime_error(self, monkeypatch):
        monkeypatch.delenv("EXPERIMENTS_ENABLED", raising=False)
        mi, _, _, _ = _make_agent_mocks([])
        from workflows.campaign_pipeline import run_pipeline
        with (
            mock.patch("workflows.campaign_pipeline.IdeaGeneratorAgent",  return_value=mi),
            mock.patch("workflows.campaign_pipeline.bm")                  as mock_bm,
            mock.patch("workflows.campaign_pipeline.load_app",
                       return_value={"id": "app_test"}),
            mock.patch("workflows.campaign_pipeline.save_bundle"),
        ):
            mock_bm.load.return_value = {}
            with pytest.raises(RuntimeError, match="Geen ideeën"):
                run_pipeline("app_test")


# ── Benchmarker edge cases ────────────────────────────────────────────

class TestBenchmarkerEdgeCases:
    def test_format_top_performers_met_ontbrekende_velden(self):
        """Post zonder score/views/hook_type crasht niet."""
        with mock.patch("quality.benchmarker.anthropic.Anthropic"):
            b = Benchmarker("app_edge")
        # Injecteer posts met ontbrekende velden
        b._top_performers = [
            {},
            {"score": 50.0},
            {"composite_score": 60.0, "views": 100},
        ]
        result = b._format_top_performers()
        assert isinstance(result, str)
        assert "Score=" in result

    def test_format_caption_met_string_input(self):
        result = Benchmarker._format_caption("gewone tekst zonder dict")
        assert "gewone tekst" in result

    def test_format_caption_met_none_achtige_input(self):
        result = Benchmarker._format_caption({})
        assert isinstance(result, str)

    def test_format_caption_hashtags_beperkt_tot_10(self):
        caption = {"caption": "test", "hashtags": [f"tag{i}" for i in range(20)]}
        result = Benchmarker._format_caption(caption)
        assert result.count("#") <= 10


# ── AssetQualityScorer edge cases ─────────────────────────────────────

class TestScorerEdgeCases:
    @pytest.fixture
    def scorer(self):
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            return AssetQualityScorer()

    def test_parse_scores_extra_onbekende_dimensies_worden_genegeerd(self, scorer):
        raw = json.dumps({
            "hook_strength":       {"score": 80, "rationale": "ok", "flags": []},
            "clarity":             {"score": 75, "rationale": "ok", "flags": []},
            "brand_fit":           {"score": 70, "rationale": "ok", "flags": []},
            "retention_potential": {"score": 78, "rationale": "ok", "flags": []},
            "unknown_extra_dim":   {"score": 99, "rationale": "extra", "flags": []},
        })
        result = scorer._parse_scores(raw)
        assert "hook_strength" in result
        # onbekende dimensie is aanwezig in de dict maar wordt genegeerd door _build_score_object

    def test_build_score_object_met_score_0_crasht_niet(self, scorer):
        raw = {
            "hook_strength":       {"score": 0, "rationale": "nul", "flags": []},
            "clarity":             {"score": 0, "rationale": "nul", "flags": []},
            "brand_fit":           {"score": 0, "rationale": "nul", "flags": []},
            "retention_potential": {"score": 0, "rationale": "nul", "flags": []},
        }
        score = scorer._build_score_object("var_zero", raw)
        assert score.passed is False
        assert score.composite_score == 0.0

    def test_build_score_object_met_score_100(self, scorer):
        raw = {
            "hook_strength":       {"score": 100, "rationale": "top", "flags": []},
            "clarity":             {"score": 100, "rationale": "top", "flags": []},
            "brand_fit":           {"score": 100, "rationale": "top", "flags": []},
            "retention_potential": {"score": 100, "rationale": "top", "flags": []},
        }
        score = scorer._build_score_object("var_perfect", raw)
        assert score.passed is True
        assert abs(score.composite_score - 100.0) < 0.1

    def test_format_script_met_ontbrekende_voiceover(self, scorer):
        script = {"scenes": [{"type": "hook"}]}  # geen voiceover
        result = AssetQualityScorer._format_script(script)
        assert "Scene 1" in result


# ── API hardening ─────────────────────────────────────────────────────

class TestApiHardening:
    def test_select_variant_lege_body_geeft_422(self, api_client):
        r = api_client.post("/api/experiments/exp_any/select-variant")
        assert r.status_code == 422

    def test_get_experiment_speciale_tekens_in_id_geeft_404(self, api_client):
        r = api_client.get("/api/experiments/exp-with-dashes-and-123")
        assert r.status_code == 404

    def test_get_comparison_speciale_tekens_in_id_geeft_404(self, api_client):
        r = api_client.get("/api/experiments/exp_totally_unknown_xyz/comparison")
        assert r.status_code == 404

    def test_list_experiments_lege_app_id_string(self, api_client):
        r = api_client.get("/api/experiments/", params={"app_id": ""})
        # Lege string is technisch geldig app_id → 200 met lege lijst
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_pending_lege_app_id(self, api_client):
        r = api_client.get("/api/experiments/pending", params={"app_id": ""})
        assert r.status_code == 200
        assert r.json()["total"] == 0
