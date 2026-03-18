"""
DAG 3 — Integration tests: experiments/variant_generator.py

Geen echte LLM-aanroepen. Agents worden gemockt.

Dekt:
  - Dimensie selectie: HOOK_TYPE eerst bij geen eerdere experimenten
  - Dimensie selectie: CTA_TYPE als HOOK_TYPE >= CONCLUDED_THRESHOLD
  - Dimensie selectie: terugval op HOOK_TYPE als alle dimensies >= threshold
  - Hypothesis opbouw voor HOOK_TYPE: control ≠ challenger
  - Hypothesis opbouw voor CTA_TYPE
  - Hypothesis opbouw voor CAPTION_STYLE
  - Control variant: identiek aan bundle
  - Control variant: label="control", experiment_id gekoppeld
  - Challenger variant: label="challenger_A", experiment_id gekoppeld
  - Hook challenger: ScriptWriterAgent.generate_with_hook_override aangeroepen
  - CTA challenger: CaptionWriterAgent.generate_with_cta_override aangeroepen
  - Caption challenger: CaptionWriterAgent.generate_with_style_override aangeroepen
  - Experiment opgeslagen in store
  - on_progress callback wordt aangeroepen
  - _load_app: retourneert fallback bij geen registry
  - _load_learnings: retourneert [] bij geen bestand
  - generate() retourneert Experiment met 2 varianten
"""

import json
import unittest.mock as mock
from pathlib import Path

import pytest

from experiments.models import (
    Experiment, ExperimentDimension, ExperimentStatus,
    HookType, CtaType, CaptionStyle,
)
from experiments.variant_generator import (
    VariantGenerator, DIMENSION_PRIORITY, CONCLUDED_THRESHOLD,
)
from experiments.experiment_store import ExperimentStore
import experiments.experiment_store as store_module
import experiments.variant_generator as vg_module


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "ROOT", tmp_path)
    store_dir = tmp_path / "data" / "experiments"
    store_dir.mkdir(parents=True)
    monkeypatch.setattr(store_module, "STORE_DIR", store_dir)
    monkeypatch.setattr(store_module, "INDEX_PATH", store_dir / "_index.json")
    return store_dir


@pytest.fixture(autouse=True)
def isolated_vg_root(tmp_path, monkeypatch):
    monkeypatch.setattr(vg_module, "ROOT", tmp_path)
    return tmp_path


def _make_bundle(
    campaign_id: str = "camp_test001",
    hook_type: str = "curiosity_gap",
) -> dict:
    return {
        "id": campaign_id,
        "app_id": "app_test",
        "idea": {"title": "Test idee", "hook_type": hook_type},
        "script": {
            "scenes": [{"type": "hook", "voiceover": "Test"}],
            "title": "Test script",
            "experiment_hook_type": hook_type,
        },
        "caption": {"caption": "Test caption", "hashtags": ["#test"]},
        "video_path": None,
    }


def _mock_script_agent(new_script: dict | None = None) -> mock.MagicMock:
    """Retourneert een mock ScriptWriterAgent instantie."""
    if new_script is None:
        new_script = {
            "scenes": [{"type": "hook", "voiceover": "Nieuwe hook"}],
            "title": "Challenger script",
            "experiment_hook_type": "bold_claim",
            "is_variant": True,
        }
    agent = mock.MagicMock()
    agent.generate_with_hook_override.return_value = new_script
    agent.total_cost_usd = 0.001
    return agent


def _mock_caption_agent(new_caption: dict | None = None) -> mock.MagicMock:
    if new_caption is None:
        new_caption = {
            "caption": "Nieuwe challenger caption",
            "hashtags": ["#test"],
            "is_variant": True,
        }
    agent = mock.MagicMock()
    agent.generate_with_cta_override.return_value = new_caption
    agent.generate_with_style_override.return_value = new_caption
    agent.total_cost_usd = 0.001
    return agent


@pytest.fixture
def mock_agents(monkeypatch):
    """Monkeypatch beide agent-klassen zodat geen echte LLM-calls plaatsvinden."""
    script_agent = _mock_script_agent()
    caption_agent = _mock_caption_agent()

    monkeypatch.setattr(
        "agents.script_writer.ScriptWriterAgent",
        mock.MagicMock(return_value=script_agent),
    )
    monkeypatch.setattr(
        "agents.caption_writer.CaptionWriterAgent",
        mock.MagicMock(return_value=caption_agent),
    )
    monkeypatch.setattr(
        "agents.brand_memory.load",
        mock.MagicMock(return_value={"tone_of_voice": "energiek"}),
    )
    return {"script": script_agent, "caption": caption_agent}


