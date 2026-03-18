"""
DAG 2 — Unit tests: quality/scorer.py (parser en fallback)

Geen LLM-aanroepen. Alleen de interne verwerkingsmethoden.

Dekt:
  - _parse_scores(): valide JSON
  - _parse_scores(): JSON in markdown fences (```json ... ```)
  - _parse_scores(): JSON in plain fences (``` ... ```)
  - _parse_scores(): tekst met JSON ergens erin
  - _parse_scores(): volledig corrupt / geen JSON
  - _parse_scores(): lege string
  - _parse_scores(): JSON met ontbrekende dimensies → fallback waarden
  - _build_score_object(): composite formule correct
  - _build_score_object(): blockers bij dimensie < BLOCK_THRESHOLD
  - _build_score_object(): warnings bij dimensie in [BLOCK, WARN)
  - _build_score_object(): composite blocker als composite < COMPOSITE_BLOCK
  - _build_score_object(): passed=True bij alle dimensies >= 65
  - _fallback_score(): retourneert valid AssetQualityScore
  - _fallback_raw_scores(): alle 4 dimensies aanwezig, score=50
  - _format_script(): lege dict, geen scenes, met scenes
"""

import pytest

from quality.scorer import AssetQualityScorer
from quality.models import BLOCK_THRESHOLD, COMPOSITE_BLOCK, WARN_THRESHOLD, AssetQualityScore


@pytest.fixture
def scorer():
    """AssetQualityScorer zonder echte Anthropic client (geen API-key nodig voor parsers)."""
    import unittest.mock as mock
    with mock.patch("quality.scorer.anthropic.Anthropic"):
        return AssetQualityScorer()


# ── _parse_scores ─────────────────────────────────────────────────────

class TestParseScores:
    def test_valid_json_direct(self, scorer):
        raw = '{"hook_strength": {"score": 80, "rationale": "Goed", "flags": []}, "clarity": {"score": 75, "rationale": "OK", "flags": []}, "brand_fit": {"score": 70, "rationale": "Past", "flags": []}, "retention_potential": {"score": 78, "rationale": "Sterk", "flags": []}}'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 80
        assert result["clarity"]["score"] == 75

    def test_json_in_markdown_fence(self, scorer):
        raw = '```json\n{"hook_strength": {"score": 82, "rationale": "x", "flags": []}, "clarity": {"score": 77, "rationale": "x", "flags": []}, "brand_fit": {"score": 73, "rationale": "x", "flags": []}, "retention_potential": {"score": 79, "rationale": "x", "flags": []}}\n```'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 82

    def test_json_in_plain_fence(self, scorer):
        raw = '```\n{"hook_strength": {"score": 71, "rationale": "y", "flags": []}, "clarity": {"score": 68, "rationale": "y", "flags": []}, "brand_fit": {"score": 65, "rationale": "y", "flags": []}, "retention_potential": {"score": 66, "rationale": "y", "flags": []}}\n```'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 71

    def test_json_embedded_in_prose(self, scorer):
        raw = 'Hier is mijn beoordeling: {"hook_strength": {"score": 60, "rationale": "ok", "flags": []}, "clarity": {"score": 55, "rationale": "ok", "flags": []}, "brand_fit": {"score": 58, "rationale": "ok", "flags": []}, "retention_potential": {"score": 57, "rationale": "ok", "flags": []}} Dat was alles.'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 60

    def test_corrupt_json_returns_fallback(self, scorer):
        raw = "dit is geen json"
        result = scorer._parse_scores(raw)
        # Fallback retourneert score=50 voor alle dimensies
        assert result["hook_strength"]["score"] == 50
        assert "parse_error" in result["hook_strength"]["flags"]

    def test_empty_string_returns_fallback(self, scorer):
        result = scorer._parse_scores("")
        assert result["hook_strength"]["score"] == 50

    def test_truncated_json_returns_fallback(self, scorer):
        raw = '{"hook_strength": {"score": 80, "rationale": "truncated'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 50

    def test_missing_dimensions_use_default_50(self, scorer):
        """JSON met slechts 2 van de 4 dimensies → ontbrekende krijgen score 50."""
        raw = '{"hook_strength": {"score": 85, "rationale": "sterk", "flags": []}}'
        result = scorer._parse_scores(raw)
        assert result["hook_strength"]["score"] == 85
        # Overige zijn afwezig → _build_score_object gebruikt default 50
        score_obj = scorer._build_score_object("var_x", result)
        assert score_obj.clarity.score == 50.0

    def test_flags_preserved(self, scorer):
        raw = '{"hook_strength": {"score": 30, "rationale": "zwak", "flags": ["te_kort", "geen_vraag"]}, "clarity": {"score": 70, "rationale": "ok", "flags": []}, "brand_fit": {"score": 70, "rationale": "ok", "flags": []}, "retention_potential": {"score": 70, "rationale": "ok", "flags": []}}'
        result = scorer._parse_scores(raw)
        assert "te_kort" in result["hook_strength"]["flags"]


# ── _build_score_object ───────────────────────────────────────────────

