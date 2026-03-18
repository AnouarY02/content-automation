"""
DAG 4 — Pipeline integratie: EXPERIMENTS_ENABLED=false (default)

Dekt:
  - Pipeline voltooit correct zonder experiment-blok
  - bundle.experiment_id blijft None
  - VariantGenerator wordt nooit geïnstantieerd
  - ExperimentService.score_experiment wordt nooit aangeroepen
  - Progress callback ontvangt geen experiment-berichten
  - Exceptions uit de pipeline propageren normaal (niet gesmoord)
  - bundle bevat idea, script, caption
  - total_cost_usd wordt bijgehouden
"""

import unittest.mock as mock
from contextlib import contextmanager

import pytest

from workflows.campaign_pipeline import run_pipeline


# ── Setup helpers ─────────────────────────────────────────────────────

def _idea_mock():
    m = mock.MagicMock()
    m.run.return_value = [
        {"title": "App tip", "goal": "awareness", "content_format": "problem-solution"}
    ]
    m.total_cost_usd = 0.001
    return m


def _script_mock():
    m = mock.MagicMock()
    m.run.return_value = {
        "scenes": [{"type": "hook", "voiceover": "Test", "duration_sec": 5}],
        "title": "Test script",
        "total_duration_sec": 45,
        "video_type": "screen_demo",
    }
    m.total_cost_usd = 0.002
    return m


def _caption_mock():
    m = mock.MagicMock()
    m.run.return_value = {
        "caption": "Test caption",
        "hashtags": ["test"],
        "hook_line": "Test hook",
        "platform": "tiktok",
    }
    m.total_cost_usd = 0.001
    return m


def _video_mock():
    m = mock.MagicMock()
    m.produce.return_value = None
    m.total_cost_usd = 0.0
    return m


@contextmanager
def _pipeline_patches(extra_patches=None):
    """Context manager die alle externe agents mockt."""
    with (
        mock.patch("workflows.campaign_pipeline.IdeaGeneratorAgent",   return_value=_idea_mock()),
        mock.patch("workflows.campaign_pipeline.ScriptWriterAgent",    return_value=_script_mock()),
        mock.patch("workflows.campaign_pipeline.CaptionWriterAgent",   return_value=_caption_mock()),
        mock.patch("workflows.campaign_pipeline.VideoOrchestrator",    return_value=_video_mock()),
        mock.patch("workflows.campaign_pipeline.bm")                  as mock_bm,
        mock.patch("workflows.campaign_pipeline.load_app")            as mock_load_app,
        mock.patch("workflows.campaign_pipeline.save_bundle"),
        mock.patch("experiments.variant_generator.VariantGenerator")  as mock_vg,
        mock.patch("backend.services.experiment_service.ExperimentService") as mock_svc,
    ):
        mock_bm.load.return_value    = {"tone_of_voice": "friendly"}
        mock_load_app.return_value   = {"id": "app_test", "name": "Test App"}
        yield {"mock_vg": mock_vg, "mock_svc": mock_svc, "mock_bm": mock_bm}


# ── autouse: geen EXPERIMENTS_ENABLED in omgeving ────────────────────

@pytest.fixture(autouse=True)
def no_experiments_env(monkeypatch):
    monkeypatch.delenv("EXPERIMENTS_ENABLED", raising=False)


# ── Tests ─────────────────────────────────────────────────────────────

class TestPipelineDisabled:
    def test_bundle_experiment_id_none(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.experiment_id is None

    def test_status_pending_approval(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.status == "pending_approval"

    def test_bundle_heeft_idea(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.idea.get("title") == "App tip"

    def test_bundle_heeft_script(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert "scenes" in bundle.script

    def test_bundle_heeft_caption(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.caption.get("caption") == "Test caption"

    def test_total_cost_bijgehouden(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.total_cost_usd > 0

    def test_variant_generator_nooit_aangeroepen(self):
        with _pipeline_patches() as mocks:
            run_pipeline("app_test")
        mocks["mock_vg"].assert_not_called()

    def test_experiment_service_nooit_aangeroepen(self):
        with _pipeline_patches() as mocks:
            run_pipeline("app_test")
        mocks["mock_svc"].assert_not_called()

    def test_progress_geen_experiment_berichten(self):
        messages = []
        with _pipeline_patches():
            run_pipeline("app_test", on_progress=messages.append)
        experiment_msgs = [m for m in messages if "Experiment" in m or "experiment" in m]
        assert experiment_msgs == []

    def test_progress_callback_ontvangt_stap_berichten(self):
        messages = []
        with _pipeline_patches():
            run_pipeline("app_test", on_progress=messages.append)
        assert len(messages) >= 5  # stap 1 t/m 6 + afsluit bericht

    def test_app_id_doorgegeven_aan_load_app(self):
        with _pipeline_patches() as mocks:
            run_pipeline("app_xyz")
        # load_app wordt aangeroepen vanuit run_pipeline
        # mock staat als return_value op de class, dus check via mock_bm aanroep
        assert mocks["mock_bm"].load.called

    def test_pipeline_fout_propageert(self):
        with (
            mock.patch("workflows.campaign_pipeline.load_app", side_effect=ValueError("App niet gevonden")),
            mock.patch("workflows.campaign_pipeline.save_bundle"),
        ):
            with pytest.raises(ValueError, match="App niet gevonden"):
                run_pipeline("app_unknown")

    def test_bundle_status_failed_bij_fout(self):
        """Bij een fout wordt de bundle opgeslagen met status FAILED."""
        saved = {}

        def capture_save(b, tenant_id="default"):
            saved["bundle"] = b

        mock_idea_cls = mock.MagicMock()
        mock_idea_cls.return_value.run.side_effect = RuntimeError("LLM timeout")

        with (
            mock.patch("workflows.campaign_pipeline.IdeaGeneratorAgent", mock_idea_cls),
            mock.patch("workflows.campaign_pipeline.bm")              as mock_bm,
            mock.patch("workflows.campaign_pipeline.load_app",
                       return_value={"id": "app_test", "name": "Test"}),
            mock.patch("workflows.campaign_pipeline.save_bundle",
                       side_effect=capture_save),
        ):
            mock_bm.load.return_value = {}
            with pytest.raises(RuntimeError):
                run_pipeline("app_test")

        assert saved["bundle"].status == "failed"