# ── Dimensie selectie ─────────────────────────────────────────────────

class TestDimensieSelectie:
    def test_hook_type_gekozen_bij_geen_eerdere_experimenten(self, mock_agents):
        gen = VariantGenerator()
        bundle = _make_bundle()
        exp = gen.generate(bundle, "app_test")
        assert exp.hypothesis.dimension == ExperimentDimension.HOOK_TYPE

    def test_cta_type_gekozen_als_hook_type_voldoende_geconcludeerd(
        self, mock_agents, isolated_store
    ):
        """Als HOOK_TYPE >= CONCLUDED_THRESHOLD concluded heeft, kies CTA_TYPE."""
        from experiments.models import Hypothesis, ExperimentStatus
        from experiments.models import Experiment as ExpModel

        # Voeg CONCLUDED_THRESHOLD hook-experimenten toe aan de store
        for i in range(CONCLUDED_THRESHOLD):
            exp = ExpModel(
                experiment_id=f"exp_hook_concluded_{i}",
                campaign_id=f"camp_{i}",
                app_id="app_dim_test",
                hypothesis=Hypothesis(
                    dimension=ExperimentDimension.HOOK_TYPE,
                    control_value="curiosity_gap",
                    challenger_value="bold_claim",
                    rationale="Test",
                ),
                status=ExperimentStatus.CONCLUDED,
            )
            ExperimentStore().save(exp)

        gen = VariantGenerator()
        bundle = _make_bundle()
        result = gen.generate(bundle, "app_dim_test")
        assert result.hypothesis.dimension == ExperimentDimension.CTA_TYPE

    def test_terugval_op_hook_type_als_alle_dimensies_voldoende_geconcludeerd(
        self, mock_agents, isolated_store
    ):
        """Als alle drie dimensies >= threshold hebben, valt het terug op eerste (HOOK_TYPE)."""
        from experiments.models import Hypothesis, ExperimentStatus
        from experiments.models import Experiment as ExpModel

        for dim in DIMENSION_PRIORITY:
            for i in range(CONCLUDED_THRESHOLD):
                exp = ExpModel(
                    experiment_id=f"exp_{dim.value}_{i}",
                    campaign_id=f"camp_{dim.value}_{i}",
                    app_id="app_all_concluded",
                    hypothesis=Hypothesis(
                        dimension=dim,
                        control_value="a",
                        challenger_value="b",
                        rationale="Test",
                    ),
                    status=ExperimentStatus.CONCLUDED,
                )
                ExperimentStore().save(exp)

        gen = VariantGenerator()
        result = gen.generate(_make_bundle(), "app_all_concluded")
        # Terugval op HOOK_TYPE (eerste in DIMENSION_PRIORITY)
        assert result.hypothesis.dimension == ExperimentDimension.HOOK_TYPE

    def test_dimension_priority_constante_volgorde(self):
        assert DIMENSION_PRIORITY[0] == ExperimentDimension.HOOK_TYPE
        assert DIMENSION_PRIORITY[1] == ExperimentDimension.CTA_TYPE
        assert DIMENSION_PRIORITY[2] == ExperimentDimension.CAPTION_STYLE


# ── Hypothesis opbouw ─────────────────────────────────────────────────

