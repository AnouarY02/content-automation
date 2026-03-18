"""
DAG 2 — Integration tests: quality/benchmarker.py

Geen echte LLM-aanroepen. Anthropic client wordt gemockt.

Dekt:
  - Lege posts.json → low-confidence fallback (confidence=0.1)
  - Geen posts.json bestand → low-confidence fallback
  - Minder dan MIN_TOP_PERFORMERS (3) → low-confidence fallback
  - Precies MIN_TOP_PERFORMERS → LLM wordt aangeroepen
  - API-fout → _fallback_result (confidence=0.0)
  - Valide LLM-response → BenchmarkResult geparsed
  - Corrupt LLM-response → _fallback_result
  - _format_top_performers() met score en views
  - _format_caption() met dict en hashtags
  - _load_top_performers() sorteert op score (hoogste eerst)
  - _load_top_performers() respecteert alternatief pad
"""

import json
import unittest.mock as mock
from pathlib import Path

import pytest

from quality.benchmarker import Benchmarker, MIN_TOP_PERFORMERS, ROOT
from quality.models import BenchmarkResult
import quality.benchmarker as benchmarker_module


# ── Fixture: tijdelijke analytics directory ───────────────────────────

@pytest.fixture(autouse=True)
def isolated_analytics(tmp_path, monkeypatch):
    """Patch ROOT zodat benchmarker zijn data uit tmp_path leest."""
    monkeypatch.setattr(benchmarker_module, "ROOT", tmp_path)
    return tmp_path


def _write_posts(tmp_path, app_id: str, posts: list):
    posts_dir = tmp_path / "data" / "analytics" / app_id
    posts_dir.mkdir(parents=True, exist_ok=True)
    (posts_dir / "posts.json").write_text(
        json.dumps(posts, ensure_ascii=False), encoding="utf-8"
    )


def _make_post(score: float, views: int = 1000) -> dict:
    return {
        "score": score,
        "views": views,
        "hook_type": "bold_claim",
        "hook_text": "Test hook tekst",
        "cta_type": "soft",
        "composite_score": score,
    }


def _make_benchmarker(app_id: str = "app_test") -> Benchmarker:
    with mock.patch("quality.benchmarker.anthropic.Anthropic"):
        return Benchmarker(app_id)


def _make_benchmarker_with_client(app_id: str, mock_client):
    with mock.patch("quality.benchmarker.anthropic.Anthropic", return_value=mock_client):
        return Benchmarker(app_id)


# ── Lege / ontbrekende data ───────────────────────────────────────────

class TestEmptyData:
    def test_no_posts_file_returns_low_confidence(self):
        b = _make_benchmarker("app_no_file")
        result = b.benchmark_variant("var_x", {}, {})
        assert isinstance(result, BenchmarkResult)
        assert result.prediction_confidence <= 0.1
        assert result.is_reliable is False

    def test_empty_posts_json_returns_low_confidence(self, isolated_analytics):
        _write_posts(isolated_analytics, "app_empty", [])
        b = _make_benchmarker("app_empty")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.prediction_confidence <= 0.1
        assert result.is_reliable is False

    def test_posts_json_not_a_list_returns_empty(self, isolated_analytics):
        posts_dir = isolated_analytics / "data" / "analytics" / "app_obj"
        posts_dir.mkdir(parents=True, exist_ok=True)
        (posts_dir / "posts.json").write_text('{"not": "a list"}', encoding="utf-8")
        b = _make_benchmarker("app_obj")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.is_reliable is False

    def test_corrupt_posts_json_returns_empty(self, isolated_analytics):
        posts_dir = isolated_analytics / "data" / "analytics" / "app_corrupt"
        posts_dir.mkdir(parents=True, exist_ok=True)
        (posts_dir / "posts.json").write_text("{ not valid json }", encoding="utf-8")
        b = _make_benchmarker("app_corrupt")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.is_reliable is False

    def test_one_post_returns_low_confidence(self, isolated_analytics):
        _write_posts(isolated_analytics, "app_one", [_make_post(80)])
        b = _make_benchmarker("app_one")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.prediction_confidence <= 0.1

    def test_two_posts_returns_low_confidence(self, isolated_analytics):
        _write_posts(isolated_analytics, "app_two", [_make_post(80), _make_post(70)])
        b = _make_benchmarker("app_two")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.prediction_confidence <= 0.1

    def test_low_confidence_result_has_caveat(self, isolated_analytics):
        _write_posts(isolated_analytics, "app_cav", [_make_post(80)])
        b = _make_benchmarker("app_cav")
        result = b.benchmark_variant("var_x", {}, {})
        assert len(result.confidence_caveats) >= 1

    def test_low_confidence_result_has_differentiating_factor(self, isolated_analytics):
        _write_posts(isolated_analytics, "app_diff", [])
        b = _make_benchmarker("app_diff")
        result = b.benchmark_variant("var_x", {}, {})
        assert len(result.differentiating_factors) >= 1

    def test_variant_id_preserved_in_low_confidence_result(self, isolated_analytics):
        b = _make_benchmarker("app_id_test")
        result = b.benchmark_variant("var_myid_001", {}, {})
        assert result.variant_id == "var_myid_001"


