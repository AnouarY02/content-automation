"""
DAG 7 — Unit tests: agents/base_agent.py

Dekt:
  - _fill_template() — vervangt placeholders
  - _fill_template() — onbekende placeholder blijft staan
  - _fill_template() — meerdere placeholders tegelijk
  - _fill_template() — waarden worden naar str geconverteerd
  - _parse_json_response() — puur JSON object
  - _parse_json_response() — JSON omgeven door tekst
  - _parse_json_response() — JSON array
  - _parse_json_response() — JSON omgeven door markdown code block
  - _parse_json_response() — ongeldige JSON → JSONDecodeError
  - _build_system_prompt() — zonder extra
  - _build_system_prompt() — met extra tekst
  - _estimate_cost() — haiku model
  - _estimate_cost() — sonnet model
  - _load_prompt() — bestaand bestand wordt geladen
  - _load_prompt() — ontbrekend bestand geeft lege string
"""

import json
import unittest.mock as mock
from pathlib import Path

import pytest

import agents.base_agent as base_module


# ── Concrete subklasse voor tests ──────────────────────────────────────

class ConcreteAgent(base_module.BaseAgent):
    task_name = "test_task"

    def run(self, *args, **kwargs):
        pass


@pytest.fixture()
def agent():
    """Geeft een ConcreteAgent terug met gemockte OpenAI client."""
    with mock.patch("agents.base_agent._build_openai_client") as mock_builder:
        mock_builder.return_value = mock.MagicMock()
        return ConcreteAgent()


# ── _fill_template ─────────────────────────────────────────────────────

class TestFillTemplate:
    def test_vervangt_placeholder(self, agent):
        result = agent._fill_template("Hallo {naam}!", {"naam": "wereld"})
        assert result == "Hallo wereld!"

    def test_onbekende_placeholder_blijft(self, agent):
        result = agent._fill_template("Hallo {onbekend}!", {"naam": "wereld"})
        assert "{onbekend}" in result

    def test_meerdere_placeholders(self, agent):
        result = agent._fill_template("{a} en {b}", {"a": "X", "b": "Y"})
        assert result == "X en Y"

    def test_int_waarde_wordt_str(self, agent):
        result = agent._fill_template("Getal: {n}", {"n": 42})
        assert result == "Getal: 42"

    def test_float_waarde_wordt_str(self, agent):
        result = agent._fill_template("Prijs: {p}", {"p": 3.14})
        assert result == "Prijs: 3.14"

    def test_lege_template(self, agent):
        result = agent._fill_template("", {"key": "val"})
        assert result == ""

    def test_geen_placeholders(self, agent):
        result = agent._fill_template("Geen vars hier.", {})
        assert result == "Geen vars hier."

    def test_zelfde_placeholder_tweemaal(self, agent):
        result = agent._fill_template("{x} + {x}", {"x": "5"})
        assert result == "5 + 5"


# ── _parse_json_response ───────────────────────────────────────────────

class TestParseJsonResponse:
    def test_puur_json_object(self, agent):
        raw = '{"key": "value"}'
        result = agent._parse_json_response(raw)
        assert result == {"key": "value"}

    def test_json_omgeven_door_tekst(self, agent):
        raw = 'Hier is mijn antwoord: {"score": 72} Dat was het.'
        result = agent._parse_json_response(raw)
        assert result == {"score": 72}

    def test_json_array(self, agent):
        raw = '[1, 2, 3]'
        result = agent._parse_json_response(raw)
        assert result == [1, 2, 3]

    def test_json_array_omgeven_door_tekst(self, agent):
        raw = 'Resultaat: [{"id": 1}, {"id": 2}] klaar.'
        result = agent._parse_json_response(raw)
        assert result == [{"id": 1}, {"id": 2}]

    def test_markdown_codeblock(self, agent):
        raw = '```json\n{"title": "test"}\n```'
        result = agent._parse_json_response(raw)
        assert result == {"title": "test"}

    def test_nested_json(self, agent):
        raw = '{"outer": {"inner": [1, 2]}}'
        result = agent._parse_json_response(raw)
        assert result["outer"]["inner"] == [1, 2]

    def test_ongeldige_json_gooit_fout(self, agent):
        raw = "dit is geen json"
        with pytest.raises(json.JSONDecodeError):
            agent._parse_json_response(raw)

    def test_whitespace_wordt_gestript(self, agent):
        raw = '   \n {"key": "val"} \n   '
        result = agent._parse_json_response(raw)
        assert result == {"key": "val"}


