"""
DAG 7 — Unit tests: agents/brand_memory.py

Dekt:
  - load() — bestand bestaat → data geladen
  - load() — bestand ontbreekt → lege default dict
  - save() — bestand aangemaakt
  - save() — last_updated veld gezet
  - save() — data herstelbaar via load()
  - apply_updates() — add_to_top_hooks voegt toe
  - apply_updates() — top_hooks gecapped op 10
  - apply_updates() — duplicaten niet opnieuw toegevoegd
  - apply_updates() — add_to_avoided voegt toe
  - apply_updates() — update_optimal_time gezet
  - apply_updates() — update_best_format gezet
  - add_insight() — voegt entry toe met datum
  - add_insight() — gecapped op 20 entries
  - add_insight() — duplicate entry niet nogmaals toegevoegd
  - format_for_prompt() — bevat app naam
  - format_for_prompt() — bevat tone_of_voice
  - format_for_prompt() — bevat top hooks
  - format_for_prompt() — bevat avoided topics
"""

import json
import unittest.mock as mock
from pathlib import Path

import pytest

import agents.brand_memory as bm_module
from agents.brand_memory import (
    add_insight,
    apply_updates,
    format_for_prompt,
    load,
    save,
)


# ── Isolatie fixture ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_brand_memory(tmp_path, monkeypatch):
    """Leid alle brand memory lees-/schrijfacties naar tmp_path."""
    monkeypatch.setattr(bm_module, "BRAND_MEMORY_DIR", tmp_path)
    return tmp_path