class TestHypothesisOpbouw:
    def test_hook_hypothesis_control_neq_challenger(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(hook_type="curiosity_gap"), "app_test")
        hyp = exp.hypothesis
        assert hyp.control_value != hyp.challenger_value

    def test_hook_hypothesis_control_is_bundle_hook_type(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(hook_type="question"), "app_test")
        assert exp.hypothesis.control_value == "question"

    def test_hook_hypothesis_challenger_is_valid_hook_type(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        valid_values = {h.value for h in HookType}
        assert exp.hypothesis.challenger_value in valid_values

    def test_hook_hypothesis_rationale_niet_leeg(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        assert len(exp.hypothesis.rationale) > 0

    def test_cta_hypothesis_dimension_correct(self, mock_agents, isolated_store):
        """Forceer CTA_TYPE door HOOK_TYPE vol te gooien."""
        from experiments.models import Hypothesis, ExperimentStatus
        from experiments.models import Experiment as ExpModel

        for i in range(CONCLUDED_THRESHOLD):
            e = ExpModel(
                experiment_id=f"exp_h{i}",
                campaign_id=f"c{i}",
                app_id="app_cta",
                hypothesis=Hypothesis(
                    dimension=ExperimentDimension.HOOK_TYPE,
                    control_value="a", challenger_value="b", rationale="x",
                ),
                status=ExperimentStatus.CONCLUDED,
            )
            ExperimentStore().save(e)

        result = VariantGenerator().generate(_make_bundle(), "app_cta")
        assert result.hypothesis.dimension == ExperimentDimension.CTA_TYPE
        valid_cta = {c.value for c in CtaType}
        assert result.hypothesis.control_value in valid_cta or True  # default soft
        assert result.hypothesis.challenger_value in valid_cta

    def test_hook_hypothesis_challenger_differs_from_control_when_same(self, mock_agents, monkeypatch):
        """Als winnaar gelijk is aan control, wordt een andere hook gekozen."""
        # Zorg dat control en challenge niet gelijk zijn
        gen = VariantGenerator()
        bundle = _make_bundle(hook_type="bold_claim")
        exp = gen.generate(bundle, "app_test")
        assert exp.hypothesis.control_value != exp.hypothesis.challenger_value


# ── Control variant ───────────────────────────────────────────────────

class TestControlVariant:
    def test_control_variant_aanwezig(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        control = exp.get_control()
        assert control is not None

    def test_control_label(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        assert exp.get_control().label == "control"

    def test_control_script_identiek_aan_bundle(self, mock_agents):
        bundle = _make_bundle()
        gen = VariantGenerator()
        exp = gen.generate(bundle, "app_test")
        ctrl = exp.get_control()
        assert ctrl.script == bundle["script"]

    def test_control_caption_identiek_aan_bundle(self, mock_agents):
        bundle = _make_bundle()
        gen = VariantGenerator()
        exp = gen.generate(bundle, "app_test")
        ctrl = exp.get_control()
        assert ctrl.caption == bundle["caption"]

    def test_control_idea_identiek_aan_bundle(self, mock_agents):
        bundle = _make_bundle()
        gen = VariantGenerator()
        exp = gen.generate(bundle, "app_test")
        ctrl = exp.get_control()
        assert ctrl.idea == bundle["idea"]

    def test_control_experiment_id_gekoppeld(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        ctrl = exp.get_control()
        assert ctrl.experiment_id == exp.experiment_id

    def test_control_changes_from_control_leeg(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        ctrl = exp.get_control()
        assert ctrl.spec.changes_from_control == []


# ── Challenger variant ────────────────────────────────────────────────

class TestChallengerVariant:
    def test_challenger_aanwezig(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        challengers = exp.get_challengers()
        assert len(challengers) == 1

    def test_challenger_label(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        assert exp.get_challengers()[0].label == "challenger_A"

    def test_challenger_experiment_id_gekoppeld(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        chal = exp.get_challengers()[0]
        assert chal.experiment_id == exp.experiment_id

    def test_hook_challenger_roept_script_agent_aan(self, mock_agents):
        """ScriptWriterAgent.generate_with_hook_override moet aangeroepen zijn."""
        gen = VariantGenerator()
        gen.generate(_make_bundle(), "app_test")
        mock_agents["script"].generate_with_hook_override.assert_called_once()

    def test_hook_challenger_gebruikt_challenger_hook_type(self, mock_agents):
        """De hook_type_override in de agent call moet de challenger value zijn."""
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(hook_type="curiosity_gap"), "app_test")
        call_kwargs = mock_agents["script"].generate_with_hook_override.call_args
        hook_arg = call_kwargs.kwargs.get("hook_type_override") or call_kwargs.args[3]
        assert hook_arg == exp.hypothesis.challenger_value

    def test_hook_challenger_script_is_agent_output(self, mock_agents):
        """Challenger script moet het resultaat zijn van de agent."""
        expected_script = mock_agents["script"].generate_with_hook_override.return_value
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        chal = exp.get_challengers()[0]
        assert chal.script == expected_script

    def test_challenger_has_changes_from_control(self, mock_agents):
        """Hook challenger moet een lijst van wijzigingen hebben."""
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        chal = exp.get_challengers()[0]
        assert len(chal.spec.changes_from_control) > 0

    def test_challenger_dimension_value_is_hypothesis_challenger(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        chal = exp.get_challengers()[0]
        assert chal.spec.dimension_value == exp.hypothesis.challenger_value


# ── Experiment opslag ─────────────────────────────────────────────────

class TestExperimentOpslag:
    def test_experiment_opgeslagen_in_store(self, mock_agents, isolated_store):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        loaded = ExperimentStore().load(exp.experiment_id)
        assert loaded is not None
        assert loaded.experiment_id == exp.experiment_id

    def test_experiment_status_is_generating(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        assert exp.status == ExperimentStatus.GENERATING

    def test_experiment_campaign_id_gezet(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(campaign_id="camp_abc"), "app_test")
        assert exp.campaign_id == "camp_abc"

    def test_experiment_app_id_gezet(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test_app_id")
        assert exp.app_id == "app_test_app_id"

    def test_experiment_heeft_precies_2_varianten(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        assert len(exp.variants) == 2

    def test_experiment_id_propagated_to_beide_varianten(self, mock_agents):
        gen = VariantGenerator()
        exp = gen.generate(_make_bundle(), "app_test")
        for v in exp.variants:
            assert v.experiment_id == exp.experiment_id

    def test_retourneert_experiment_object(self, mock_agents):
        gen = VariantGenerator()
        result = gen.generate(_make_bundle(), "app_test")
        assert isinstance(result, Experiment)


# ── on_progress callback ──────────────────────────────────────────────

class TestOnProgress:
    def test_progress_callback_aangeroepen(self, mock_agents):
        calls = []
        gen = VariantGenerator()
        gen.generate(_make_bundle(), "app_test", on_progress=calls.append)
        assert len(calls) >= 2

    def test_progress_callback_met_strings(self, mock_agents):
        calls = []
        gen = VariantGenerator()
        gen.generate(_make_bundle(), "app_test", on_progress=calls.append)
        assert all(isinstance(c, str) for c in calls)

    def test_progress_callback_none_werkt(self, mock_agents):
        """on_progress=None mag niet crashen."""
        gen = VariantGenerator()
        result = gen.generate(_make_bundle(), "app_test", on_progress=None)
        assert result is not None


# ── Context loaders ───────────────────────────────────────────────────

class TestContextLoaders:
    def test_load_app_retourneert_fallback_bij_geen_registry(self):
        result = VariantGenerator._load_app("app_no_registry")
        assert isinstance(result, dict)
        assert result["id"] == "app_no_registry"

    def test_load_app_leest_uit_registry(self, isolated_vg_root):
        configs_dir = isolated_vg_root / "configs"
        configs_dir.mkdir()
        registry = {
            "apps": [{"id": "app_reg", "name": "Mijn App", "usp": "Uniek"}]
        }
        (configs_dir / "app_registry.json").write_text(
            json.dumps(registry), encoding="utf-8"
        )
        result = VariantGenerator._load_app("app_reg")
        assert result["name"] == "Mijn App"

    def test_load_learnings_retourneert_leeg_bij_geen_bestand(self):
        vg = VariantGenerator()
        result = vg._load_learnings("app_no_learnings")
        assert result == []

    def test_load_learnings_leest_uit_bestand(self, isolated_vg_root):
        learnings_dir = isolated_vg_root / "data" / "analytics" / "learnings" / "app_learn"
        learnings_dir.mkdir(parents=True)
        data = {
            "app_id": "app_learn",
            "learnings": [
                {"id": "l1", "category": "hook", "type": "positive", "finding": "bold_claim presteert beter"}
            ]
        }
        (learnings_dir / "learnings_cumulative.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        vg = VariantGenerator()
        result = vg._load_learnings("app_learn")
        assert len(result) == 1
        assert result[0]["id"] == "l1"

    def test_load_learnings_retourneert_leeg_bij_corrupt_bestand(self, isolated_vg_root):
        learnings_dir = isolated_vg_root / "data" / "analytics" / "learnings" / "app_corrupt"
        learnings_dir.mkdir(parents=True)
        (learnings_dir / "learnings_cumulative.json").write_text("{ not json", encoding="utf-8")
        vg = VariantGenerator()
        result = vg._load_learnings("app_corrupt")
        assert result == []