class TestBuildScoreObject:
    def _scores(self, hook=75, clarity=75, brand=75, retention=75):
        return {
            "hook_strength":       {"score": hook,      "rationale": "test", "flags": []},
            "clarity":             {"score": clarity,   "rationale": "test", "flags": []},
            "brand_fit":           {"score": brand,     "rationale": "test", "flags": []},
            "retention_potential": {"score": retention, "rationale": "test", "flags": []},
        }

    def test_composite_score_formula(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = self._scores(hook=80, clarity=70, brand=60, retention=90)
        score = scorer._build_score_object("var_x", raw)
        expected = round(80 * 0.35 + 70 * 0.25 + 60 * 0.20 + 90 * 0.20, 1)
        assert abs(score.composite_score - expected) < 0.05

    def test_passed_true_all_above_65(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        score = scorer._build_score_object("var_x", self._scores(75, 75, 75, 75))
        assert score.passed is True
        assert score.blockers == []

    def test_blocker_when_dimension_below_40(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = self._scores(hook=35, clarity=75, brand=75, retention=75)
        score = scorer._build_score_object("var_x", raw)
        assert score.passed is False
        assert len(score.blockers) >= 1
        assert "hook_strength" in score.blockers[0]

    def test_warning_when_dimension_in_warn_zone(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        # 50 is in [40, 65) → WARN
        raw = self._scores(hook=50, clarity=75, brand=75, retention=75)
        score = scorer._build_score_object("var_x", raw)
        assert len(score.warnings) >= 1
        assert "hook_strength" in score.warnings[0]

    def test_composite_blocker_when_composite_below_55(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        # Alle 40 → composite = 40 < 55 → composite blocker
        raw = self._scores(hook=40, clarity=40, brand=40, retention=40)
        score = scorer._build_score_object("var_x", raw)
        assert score.passed is False
        # Dimensies 40 zitten in WARN-zone (niet BLOCK), maar composite < 55
        assert any("Composite" in b or "composite" in b for b in score.blockers)

    def test_no_composite_blocker_when_already_blocked(self):
        """Als er al een dimensie-blocker is, mag composite-blocker niet dubbel toegevoegd worden."""
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = self._scores(hook=20, clarity=20, brand=20, retention=20)  # allemaal < 40
        score = scorer._build_score_object("var_x", raw)
        # Alleen dimensie-blockers, niet ook composite-blocker
        composite_blockers = [b for b in score.blockers if "Composite" in b or "composite" in b]
        dimension_blockers = [b for b in score.blockers if "Composite" not in b and "composite" not in b]
        assert len(dimension_blockers) >= 1
        assert len(composite_blockers) == 0  # composite blocker alleen als GEEN dim-blockers

    def test_variant_id_stored_in_result(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        score = scorer._build_score_object("var_custom_123", self._scores())
        assert score.variant_id == "var_custom_123"

    def test_returns_asset_quality_score_instance(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        result = scorer._build_score_object("var_x", self._scores())
        assert isinstance(result, AssetQualityScore)


# ── _fallback_score ───────────────────────────────────────────────────

class TestFallbackScore:
    def test_fallback_score_returns_asset_quality_score(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        result = scorer._fallback_score("var_fallback")
        assert isinstance(result, AssetQualityScore)
        assert result.variant_id == "var_fallback"

    def test_fallback_score_composite_is_50(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        result = scorer._fallback_score("var_fallback")
        # Alle dimensies 50 → composite = 50
        assert abs(result.composite_score - 50.0) < 0.1

    def test_fallback_score_passed_is_false(self):
        """Composite 50 < COMPOSITE_BLOCK 55 → passed moet False zijn."""
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        result = scorer._fallback_score("var_fallback")
        assert result.passed is False

    def test_fallback_raw_scores_all_dimensions_present(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = scorer._fallback_raw_scores()
        required = {"hook_strength", "clarity", "brand_fit", "retention_potential"}
        assert required == set(raw.keys())

    def test_fallback_raw_scores_score_is_50(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = scorer._fallback_raw_scores()
        for dim, val in raw.items():
            assert val["score"] == 50, f"{dim} score moet 50 zijn"

    def test_fallback_raw_scores_has_parse_error_flag(self):
        import unittest.mock as mock
        with mock.patch("quality.scorer.anthropic.Anthropic"):
            scorer = AssetQualityScorer()
        raw = scorer._fallback_raw_scores()
        for dim, val in raw.items():
            assert "parse_error" in val["flags"], f"{dim} moet parse_error flag hebben"


# ── _format_script ────────────────────────────────────────────────────

class TestFormatScript:
    def test_empty_dict_returns_placeholder(self):
        result = AssetQualityScorer._format_script({})
        assert "Geen script" in result

    def test_none_equivalent_empty(self):
        result = AssetQualityScorer._format_script({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_script_without_scenes_returns_json_dump(self):
        script = {"title": "Test video", "total_duration_sec": 45}
        result = AssetQualityScorer._format_script(script)
        assert "Test video" in result

    def test_script_with_scenes_formats_each(self):
        script = {
            "scenes": [
                {"type": "hook", "voiceover": "Wist je dat...", "duration_sec": 5},
                {"type": "solution", "voiceover": "Met onze app...", "duration_sec": 10},
            ]
        }
        result = AssetQualityScorer._format_script(script)
        assert "Scene 1" in result
        assert "Wist je dat" in result
        assert "Scene 2" in result

    def test_scene_uses_hook_key_as_label(self):
        script = {
            "scenes": [{"hook": "bold_claim", "voiceover": "Direct statement"}]
        }
        result = AssetQualityScorer._format_script(script)
        assert "bold_claim" in result

    def test_scene_falls_back_to_type_label(self):
        script = {
            "scenes": [{"type": "cta", "voiceover": "Probeer nu"}]
        }
        result = AssetQualityScorer._format_script(script)
        assert "cta" in result