# ── MIN_TOP_PERFORMERS grens ──────────────────────────────────────────

class TestMinTopPerformersThreshold:
    def test_exactly_min_performers_calls_llm(self, isolated_analytics):
        posts = [_make_post(float(80 - i * 5)) for i in range(MIN_TOP_PERFORMERS)]
        _write_posts(isolated_analytics, "app_min", posts)

        mock_response = mock.MagicMock()
        mock_response.content = [mock.MagicMock(text='{"similarity_score": 0.7, "matching_patterns": ["A"], "differentiating_factors": [], "predicted_band": "top_25%", "confidence": 0.5, "confidence_caveats": []}')]
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = mock_response

        b = _make_benchmarker_with_client("app_min", mock_client)
        result = b.benchmark_variant("var_x", {}, {})
        mock_client.messages.create.assert_called_once()
        assert result.prediction_confidence == 0.5

    def test_below_min_performers_skips_llm(self, isolated_analytics):
        posts = [_make_post(80.0) for _ in range(MIN_TOP_PERFORMERS - 1)]
        _write_posts(isolated_analytics, "app_below", posts)

        mock_client = mock.MagicMock()
        b = _make_benchmarker_with_client("app_below", mock_client)
        b.benchmark_variant("var_x", {}, {})
        mock_client.messages.create.assert_not_called()


# ── LLM response parsing ──────────────────────────────────────────────

class TestLLMResponseParsing:
    def _setup(self, isolated_analytics, app_id: str, llm_response: str):
        posts = [_make_post(float(80 - i * 3)) for i in range(MIN_TOP_PERFORMERS + 2)]
        _write_posts(isolated_analytics, app_id, posts)

        mock_response = mock.MagicMock()
        mock_response.content = [mock.MagicMock(text=llm_response)]
        mock_client = mock.MagicMock()
        mock_client.messages.create.return_value = mock_response

        return _make_benchmarker_with_client(app_id, mock_client)

    def test_valid_json_response_parsed(self, isolated_analytics):
        raw = '{"similarity_score": 0.74, "matching_patterns": ["Directe opening"], "differentiating_factors": ["Geen social proof"], "predicted_band": "top_25%", "confidence": 0.52, "confidence_caveats": []}'
        b = self._setup(isolated_analytics, "app_valid", raw)
        result = b.benchmark_variant("var_x", {}, {})
        assert abs(result.similarity_to_top_performers - 0.74) < 0.01
        assert result.predicted_performance_band == "top_25%"
        assert abs(result.prediction_confidence - 0.52) < 0.01
        assert "Directe opening" in result.matching_patterns

    def test_json_in_markdown_fence_parsed(self, isolated_analytics):
        raw = '```json\n{"similarity_score": 0.65, "matching_patterns": [], "differentiating_factors": [], "predicted_band": "average", "confidence": 0.45, "confidence_caveats": []}\n```'
        b = self._setup(isolated_analytics, "app_fence", raw)
        result = b.benchmark_variant("var_x", {}, {})
        assert abs(result.similarity_to_top_performers - 0.65) < 0.01

    def test_corrupt_response_returns_fallback(self, isolated_analytics):
        b = self._setup(isolated_analytics, "app_corrupt", "dit is geen json")
        result = b.benchmark_variant("var_x", {}, {})
        assert result.prediction_confidence == 0.0
        assert result.is_reliable is False

    def test_api_exception_returns_fallback(self, isolated_analytics):
        posts = [_make_post(float(80 - i * 3)) for i in range(MIN_TOP_PERFORMERS + 2)]
        _write_posts(isolated_analytics, "app_exc", posts)

        mock_client = mock.MagicMock()
        mock_client.messages.create.side_effect = Exception("Connection timeout")

        b = _make_benchmarker_with_client("app_exc", mock_client)
        result = b.benchmark_variant("var_x", {}, {})
        assert isinstance(result, BenchmarkResult)
        assert result.prediction_confidence == 0.0
        assert result.is_reliable is False

    def test_fallback_result_has_differentiating_factor(self, isolated_analytics):
        posts = [_make_post(float(80 - i * 3)) for i in range(MIN_TOP_PERFORMERS + 2)]
        _write_posts(isolated_analytics, "app_fb_diff", posts)
        mock_client = mock.MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("timeout")
        b = _make_benchmarker_with_client("app_fb_diff", mock_client)
        result = b.benchmark_variant("var_x", {}, {})
        assert len(result.differentiating_factors) >= 1


