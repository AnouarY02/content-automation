"""
Market Research Agent — onderzoekt de niche VOOR idea generation.

Genereert een diep psychografisch profiel van de doelgroep:
- Dagelijkse frustraties in hun eigen taalgebruik
- Wat werkt en wat verzadigd is in de niche op het platform
- Psychologische haakjes (verliesaversie, identiteit, sociale proof)
- Concrete invalshoeken met voorbeeldhooks

Gebruik: Injecteer de output als 'market_research' in de IdeaGeneratorAgent.
"""

import json
from pathlib import Path

from loguru import logger

from agents.base_agent import BaseAgent

ROOT = Path(__file__).parent.parent


class MarketResearchAgent(BaseAgent):
    task_name = "market_research"

    def run(
        self,
        app: dict,
        platform: str = "tiktok",
        custom_brief: str | None = None,
    ) -> dict:
        """
        Doe marktonderzoek voor de gegeven app en platform.

        Args:
            app:           App-configuratie dict (uit app_registry.json)
            platform:      Doelplatform (tiktok | instagram | facebook | youtube)
            custom_brief:  Optionele extra context/opdracht

        Returns:
            Research rapport als dict (zie market_research.txt voor structuur)
        """
        template = self._load_prompt("tasks/market_research.txt")

        app_context = (
            f"Naam: {app.get('name', '?')}\n"
            f"Beschrijving: {app.get('description', '?')}\n"
            f"USP: {app.get('usp', '?')}\n"
            f"Niche: {app.get('niche', '?')}"
        )

        prompt = self._fill_template(
            template,
            {
                "app_context": app_context,
                "platform": platform.upper(),
                "niche": app.get("niche", "onbekend"),
                "target_audience": app.get("target_audience", "onbekend"),
                "custom_brief": custom_brief or "Geen extra brief opgegeven.",
            },
        )

        system = self._build_system_prompt()
        raw = self._call_api(system, prompt)

        research = self._parse_json_response(raw, default={})

        if not research:
            logger.warning("[MarketResearcher] Lege research output — gebruik fallback.")
            research = {"niche_summary": "Geen research beschikbaar.", "content_opportunities": []}

        logger.success(
            f"[MarketResearcher] Research voltooid | "
            f"kansen={len(research.get('content_opportunities', []))} | "
            f"kosten=${self.total_cost_usd:.4f}"
        )
        return research

    @staticmethod
    def format_for_idea_prompt(research: dict) -> str:
        """
        Formatteer research-output voor injectie in de idea_generation prompt.

        Geeft een beknopte, actie-gerichte samenvatting terug die als extra
        context kan worden toegevoegd aan de IdeaGeneratorAgent run-call.
        """
        if not research:
            return "Geen marktonderzoek beschikbaar."

        lines = ["═══ MARKTONDERZOEK — GEBRUIK DEZE INZICHTEN ═══\n"]

        summary = research.get("niche_summary", "")
        if summary:
            lines.append(f"NICHE SAMENVATTING: {summary}\n")

        audience = research.get("audience_psychography", {})
        if frustrations := audience.get("daily_frustrations", []):
            lines.append("DAGELIJKSE FRUSTRATIES (in hun eigen woorden):")
            for f in frustrations[:3]:
                lines.append(f"  • {f}")
            lines.append("")

        if exact_language := audience.get("exact_language", []):
            lines.append("EXACT TAALGEBRUIK VAN DE DOELGROEP:")
            for l in exact_language[:3]:
                lines.append(f'  • "{l}"')
            lines.append("")

        psych = research.get("psychological_hooks", {})
        if loss_frames := psych.get("loss_aversion_frames", []):
            lines.append("VERLIESAVERSIE-FRAMES (gebruik in hooks):")
            for frame in loss_frames[:2]:
                lines.append(f"  • {frame.get('what_they_lose', '')} → {frame.get('how_to_frame', '')}")
            lines.append("")

        if identity := psych.get("identity_frames", {}):
            lines.append("IDENTITEITSFRAMES:")
            lines.append(f"  • In-group: {identity.get('in_group', '')}")
            lines.append(f"  • Out-group: {identity.get('out_group', '')}")
            lines.append(f"  • Aspirationeel: {identity.get('aspirational_identity', '')}")
            lines.append("")

        landscape = research.get("content_landscape", {})
        if saturated := landscape.get("what_is_saturated", []):
            lines.append("VERMIJD (verzadigd, werkt niet meer):")
            for s in saturated[:2]:
                lines.append(f"  ❌ {s}")
            lines.append("")

        if white_spaces := landscape.get("white_spaces", []):
            lines.append("WITTE VLEKKEN (onbenutte kansen):")
            for w in white_spaces[:2]:
                lines.append(f"  ✓ {w}")
            lines.append("")

        if opportunities := research.get("content_opportunities", []):
            lines.append("TOP CONTENT KANSEN:")
            for opp in opportunities[:3]:
                lines.append(f"  → {opp.get('angle', '')}")
                lines.append(f"     Hook: {opp.get('hook_example', '')}")
                lines.append(f"     Psychologie: {opp.get('psychological_basis', '')}")
            lines.append("")

        if forbidden := research.get("forbidden_territory", []):
            lines.append("VERBODEN TERRITORIUM (vermijd dit absoluut):")
            for fb in forbidden[:2]:
                lines.append(f"  ⛔ {fb}")
            lines.append("")

        if top_insight := research.get("top_insight", ""):
            lines.append(f"TOP INZICHT: {top_insight}")

        lines.append("═══════════════════════════════════════════════════")

        return "\n".join(lines)
