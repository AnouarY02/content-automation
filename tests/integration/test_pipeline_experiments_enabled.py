"""
DAG 4 — Pipeline integratie: EXPERIMENTS_ENABLED=true

Dekt:
  - bundle.experiment_id wordt ingevuld na experiment generatie
  - VariantGenerator.generate wordt exact één keer aangeroepen
  - ExperimentService.score_experiment wordt aangeroepen met het experiment_id
  - generate() ontvangt het campaign_bundle dict en app_id
  - Progress callback ontvangt "Experiment:" berichten
  - Fout in VariantGenerator smoort de pipeline NIET (graceful degradation)
  - Als experiment mislukt, experiment_id blijft None maar bundle slaagt
  - Fout in score_experiment smoort de pipeline NIET
"""

import unittest.mock as mock
from contextlib import contextmanager

import pytest

from experiments.models import Experiment, ExperimentStatus, ExperimentDimension, Hypothesis
from workflows.campaign_pipeline import run_pipeline


# ── Helpers ────────────────────────────────────────────────────────────

def _make_mock_experiment(exp_id: str = "exp_dag4_001") -> Experiment:
    """Minimaal Experiment object dat VariantGenerator retourneert."""
    return Experiment(
        experiment_id=exp_id,
        campaign_id="camp_test",
        app_id="app_test",
        hypothesis=Hypothesis(
            dimension=ExperimentDimension.HOOK_TYPE,
            control_value="curiosity_gap",
            challenger_value="bold_claim",
            rationale="Test",
        ),
        variants=[],
        status=ExperimentStatus.GENERATING,
    )


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
        "scenes": [], "title": "Script", "total_duration_sec": 45, "video_type": "screen_demo"
    }
    m.total_cost_usd = 0.002
    return m


def _caption_mock():
    m = mock.MagicMock()
    m.run.return_value = {
        "caption": "Caption", "hashtags": ["test"], "hook_line": "Hook", "platform": "tiktok"
    }
    m.total_cost_usd = 0.001
    return m


def _video_mock():
    m = mock.MagicMock()
    m.produce.return_value = None
    m.total_cost_usd = 0.0
    return m


@contextmanager
def _pipeline_patches(mock_experiment=None, vg_side_effect=None, svc_side_effect=None):
    """Pipeline patches + experiment mocks."""
    exp = mock_experiment or _make_mock_experiment()

    mock_vg_instance = mock.MagicMock()
    if vg_side_effect:
        mock_vg_instance.generate.side_effect = vg_side_effect
    else:
        mock_vg_instance.generate.return_value = exp

    mock_svc_instance = mock.MagicMock()
    if svc_side_effect:
        mock_svc_instance.score_experiment.side_effect = svc_side_effect

    with (
        mock.patch("workflows.campaign_pipeline.IdeaGeneratorAgent",  return_value=_idea_mock()),
        mock.patch("workflows.campaign_pipeline.ScriptWriterAgent",   return_value=_script_mock()),
        mock.patch("workflows.campaign_pipeline.CaptionWriterAgent",  return_value=_caption_mock()),
        mock.patch("workflows.campaign_pipeline.VideoOrchestrator",   return_value=_video_mock()),
        mock.patch("workflows.campaign_pipeline.bm")                 as mock_bm,
        mock.patch("workflows.campaign_pipeline.load_app")           as mock_load_app,
        mock.patch("workflows.campaign_pipeline.save_bundle"),
        mock.patch("experiments.variant_generator.VariantGenerator",
                   return_value=mock_vg_instance),
        mock.patch("backend.services.experiment_service.ExperimentService",
                   return_value=mock_svc_instance),
    ):
        mock_bm.load.return_value  = {"tone_of_voice": "friendly"}
        mock_load_app.return_value = {"id": "app_test", "name": "Test App"}
        yield {
            "experiment":       exp,
            "mock_vg_instance": mock_vg_instance,
            "mock_svc_instance": mock_svc_instance,
        }


# ── autouse: EXPERIMENTS_ENABLED=true ────────────────────────────────