# ── _load_top_performers sortering ────────────────────────────────────

class TestLoadTopPerformers:
    def test_sorted_by_score_descending(self, isolated_analytics):
        posts = [_make_post(40.0), _make_post(90.0), _make_post(60.0)]
        _write_posts(isolated_analytics, "app_sort", posts)
        b = _make_benchmarker("app_sort")
        assert b._top_performers[0]["score"] == 90.0
        assert b._top_performers[-1]["score"] == 40.0

    def test_sorted_by_composite_score_when_no_score_key(self, isolated_analytics):
        posts = [
            {"composite_score": 55.0, "views": 1000},
            {"composite_score": 88.0, "views": 2000},
        ]
        _write_posts(isolated_analytics, "app_composite", posts)
        b = _make_benchmarker("app_composite")
        assert b._top_performers[0]["composite_score"] == 88.0

    def test_max_10_top_performers_loaded(self, isolated_analytics):
        posts = [_make_post(float(i)) for i in range(20)]
        _write_posts(isolated_analytics, "app_max", posts)
        b = _make_benchmarker("app_max")
        assert len(b._top_performers) <= 10

    def test_alternative_path_used_when_primary_missing(self, isolated_analytics):
        """Als data/analytics/{app_id}/posts.json niet bestaat, probeer alternatief pad."""
        alt_dir = isolated_analytics / "data" / "analytics" / "learnings" / "app_alt"
        alt_dir.mkdir(parents=True, exist_ok=True)
        posts = [_make_post(float(75 - i * 5)) for i in range(MIN_TOP_PERFORMERS + 1)]
        (alt_dir / "posts.json").write_text(json.dumps(posts), encoding="utf-8")

        b = _make_benchmarker("app_alt")
        assert len(b._top_performers) >= MIN_TOP_PERFORMERS


# ── _format_caption ───────────────────────────────────────────────────

class TestFormatCaption:
    def test_empty_dict_returns_placeholder(self):
        result = Benchmarker._format_caption({})
        assert "Geen caption" in result

    def test_caption_text_included(self):
        result = Benchmarker._format_caption({"caption": "Test caption tekst", "hashtags": []})
        assert "Test caption tekst" in result

    def test_hashtags_included(self):
        result = Benchmarker._format_caption({"caption": "x", "hashtags": ["tiktok", "app"]})
        assert "#tiktok" in result
        assert "#app" in result

    def test_max_10_hashtags(self):
        tags = [f"tag{i}" for i in range(15)]
        result = Benchmarker._format_caption({"caption": "x", "hashtags": tags})
        assert result.count("#") <= 10

    def test_non_dict_returns_string(self):
        result = Benchmarker._format_caption("plain caption string")
        assert "plain caption string" in result