# ── _build_system_prompt ───────────────────────────────────────────────

class TestBuildSystemPrompt:
    def test_zonder_extra_retourneert_base(self, agent):
        agent._base_system_prompt = "BASE"
        result = agent._build_system_prompt()
        assert result == "BASE"

    def test_met_extra_voegt_toe(self, agent):
        agent._base_system_prompt = "BASE"
        result = agent._build_system_prompt("EXTRA")
        assert "BASE" in result
        assert "EXTRA" in result

    def test_met_extra_gescheiden_door_newlines(self, agent):
        agent._base_system_prompt = "BASE"
        result = agent._build_system_prompt("EXTRA")
        assert result == "BASE\n\nEXTRA"

    def test_lege_extra_retourneert_alleen_base(self, agent):
        agent._base_system_prompt = "BASE"
        result = agent._build_system_prompt("")
        assert result == "BASE"


# ── _estimate_cost ─────────────────────────────────────────────────────

class TestEstimateCost:
    def test_mini_model_gebruikt_mini_tarieven(self, agent):
        agent.provider = "openai"
        agent.model = "gpt-4o-mini"
        cost = agent._estimate_cost(1000, 1000)
        assert cost > 0.0

    def test_4o_model_gebruikt_4o_tarieven(self, agent):
        agent.provider = "openai"
        agent.model = "gpt-4o"
        cost = agent._estimate_cost(1000, 1000)
        assert cost > 0.0

    def test_4o_duurder_dan_mini(self, agent):
        agent.provider = "openai"
        agent.model = "gpt-4o-mini"
        mini_cost = agent._estimate_cost(1000, 1000)
        agent.model = "gpt-4o"
        full_cost = agent._estimate_cost(1000, 1000)
        assert full_cost > mini_cost

    def test_nul_tokens_nul_kosten(self, agent):
        agent.provider = "openai"
        agent.model = "gpt-4o-mini"
        cost = agent._estimate_cost(0, 0)
        assert cost == 0.0

    def test_kosten_schalen_met_tokens(self, agent):
        agent.provider = "openai"
        agent.model = "gpt-4o-mini"
        cost_1k = agent._estimate_cost(1000, 1000)
        cost_2k = agent._estimate_cost(2000, 2000)
        assert abs(cost_2k - 2 * cost_1k) < 0.0001

    def test_anthropic_fallback_haiku(self, agent):
        agent.provider = "anthropic"
        agent.model = "claude-haiku-4-5-20251001"
        cost = agent._estimate_cost(1000, 1000)
        assert cost > 0.0

    def test_anthropic_fallback_sonnet(self, agent):
        agent.provider = "anthropic"
        agent.model = "claude-sonnet-4-6"
        cost = agent._estimate_cost(1000, 1000)
        assert cost > 0.0


# ── _load_prompt ───────────────────────────────────────────────────────

class TestLoadPrompt:
    def test_bestaand_bestand_wordt_geladen(self, tmp_path, agent):
        """Vervangt PROMPTS_DIR tijdelijk om een eigen bestand te lezen."""
        prompt_file = tmp_path / "test_prompt.txt"
        prompt_file.write_text("Hallo prompt!", encoding="utf-8")

        original = base_module.PROMPTS_DIR
        try:
            base_module.PROMPTS_DIR = tmp_path
            result = agent._load_prompt("test_prompt.txt")
        finally:
            base_module.PROMPTS_DIR = original

        assert result == "Hallo prompt!"

    def test_ontbrekend_bestand_geeft_lege_string(self, agent):
        result = agent._load_prompt("bestaat_niet/nooit.txt")
        assert result == ""
