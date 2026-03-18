"""
Idea Generator Agent.
Genereert 5 campagne-ideeën op basis van app-context, brand memory en trending formats.
"""

import json
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import BaseAgent
from agents import brand_memory as bm

ROOT = Path(__file__).parent.parent


class IdeaGeneratorAgent(BaseAgent):
    task_name = "idea_generation"

    def run(
        self,
        app: dict,
        memory: dict,
        platform: str = "tiktok",
        recent_performance: dict | None = None,
        trending_formats: list | None = None,
    ) -> list[dict]:
        """
        Genereer 5 campagne-ideeën.

        Args:
            app: App-configuratie dict (uit app_registry.json)
            memory: Brand memory dict
            platform: Doelplatform
            recent_performance: Dict met recente performance-cijfers
            trending_formats: Lijst van trending content-formats

        Returns:
            Lijst van 5 ideeën als dicts
        """
        template = self._load_prompt("tasks/idea_generation.txt")

        app_context = (
            f"Naam: {app.get('name', '?')}\n"
            f"Beschrijving: {app.get('description', '?')}\n"
            f"Target audience: {app.get('target_audience', '?')}\n"
            f"USP: {app.get('usp', '?')}\n"
            f"Niche: {app.get('niche', '?')}"
        )

        perf_str = "Geen recente data beschikbaar."
        if recent_performance:
            perf_str = json.dumps(recent_performance, ensure_ascii=False, indent=2)

        trending_str = "Gebruik algemene best practices voor het platform."
        if trending_formats:
            trending_str = "\n".join(f"- {f}" for f in trending_formats)

        persona = memory.get("creator_persona", {})
        if persona:
            persona_context = (
                f"Naam: {persona.get('naam', '?')}, {persona.get('leeftijd', '?')} jaar\n"
                f"Achtergrond: {persona.get('achtergrond', '')}\n"
                f"Spreekstijl: {persona.get('spreekstijl', '')}\n"
                f"Content aanpak: {persona.get('content_aanpak', '')}"
            )
        else:
            persona_context = "Geen persona gedefinieerd — schrijf vanuit merkperspectief."

        prompt = self._fill_template(
            template,
            {
                "app_context": app_context,
                "persona_context": persona_context,
                "brand_memory": bm.format_for_prompt(memory),
                "recent_performance": perf_str,
                "trending_formats": trending_str,
                "platform": platform.upper(),
            },
        )

        system = self._build_system_prompt()
        raw = self._call_api(system, prompt)

        ideas = self._parse_json_response(raw, default=[])

        # Normaliseer naar een lijst van dicts — bescherming tegen malformed JSON varianten
        if isinstance(ideas, dict):
            # API gaf een wrapper dict terug (bijv. {"ideas": [...]})
            ideas = next((v for v in ideas.values() if isinstance(v, list)), [])
            logger.warning(f"[IdeaGenerator] API retourneerde dict i.p.v. list, geëxtraheerd: {len(ideas)} items")
        if not isinstance(ideas, list):
            logger.warning(f"[IdeaGenerator] Onverwacht type: {type(ideas).__name__}. Fallback naar lege lijst.")
            ideas = []
        # Filter naar alleen dicts (verwijder strings, None, geneste lijsten)
        ideas = [i for i in ideas if isinstance(i, dict)]

        logger.success(f"[IdeaGenerator] {len(ideas)} ideeën gegenereerd | kosten=${self.total_cost_usd:.4f}")
        return ideas
