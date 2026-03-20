"""
Analyst Agent

Analyseert performance-data van meerdere posts en genereert concrete leerpunten.
Gebruikt Claude Sonnet (hogere kwaliteit nodig voor nuanced patroon-analyse).

VERANTWOORDELIJKHEDEN:
1. Ruwe PostAnalysis data samenvatten voor de LLM
2. Pattern synthesis prompt uitvoeren
3. Output valideren + structureren
4. Teruggeven als gestructureerde LearningEntry objecten

VEILIGHEID:
- Agent suggereert alleen — schrijft NIET direct naar brand memory
- Alle suggesties gaan via feedback_injector.py die ze valideert
- Bij te weinig data: confidence=low, multiplier dempft
"""

import json
from datetime import datetime
from typing import Any

from loguru import logger

from agents.base_agent import BaseAgent
from agents import brand_memory as bm
from analytics.models import (
    LearningConfidence,
    LearningEntry,
    LearningStore,
    Platform,
    PostAnalysis,
)


class AnalystAgent(BaseAgent):
    task_name = "performance_analysis"

    # Minimum posts voor een betrouwbare analyse
    MIN_POSTS_FOR_ANALYSIS = 3
    MIN_POSTS_FOR_HIGH_CONFIDENCE = 10

    def run(
        self,
        app_id: str,
        post_analyses: list[PostAnalysis],
        existing_store: LearningStore | None = None,
    ) -> dict[str, Any]:
        """
        Analyseer performance van meerdere posts en genereer leerpunten.

        Args:
            app_id: App ID
            post_analyses: Lijst van PostAnalysis objecten
            existing_store: Bestaande learnings (voor context)

        Returns:
            Dict met 'learnings', 'brand_memory_updates', 'experiment_suggestions'
        """
        if len(post_analyses) < 1:
            logger.warning(f"[Analyst] Geen posts om te analyseren voor {app_id}")
            return {}

        if len(post_analyses) < self.MIN_POSTS_FOR_ANALYSIS:
            logger.info(
                f"[Analyst] Slechts {len(post_analyses)} posts — "
                f"voer analyse uit maar met lage confidence"
            )

        # Bouw prompt context
        app = self._load_app(app_id)
        memory = bm.load(app_id)
        posts_summary = self._summarize_posts(post_analyses)
        scores = [p.score.composite_score for p in post_analyses]
        best_idx = scores.index(max(scores))
        worst_idx = scores.index(min(scores))

        template = self._load_prompt("tasks/pattern_synthesis.txt")
        prompt = self._fill_template(
            template,
            {
                "app_context": (
                    f"Naam: {app.get('name', app_id)}\n"
                    f"Niche: {app.get('niche', '?')}\n"
                    f"Target audience: {app.get('target_audience', '?')}\n"
                    f"USP: {app.get('usp', '?')}"
                ),
                "post_count": len(post_analyses),
                "posts_summary": posts_summary,
                "brand_memory_summary": bm.format_for_prompt(memory),
                "avg_score": round(sum(scores) / len(scores), 1),
                "best_score": round(max(scores), 1),
                "worst_score": round(min(scores), 1),
                "best_post_id": post_analyses[best_idx].post_id,
                "worst_post_id": post_analyses[worst_idx].post_id,
                "above_avg_count": sum(1 for s in scores if s > sum(scores) / len(scores)),
                "app_id": app_id,
            },
        )

        system = self._build_system_prompt(
            "Je analyseert marketing performance data. Wees data-gedreven en specifiek. "
            "Maak GEEN claims die niet door de data worden onderbouwd."
        )

        raw = self._call_api(system, prompt)
        result = self._parse_json_response(raw)

        # Valideer en verrijk learnings
        validated_learnings = self._validate_and_enrich_learnings(
            result.get("learnings", []),
            app_id=app_id,
            post_analyses=post_analyses,
        )
        result["learnings"] = validated_learnings
        result["analysis_id"] = f"analysis_{app_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}"
        result["analyzed_at"] = datetime.utcnow().isoformat()

        logger.success(
            f"[Analyst] Analyse klaar: {len(validated_learnings)} leerpunten | "
            f"kosten=${self.total_cost_usd:.4f}"
        )
        return result

    def _summarize_posts(self, analyses: list[PostAnalysis]) -> str:
        """Zet PostAnalysis lijst om naar een leesbare tekst voor de prompt."""
        lines = []
        for i, a in enumerate(analyses, 1):
            score = a.score
            norm = a.normalized
            tags = a.tags
            lines.append(
                f"\nPost {i}: ID={a.post_id}"
                f"\n  Score: {score.composite_score:.1f}/100 "
                f"(retention={score.retention_score:.1f}, engagement={score.engagement_score:.1f}, "
                f"virality={score.virality_score:.1f})"
                f"\n  Views: {a.raw.views:,} | ER: {norm.engagement_rate:.1%} | "
                f"Completion: {norm.completion_rate:.1%} | Save rate: {norm.save_rate:.1%}"
                f"\n  Tags: hook_type={tags.hook_type} | format={tags.content_format} | "
                f"video_type={tags.video_type} | cta={tags.cta_type} | "
                f"caption={tags.caption_style} | tijd={tags.posting_hour}:00"
                f"\n  Confidence: {score.confidence_level}"
            )
            if a.raw.views < 200:
                lines.append(f"  ⚠️ Lage views ({a.raw.views}) — beperkte statistische waarde")

        return "\n".join(lines)

    def _validate_and_enrich_learnings(
        self,
        raw_learnings: list[dict],
        app_id: str,
        post_analyses: list[PostAnalysis],
    ) -> list[LearningEntry]:
        """
        Valideer LLM-output en converteer naar LearningEntry objecten.

        VEILIGHEIDSREGELS:
        1. Confidence wordt gedegradeerd als sample_size te klein is
        2. Learnings zonder evidence worden verwijderd
        3. Prompt_instruction mag niet langer zijn dan 200 tekens
        """
        validated = []
        for raw in raw_learnings:
            # Vereiste velden
            if not all(k in raw for k in ["category", "type", "finding", "action"]):
                logger.debug(f"[Analyst] Learning overgeslagen — ontbrekende velden: {raw}")
                continue

            evidence = raw.get("evidence", {})
            sample_size = evidence.get("sample_size", 1)

            # Confidence afdwingen op basis van sample size
            stated_confidence = raw.get("confidence", "low")
            actual_confidence = self._cap_confidence(stated_confidence, sample_size, len(post_analyses))

            entry = LearningEntry(
                app_id=app_id,
                platform=Platform.TIKTOK,
                category=raw["category"],
                type=raw["type"],
                finding=raw["finding"],
                evidence=evidence,
                action=raw["action"],
                prompt_instruction=raw.get("prompt_instruction", "")[:200],
                confidence=actual_confidence,
                derived_from_posts=raw.get("derived_from_posts", []),
                sample_size=sample_size,
            )
            validated.append(entry)

        return validated

    def _cap_confidence(
        self,
        stated: str,
        sample_size: int,
        total_posts: int,
    ) -> LearningConfidence:
        """
        Verlaag confidence als de data het niet ondersteunt.
        Voorkomt overfitting op kleine datasets.
        """
        if total_posts < 3 or sample_size < 2:
            return LearningConfidence.LOW
        if total_posts < 10 or sample_size < 5:
            return min_confidence(stated, LearningConfidence.MEDIUM)
        return LearningConfidence[stated.upper()] if stated.upper() in LearningConfidence.__members__ else LearningConfidence.LOW

    def _load_app(self, app_id: str) -> dict:
        from backend.repository.factory import get_app_repo

        app = get_app_repo(tenant_id="default").get_app(app_id)
        return app or {"id": app_id}


def min_confidence(stated: str, max_level: LearningConfidence) -> LearningConfidence:
    """Zorg dat confidence nooit hoger is dan max_level."""
    order = [LearningConfidence.LOW, LearningConfidence.MEDIUM, LearningConfidence.HIGH]
    stated_level = LearningConfidence[stated.upper()] if stated.upper() in LearningConfidence.__members__ else LearningConfidence.LOW
    stated_idx = order.index(stated_level)
    max_idx = order.index(max_level)
    return order[min(stated_idx, max_idx)]
