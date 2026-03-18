"""
Benchmarker — vergelijkt nieuwe assets met historische top-performers.

ANTI-CONFOUND REGELS:
  1. Conclusies alleen op patroonherkenning, NIET causaliteit
  2. Confidence verlaagd als < 3 top-performers beschikbaar zijn
  3. Resultaten zijn indicatief, niet prescriptief
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import anthropic
from loguru import logger

from quality.models import BenchmarkResult

ROOT = Path(__file__).parent.parent
PROMPTS_DIR = ROOT / "prompts" / "quality"
_BENCHMARK_MODEL = "claude-haiku-4-5-20251001"
MIN_TOP_PERFORMERS = 3


class Benchmarker:
    """
    Vergelijkt een nieuwe variant met de historische top-performers van een app.

    Gebruik:
        benchmarker = Benchmarker(app_id="myapp_001")
        result = benchmarker.benchmark_variant("var_chal_001", script, caption)
        print(result.summary())
    """

    def __init__(self, app_id: str):
        self._app_id = app_id
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self._top_performers = self._load_top_performers()

    def benchmark_variant(
        self,
        variant_id: str,
        script: dict,
        caption: dict,
    ) -> BenchmarkResult:
        """
        Vergelijk een variant met de top-performers.

        Args:
            variant_id: ID van de te benchmarken variant
            script:     Script dict van de variant
            caption:    Caption dict van de variant

        Returns:
            BenchmarkResult (met lage confidence als weinig data beschikbaar)
        """
        if len(self._top_performers) < MIN_TOP_PERFORMERS:
            logger.info(
                f"[Benchmarker] Onvoldoende top-performers ({len(self._top_performers)} < {MIN_TOP_PERFORMERS}) "
                f"voor {self._app_id} — benchmark overgeslagen"
            )
            return BenchmarkResult(
                variant_id=variant_id,
                similarity_to_top_performers=0.5,
                matching_patterns=[],
                differentiating_factors=["Onvoldoende historische data voor benchmark"],
                predicted_performance_band="unknown",
                prediction_confidence=0.1,
                confidence_caveats=[f"Slechts {len(self._top_performers)} top-performers beschikbaar (min. {MIN_TOP_PERFORMERS})"],
            )

        prompt = self._build_prompt(script, caption)

        try:
            response = self._client.messages.create(
                model=_BENCHMARK_MODEL,
                max_tokens=1024,
                system=(
                    "Je vergelijkt TikTok content met historische top-performers. "
                    "Focus op concrete patronen. Geen causaliteitsclaims. "
                    "Geef output altijd als valid JSON."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
        except Exception as e:
            logger.error(f"[Benchmarker] API call mislukt: {e}")
            return self._fallback_result(variant_id)

        result = self._parse_result(variant_id, raw)
        logger.info(f"[Benchmarker] {variant_id}: {result.summary()}")
        return result

    # ── Prompt builder ────────────────────────────────────────────────

    def _build_prompt(self, script: dict, caption: dict) -> str:
        template = self._load_template()
        return template.format(
            top_performers=self._format_top_performers(),
            new_script=self._format_script(script)[:2000],
            new_caption=self._format_caption(caption)[:500],
        )

    def _format_top_performers(self) -> str:
        lines = []
        for i, post in enumerate(self._top_performers[:5]):
            score  = post.get("score", post.get("composite_score", 0))
            views  = post.get("views", post.get("play_count", 0))
            hook   = post.get("hook_text", post.get("hook", post.get("hook_type", "?")))
            cta    = post.get("cta_type", "?")
            lines.append(
                f"#{i + 1}: Score={score:.1f}, Views={views:,}, "
                f"Hook_type='{post.get('hook_type', '?')}', "
                f"CTA='{cta}', "
                f"Hook-tekst: \"{str(hook)[:80]}\""
            )
        return "\n".join(lines)

    # ── Result parsing ────────────────────────────────────────────────

    def _parse_result(self, variant_id: str, raw: str) -> BenchmarkResult:
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                data = json.loads(cleaned[start:end + 1])
                return BenchmarkResult(
                    variant_id=variant_id,
                    similarity_to_top_performers=float(data.get("similarity_score", 0.5)),
                    matching_patterns=data.get("matching_patterns", []),
                    differentiating_factors=data.get("differentiating_factors", []),
                    predicted_performance_band=data.get("predicted_band", "unknown"),
                    prediction_confidence=float(data.get("confidence", 0.3)),
                    confidence_caveats=data.get("confidence_caveats", []),
                )
            except Exception as e:
                logger.warning(f"[Benchmarker] Parse fout: {e}")

        return self._fallback_result(variant_id)

    # ── Data laden ────────────────────────────────────────────────────

    def _load_top_performers(self) -> list[dict]:
        """Laad top-performers uit de analytics data van de app."""
        posts_path = ROOT / "data" / "analytics" / self._app_id / "posts.json"
        if not posts_path.exists():
            # Probeer ook het cumulative learning pad
            alt_path = ROOT / "data" / "analytics" / "learnings" / self._app_id / "posts.json"
            if not alt_path.exists():
                return []
            posts_path = alt_path

        try:
            posts = json.loads(posts_path.read_text(encoding="utf-8"))
            if not isinstance(posts, list):
                return []
            sorted_posts = sorted(
                posts,
                key=lambda p: p.get("score", p.get("composite_score", 0)),
                reverse=True,
            )
            return sorted_posts[:10]
        except Exception as e:
            logger.warning(f"[Benchmarker] Kon top-performers niet laden: {e}")
            return []

    # ── Hulpmethoden ──────────────────────────────────────────────────

    @staticmethod
    def _format_script(script: dict) -> str:
        if not script:
            return "Geen script."
        scenes = script.get("scenes", [])
        if not scenes:
            return json.dumps(script, ensure_ascii=False)[:1500]
        lines = []
        for i, scene in enumerate(scenes):
            lines.append(f"Scene {i + 1}: {scene.get('hook', scene.get('type', ''))}")
            if "voiceover" in scene:
                lines.append(f"  VO: {scene['voiceover']}")
        return "\n".join(lines)

    @staticmethod
    def _format_caption(caption: dict) -> str:
        if not caption:
            return "Geen caption."
        if isinstance(caption, dict):
            text = caption.get("caption", "")
            tags = " ".join(f"#{h}" for h in caption.get("hashtags", [])[:10])
            return f"{text}\n{tags}".strip()
        return str(caption)[:400]

    @staticmethod
    def _load_template() -> str:
        path = PROMPTS_DIR / "benchmark_scorer.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return (
            "Vergelijk het nieuwe asset met de top-performers.\n\n"
            "TOP-PERFORMERS:\n{top_performers}\n\n"
            "NIEUW ASSET:\nScript:\n{new_script}\n\nCaption:\n{new_caption}\n\n"
            'Output als JSON: {{"similarity_score": 0.5, "matching_patterns": [], '
            '"differentiating_factors": [], "predicted_band": "average", '
            '"confidence": 0.3, "confidence_caveats": []}}'
        )

    @staticmethod
    def _fallback_result(variant_id: str) -> BenchmarkResult:
        return BenchmarkResult(
            variant_id=variant_id,
            similarity_to_top_performers=0.5,
            matching_patterns=[],
            differentiating_factors=["Benchmark niet beschikbaar (API of parse fout)"],
            predicted_performance_band="unknown",
            prediction_confidence=0.0,
            confidence_caveats=["API of parse fout"],
        )