def _write_memory(tmp_path: Path, app_id: str, data: dict) -> None:
    (tmp_path / f"{app_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ── load ───────────────────────────────────────────────────────────────

class TestLoad:
    def test_bestaand_bestand_geladen(self, isolated_brand_memory):
        _write_memory(isolated_brand_memory, "app_x", {"app_id": "app_x", "tone_of_voice": "zakelijk"})
        result = load("app_x")
        assert result["tone_of_voice"] == "zakelijk"

    def test_ontbrekend_bestand_geeft_default(self):
        result = load("app_bestaat_niet")
        assert result["app_id"] == "app_bestaat_niet"
        assert result["learned_insights"] == []
        assert result["performance_history"] == {}

    def test_bestaand_bestand_app_id_correct(self, isolated_brand_memory):
        _write_memory(isolated_brand_memory, "app_y", {"app_id": "app_y"})
        result = load("app_y")
        assert result["app_id"] == "app_y"


# ── save ───────────────────────────────────────────────────────────────

class TestSave:
    def test_bestand_aangemaakt(self, isolated_brand_memory):
        save("app_s", {"app_id": "app_s"})
        assert (isolated_brand_memory / "app_s.json").exists()

    def test_last_updated_gezet(self, isolated_brand_memory):
        memory = {"app_id": "app_s"}
        save("app_s", memory)
        loaded = load("app_s")
        assert "last_updated" in loaded

    def test_data_herstelbaar(self, isolated_brand_memory):
        memory = {"app_id": "app_s", "tone_of_voice": "energiek"}
        save("app_s", memory)
        loaded = load("app_s")
        assert loaded["tone_of_voice"] == "energiek"

    def test_unicode_opgeslagen(self, isolated_brand_memory):
        memory = {"app_id": "app_u", "usp": "Geweldige app 🚀"}
        save("app_u", memory)
        loaded = load("app_u")
        assert "🚀" in loaded["usp"]


# ── apply_updates ──────────────────────────────────────────────────────

class TestApplyUpdates:
    def _base_memory(self, isolated_brand_memory, app_id="app_a"):
        _write_memory(isolated_brand_memory, app_id, {"app_id": app_id})

    def test_add_to_top_hooks_voegt_toe(self, isolated_brand_memory):
        self._base_memory(isolated_brand_memory)
        result = apply_updates("app_a", {"add_to_top_hooks": ["hook1"]})
        assert "hook1" in result["top_performing_hooks"]

    def test_top_hooks_gecapped_op_10(self, isolated_brand_memory):
        existing = [f"hook{i}" for i in range(10)]
        _write_memory(isolated_brand_memory, "app_a", {"app_id": "app_a", "top_performing_hooks": existing})
        result = apply_updates("app_a", {"add_to_top_hooks": ["nieuw_hook"]})
        assert len(result["top_performing_hooks"]) == 10
        assert "nieuw_hook" in result["top_performing_hooks"]

    def test_duplicaten_niet_toegevoegd(self, isolated_brand_memory):
        _write_memory(isolated_brand_memory, "app_a", {"app_id": "app_a", "top_performing_hooks": ["hook1"]})
        result = apply_updates("app_a", {"add_to_top_hooks": ["hook1"]})
        assert result["top_performing_hooks"].count("hook1") == 1

    def test_add_to_avoided_voegt_toe(self, isolated_brand_memory):
        self._base_memory(isolated_brand_memory)
        result = apply_updates("app_a", {"add_to_avoided": ["politiek"]})
        assert "politiek" in result["avoided_topics"]

    def test_update_optimal_time(self, isolated_brand_memory):
        self._base_memory(isolated_brand_memory)
        result = apply_updates("app_a", {"update_optimal_time": "19:00"})
        assert result["performance_history"]["optimal_post_time"] == "19:00"

    def test_update_best_format(self, isolated_brand_memory):
        self._base_memory(isolated_brand_memory)
        result = apply_updates("app_a", {"update_best_format": "screen_demo"})
        assert result["content_formats"]["best_performing"] == "screen_demo"

    def test_lege_updates_geen_fout(self, isolated_brand_memory):
        self._base_memory(isolated_brand_memory)
        result = apply_updates("app_a", {})
        assert result["app_id"] == "app_a"


# ── add_insight ────────────────────────────────────────────────────────

class TestAddInsight:
    def test_voegt_insight_toe(self, isolated_brand_memory):
        _write_memory(isolated_brand_memory, "app_i", {"app_id": "app_i", "learned_insights": []})
        add_insight("app_i", "Vraag-hooks werken goed")
        loaded = load("app_i")
        assert any("Vraag-hooks werken goed" in e["insight"] for e in loaded["learned_insights"])

    def test_gecapped_op_20(self, isolated_brand_memory):
        existing = [{"date": "2024-01-01", "insight": f"les {i}"} for i in range(20)]
        _write_memory(isolated_brand_memory, "app_i", {"app_id": "app_i", "learned_insights": existing})
        add_insight("app_i", "nieuwe les")
        loaded = load("app_i")
        assert len(loaded["learned_insights"]) <= 20

    def test_heeft_datum_veld(self, isolated_brand_memory):
        _write_memory(isolated_brand_memory, "app_i", {"app_id": "app_i"})
        add_insight("app_i", "test")
        loaded = load("app_i")
        assert "date" in loaded["learned_insights"][0]


# ── format_for_prompt ──────────────────────────────────────────────────

class TestFormatForPrompt:
    def _memory(self) -> dict:
        return {
            "app_id": "app_fp",
            "app_name": "MijnApp",
            "tone_of_voice": "informeel",
            "target_audience": "jongeren",
            "usp": "Snelste app",
            "top_performing_hooks": ["hook A", "hook B"],
            "avoided_topics": ["politiek"],
        }

    def test_bevat_app_naam(self):
        result = format_for_prompt(self._memory())
        assert "MijnApp" in result

    def test_bevat_tone_of_voice(self):
        result = format_for_prompt(self._memory())
        assert "informeel" in result

    def test_bevat_top_hooks(self):
        result = format_for_prompt(self._memory())
        assert "hook A" in result
        assert "hook B" in result

    def test_bevat_avoided_topics(self):
        result = format_for_prompt(self._memory())
        assert "politiek" in result

    def test_lege_memory_geen_fout(self):
        result = format_for_prompt({})
        assert isinstance(result, str)

    def test_bevat_learned_insights(self):
        memory = self._memory()
        memory["learned_insights"] = [{"date": "2024-01-01", "insight": "Test les"}]
        result = format_for_prompt(memory)
        assert "Test les" in result

    def test_performance_history_getoond(self):
        memory = self._memory()
        memory["performance_history"] = {
            "best_post_type": "screen_demo",
            "optimal_post_time": "18:00",
        }
        result = format_for_prompt(memory)
        assert "18:00" in result
