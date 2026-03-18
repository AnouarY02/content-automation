"""
Asset Quality Scorer — pre-publish kwaliteitsbeoordeling via LLM.

Evalueert elke variant op 4 dimensies en geeft een PASS/WARN/BLOCK beslissing.

THRESHOLDS (zie quality/models.py):
  score < 40 op enige dimensie  → BLOCK
  score < 65 op enige dimensie  → WARN
  composite < 55                → BLOCK (ook als individuele dimensies passen)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import anthropic
from loguru import logger

from experiments.models import Variant
from quality.models import (
    BLOCK_THRESHOLD,
    COMPOSITE_BLOCK,
    DIMENSION_WEIGHTS,
    WARN_THRESHOLD,
    AssetQualityScore,
    DimensionScore,
)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "quality"
_SCORER_MODEL = "claude-haiku-4-5-20251001"


class AssetQualityScorer:
    """
    Scoort een content-variant op 4 kwaliteitsdimensies via de Anthropic API.

    Gebruik:
        scorer = AssetQualityScorer()
        score = scorer.score_variant(variant, brand_memory, top_performers)
        if not score.passed:
            print(score.blockers)
    """

    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def score_variant(
        self,
        variant: Variant,
        brand_memory: dict,
        top_performers: list[dict],
    ) -> AssetQualityScore:
        """
        Score één variant op alle 4 dimensies.

        Args:
            variant:        De te scoren Variant (met script en caption)
            brand_memory:   Brand memory dict (voor brand_fit context)
            top_performers: Lijst van top-performing posts (voor hook-voorbeelden)

        Returns:
            AssetQualityScore met passed=True/False en gedetailleerde scores
        """
        prompt = self._build_prompt(variant, brand_memory, top_performers)

        try:
            response = self._client.messages.create(
                model=_SCORER_MODEL,
                max_tokens=1024,
                system=(
                    "Je bent een TikTok content kwaliteitsbeoordelaar. "
                    "Geef output altijd als valid JSON."
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            logger.debug(f"[Scorer] Raw response voor {variant.variant_id}: {raw[:200]}")
        except Exception as e:
            logger.error(f"[Scorer] API call mislukt voor {variant.variant_id}: {e}")
            return self._fallback_score(variant.variant_id)

        scores_dict = self._parse_scores(raw)
        return self._build_score_object(variant.variant_id, scores_dict)

    # ── Prompt builder ────────────────────────────────────────────────

    def _build_prompt(
        self,
        variant: Variant,
        brand_memory: dict,
        top_performers: list[dict],
    ) -> str:
        template = self._load_template()

        script_text   = self._format_script(variant.script)
        caption_data  = variant.caption if isinstance(variant.caption, dict) else {}
        caption_text  = caption_data.get("caption", str(variant.caption))
        hashtags      = " ".join(f"#{h}" for h in caption_data.get("hashtags", [])[:10])
        brand_voice   = brand_memory.get("tone_of_voice", brand_memory.get("brand_voice", "Niet gespecificeerd"))
        hook_type     = variant.spec.dimension_value if variant.spec.dimension.value == "hook_type" else "onbekend"
        top_hooks     = [
            p.get("hook_text", p.get("hook", ""))
            for p in top_performers[:5]
            if p.get("hook_text") or p.get("hook")
        ]
        top_hooks_str = "\n".join(f"- {h}" for h in top_hooks) if top_hooks else "Geen voorbeelden beschikbaar."

        return template.format(
            script=script_text[:2500],
            caption=caption_text[:600],
            hashtags=hashtags,
            brand_voice=brand_voice[:300],
            top_hooks_examples=top_hooks_str,
            hook_type=hook_type,
        )

    # ── Score parsing ─────────────────────────────────────────────────

    def _parse_scores(self, raw: str) -> dict:
        """Extraheer JSON uit LLM response, ook als er markdown fences omheen staan."""
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]

        # Zoek { ... } blok
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("[Scorer] JSON parse mislukt — fallback scores")
        return self._fallback_raw_scores()

    def _build_score_object(self, variant_id: str, scores: dict) -> AssetQualityScore:
        dims: dict[str, DimensionScore] = {}
        for dim_name in ("hook_strength", "clarity", "brand_fit", "retention_potential"):
            raw = scores.get(dim_name, {})
            dims[dim_name] = DimensionScore(
                score=float(raw.get("score", 50)),
                rationale=str(raw.get("rationale", ""))[:150],
                flags=[str(f) for f in raw.get("flags", [])],
            )

        composite = sum(
            dims[d].score * DIMENSION_WEIGHTS[d]
            for d in DIMENSION_WEIGHTS
        )
        composite = round(composite, 1)

        blockers: list[str] = []
        warnings: list[str] = []

        for dim_name, score_obj in dims.items():
            if score_obj.score < BLOCK_THRESHOLD:
                blockers.append(
                    f"{dim_name}: {score_obj.score:.0f} < {BLOCK_THRESHOLD} — {score_obj.rationale[:60]}"
                )
            elif score_obj.score < WARN_THRESHOLD:
                warnings.append(
                    f"{dim_name}: {score_obj.score:.0f} — {score_obj.rationale[:60]}"
                )

        if composite < COMPOSITE_BLOCK and not blockers:
            blockers.append(
                f"Composite score {composite:.1f} < {COMPOSITE_BLOCK} (te laag ondanks individuele scores)"
            )

        passed = len(blockers) == 0
        score = AssetQualityScore(
            variant_id=variant_id,
            hook_strength=dims["hook_strength"],
            clarity=dims["clarity"],
            brand_fit=dims["brand_fit"],
            retention_potential=dims["retention_potential"],
            composite_score=composite,
            passed=passed,
            warnings=warnings,
            blockers=blockers,
        )
        logger.info(f"[Scorer] {variant_id}: {score.summary()}")
        return score

    # ── Hulpmethoden ──────────────────────────────────────────────────

    @staticmethod
    def _format_script(script: dict) -> str:
        if not script:
            return "Geen script beschikbaar."
        scenes = script.get("scenes", [])
        if not scenes:
            return json.dumps(script, ensure_ascii=False, indent=2)[:2000]
        lines = []
        for i, scene in enumerate(scenes):
            label = scene.get("hook", scene.get("type", f"scene_{i+1}"))
            lines.append(f"[Scene {i + 1}: {label}]")
            if "voiceover" in scene:
                lines.append(f"  Voiceover: {scene['voiceover']}")
            if "visual" in scene:
                lines.append(f"  Visueel:   {scene['visual']}")
            if "duration_sec" in scene:
                lines.append(f"  Duur:      {scene['duration_sec']}s")
        return "\n".join(lines)

    @staticmethod
    def _load_template() -> str:
        path = PROMPTS_DIR / "asset_scorer.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        # Inline fallback als prompt-bestand ontbreekt
        return (
            "Beoordeel dit TikTok script en caption op 4 dimensies.\n\n"
            "Script:\n{script}\n\nCaption:\n{caption}\n{hashtags}\n\n"
            "Merkstem: {brand_voice}\nHook type: {hook_type}\n"
            "Top hooks: {top_hooks_examples}\n\n"
            'Output als JSON: {{"hook_strength": {{"score": 0, "rationale": "", "flags": []}}, '
            '"clarity": {{"score": 0, "rationale": "", "flags": []}}, '
            '"brand_fit": {{"score": 0, "rationale": "", "flags": []}}, '
            '"retention_potential": {{"score": 0, "rationale": "", "flags": []}}}}'
        )

    @staticmethod
    def _fallback_raw_scores() -> dict:
        return {
            "hook_strength":       {"score": 50, "rationale": "Score niet bepaald (parse error)", "flags": ["parse_error"]},
            "clarity":             {"score": 50, "rationale": "Score niet bepaald (parse error)", "flags": ["parse_error"]},
            "brand_fit":           {"score": 50, "rationale": "Score niet bepaald (parse error)", "flags": ["parse_error"]},
            "retention_potential": {"score": 50, "rationale": "Score niet bepaald (parse error)", "flags": ["parse_error"]},
        }

    def _fallback_score(self, variant_id: str) -> AssetQualityScore:
        return self._build_score_object(variant_id, self._fallback_raw_scores())
