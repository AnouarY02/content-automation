"""
Idea Generator Agent.
Genereert 5 campagne-ideeën op basis van app-context, brand memory en trending formats.

v2: Diversiteits-engine — voorkomt dat elke run hetzelfde idee oplevert:
  - Injecteert recente campagne-titels als VERBODEN onderwerpen
  - Voegt datum + random invalshoek toe voor variatie
  - Forceert minstens 3 VERSCHILLENDE hook_types per batch
"""

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from agents.base_agent import BaseAgent
from agents import brand_memory as bm

ROOT = Path(__file__).parent.parent

# Invalshoeken die de LLM kan gebruiken — random gekozen per run
_ANGLE_POOL = [
    "Vertel het vanuit het perspectief van iemand die NET begonnen is",
    "Focus op een EMOTIONEEL moment, niet het product",
    "Gebruik een vergelijking met iets BUITEN de sector (sport, koken, reizen)",
    "Draai het om: wat gebeurt er als je NIKS doet?",
    "Maak het persoonlijk: een specifieke dag uit het leven van de doelgroep",
    "Focus op het team/collega's, niet het individu",
    "Gebruik humor of zelfspot als ingang",
    "Vertel het als een ontdekking/geheim dat niemand kent",
    "Begin vanuit een trending topic of actualiteit en link het naar het onderwerp",
    "Focus op de resultaten na 30/60/90 dagen",
    "Vertel het vanuit een scepticus die overtuigd wordt",
    "Gebruik een 'voor en na' van een specifiek moment in de dag",
    "Focus op wat de doelgroep MIST door het oude systeem",
    "Maak een toekomstvisie: hoe ziet werk er over 5 jaar uit?",
    "Vertel het als waarschuwing vanuit iemand die het fout deed",
]


class IdeaGeneratorAgent(BaseAgent):
    task_name = "idea_generation"

    def run(
        self,
        app: dict,
        memory: dict,
        platform: str = "tiktok",
        recent_performance: dict | None = None,
        trending_formats: list | None = None,
        recent_titles: list[str] | None = None,
    ) -> list[dict]:
        """
        Genereer 5 campagne-ideeën.

        Args:
            app: App-configuratie dict (uit app_registry.json)
            memory: Brand memory dict
            platform: Doelplatform
            recent_performance: Dict met recente performance-cijfers
            trending_formats: Lijst van trending content-formats
            recent_titles: Titels van recente campagnes (worden uitgesloten)

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

        # ── v2: Diversiteits-injectie ──
        # 1. Recente titels als verboden onderwerpen
        exclusion_str = ""
        if recent_titles:
            titles_list = "\n".join(f"  - {t}" for t in recent_titles[-10:])
            exclusion_str = (
                f"\n\n⛔ VERBODEN — DEZE IDEEËN ZIJN AL GEBRUIKT:\n"
                f"{titles_list}\n"
                f"Genereer COMPLEET ANDERE invalshoeken. Geen variaties op bovenstaande titels.\n"
                f"Gebruik ANDERE hook_types, ANDERE content_formats, ANDERE emoties.\n"
            )

        # 2. Random invalshoek + datum voor uniekheid
        chosen_angles = random.sample(_ANGLE_POOL, min(3, len(_ANGLE_POOL)))
        angle_str = (
            f"\n\n🎯 CREATIEVE RICHTING VOOR DEZE RUN ({datetime.now().strftime('%d %B %Y, %H:%M')}):\n"
            f"Gebruik minstens 2 van deze invalshoeken:\n"
            + "\n".join(f"  → {a}" for a in chosen_angles)
            + "\n\nDit is run #{random.randint(100, 999)} — maak het VERS en ANDERS dan alles hiervoor.\n"
        )

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

        # Voeg diversiteits-blokken toe aan het einde van de prompt
        prompt += exclusion_str + angle_str

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