@pytest.fixture(autouse=True)
def experiments_enabled(monkeypatch):
    monkeypatch.setenv("EXPERIMENTS_ENABLED", "true")


# ── Tests ─────────────────────────────────────────────────────────────

class TestPipelineEnabled:
    def test_bundle_experiment_id_ingevuld(self):
        with _pipeline_patches() as ctx:
            bundle = run_pipeline("app_test")
        assert bundle.experiment_id == ctx["experiment"].experiment_id

    def test_bundle_status_pending_approval(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.status == "pending_approval"

    def test_variant_generator_eenmalig_aangeroepen(self):
        with _pipeline_patches() as ctx:
            run_pipeline("app_test")
        ctx["mock_vg_instance"].generate.assert_called_once()

    def test_variant_generator_ontvangt_app_id(self):
        with _pipeline_patches() as ctx:
            run_pipeline("app_test")
        call_kwargs = ctx["mock_vg_instance"].generate.call_args
        assert call_kwargs.kwargs.get("app_id") == "app_test" or \
               (call_kwargs.args and "app_test" in str(call_kwargs))

    def test_variant_generator_ontvangt_bundle_als_dict(self):
        with _pipeline_patches() as ctx:
            run_pipeline("app_test")
        call_kwargs = ctx["mock_vg_instance"].generate.call_args
        # campaign_bundle argument moet een dict zijn
        bundle_arg = call_kwargs.kwargs.get("campaign_bundle") or \
                     (call_kwargs.args[0] if call_kwargs.args else None)
        assert isinstance(bundle_arg, dict)

    def test_score_experiment_aangeroepen(self):
        with _pipeline_patches() as ctx:
            run_pipeline("app_test")
        ctx["mock_svc_instance"].score_experiment.assert_called_once()

    def test_score_experiment_ontvangt_experiment_id(self):
        with _pipeline_patches() as ctx:
            run_pipeline("app_test")
        call_args = ctx["mock_svc_instance"].score_experiment.call_args
        exp_id_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("experiment_id")
        assert exp_id_arg == ctx["experiment"].experiment_id

    def test_progress_bevat_experiment_berichten(self):
        messages = []
        with _pipeline_patches():
            run_pipeline("app_test", on_progress=messages.append)
        experiment_msgs = [m for m in messages if "Experiment" in m or "experiment" in m]
        assert len(experiment_msgs) >= 1

    def test_vg_fout_smoort_pipeline_niet(self):
        """VariantGenerator Exception → pipeline slaagt nog steeds."""
        with _pipeline_patches(vg_side_effect=RuntimeError("Agent crash")):
            bundle = run_pipeline("app_test")
        assert bundle.status == "pending_approval"

    def test_experiment_id_none_bij_vg_fout(self):
        """Als VariantGenerator faalt, experiment_id blijft None."""
        with _pipeline_patches(vg_side_effect=RuntimeError("crash")):
            bundle = run_pipeline("app_test")
        assert bundle.experiment_id is None

    def test_score_experiment_fout_smoort_pipeline_niet(self):
        """ExperimentService.score_experiment Exception → pipeline slaagt nog steeds."""
        with _pipeline_patches(svc_side_effect=RuntimeError("Score crash")):
            bundle = run_pipeline("app_test")
        assert bundle.status == "pending_approval"

    def test_bundle_heeft_script(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.script != {}

    def test_bundle_heeft_caption(self):
        with _pipeline_patches():
            bundle = run_pipeline("app_test")
        assert bundle.caption != {}

    def test_variant_generator_ontvangt_progress_callback(self):
        """on_progress wordt doorgegeven aan generate()."""
        cb = mock.MagicMock()
        with _pipeline_patches() as ctx:
            run_pipeline("app_test", on_progress=cb)
        call_kwargs = ctx["mock_vg_instance"].generate.call_args
        on_progress_arg = call_kwargs.kwargs.get("on_progress") or \
                          (call_kwargs.args[2] if len(call_kwargs.args) > 2 else None)
        assert on_progress_arg is not None
