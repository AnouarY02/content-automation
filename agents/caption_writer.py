"""
Caption Writer Agent.
Schrijft platform-geoptimaliseerde captions en hashtag sets.
"""

import json
from loguru import logger

from agents.base_agent import BaseAgent
from analytics.feedback_injector import load_agent_context


def _build_persona_context(memory: dict) -> str:
    persona = memory.get("creator_persona", {})
    if not persona:
        return ""
    return (
        f"{persona.get('naam', '')}, {persona.get('leeftijd', '')} jaar. "
        f"{persona.get('spreekstijl', '')} "
        f"{persona.get('content_aanpak', '')}"
    )


class CaptionWriterAgent(BaseAgent):
    task_name = "caption_writing"

    def run(
        self,
        script: dict,
        app: dict,
        memory: dict,
        platform: str = "tiktok",
        post_goal: str = "awareness",
    ) -> dict:
        """
        Schrijf captions en hashtags voor een video.

        Returns:
            Dict met caption_options en hashtags
        """
        template = self._load_prompt("tasks/caption_writing.txt")

        scenes = script.get("scenes", [])
        full_vo = script.get("full_voiceover_text", "") or " ".join(
            s.get("voiceover", "") for s in scenes
        )
        script_summary = (
            f"Hook: {scenes[0].get('voiceover', '') if scenes else ''}\n"
            f"Kernboodschap: {script.get('title', '')}\n"
            f"Volledige voiceover: {full_vo[:800]}\n"
            f"CTA: {scenes[-1].get('voiceover', '') if scenes else ''}"
        )

        prompt = self._fill_template(
            template,
            {
                "script_summary": script_summary,
                "platform": platform.upper(),
                "brand_voice": memory.get("tone_of_voice", "direct en energiek"),
                "persona_context": _build_persona_context(memory),
                "target_audience": app.get("target_audience", "?"),
                "post_goal": post_goal,
            },
        )

        # Injecteer caption-optimalisatie patronen uit eerdere performance-data
        try:
            feedback_ctx = load_agent_context(app.get("id", ""), "caption_writer")
        except Exception:
            feedback_ctx = ""

        system = self._build_system_prompt(extra=feedback_ctx)
        raw = self._call_api(system, prompt)
        result = self._parse_json_response(raw, default={})

        logger.success(f"[CaptionWriter] Caption gegenereerd | kosten=${self.total_cost_usd:.4f}")
        return result

    def generate_with_cta_override(
        self,
        script: dict,
        app: dict,
        memory: dict,
        cta_type_override: str,
        platform: str = "tiktok",
    ) -> dict:
        """
        Genereert een caption met een specifiek CTA type.
        Gebruikt voor cta_type variant-generatie in experimenten.

        Returns:
            Caption dict met "experiment_cta_type" tag
        """
        cta_descriptions = {
            "soft":      "follow / like — laagste drempel, breed inzetbaar",
            "hard":      "koop / meld je aan / ga naar link — conversie-gericht",
            "social":    "deel / duet / stitch — virality-gericht",
            "retention": "sla op / reageer — engagement en algoritme-gericht",
        }
        cta_desc = cta_descriptions.get(cta_type_override, cta_type_override)

        template = self._load_prompt("tasks/caption_writing.txt")
        _scenes = script.get("scenes", [])
        _full_vo = script.get("full_voiceover_text", "") or " ".join(
            s.get("voiceover", "") for s in _scenes
        )
        script_summary = (
            f"Hook: {_scenes[0].get('voiceover', '') if _scenes else ''}\n"
            f"Kernboodschap: {script.get('title', '')}\n"
            f"Volledige voiceover: {_full_vo[:800]}\n"
            f"CTA: {_scenes[-1].get('voiceover', '') if _scenes else ''}"
        )

        variant_instruction = (
            f"\n\nVARIANT INSTRUCTIE (EXPERIMENT):\n"
            f"Gebruik uitsluitend CTA type '{cta_type_override}': {cta_desc}.\n"
            f"De CTA moet prominent aanwezig zijn in zowel de caption als de hashtag-keuze.\n"
            f"Alle andere aspecten van de caption blijven hetzelfde."
        )

        prompt = self._fill_template(
            template,
            {
                "script_summary":  script_summary,
                "platform":        platform.upper(),
                "brand_voice":     memory.get("tone_of_voice", "direct en energiek"),
                "persona_context": _build_persona_context(memory),
                "target_audience": app.get("target_audience", "?"),
                "post_goal":       "engagement",
            },
        ) + variant_instruction

        system = self._build_system_prompt()
        raw    = self._call_api(system, prompt)
        result = self._parse_json_response(raw, default={})
        result["experiment_cta_type"] = cta_type_override
        result["is_variant"] = True

        logger.success(f"[CaptionWriter] Variant caption (cta={cta_type_override}) | kosten=${self.total_cost_usd:.4f}")
        return result

    def generate_with_style_override(
        self,
        script: dict,
        app: dict,
        memory: dict,
        style_override: str,
        platform: str = "tiktok",
    ) -> dict:
        """
        Genereert een caption met een specifieke schrijfstijl.
        Gebruikt voor caption_style variant-generatie in experimenten.

        Returns:
            Caption dict met "experiment_caption_style" tag
        """
        style_descriptions = {
            "minimal":      "maximaal 1 zin + hashtags, geen extra uitleg",
            "storytelling": "korte narrative die de video aanvult en verdiept",
            "list":         "3 concrete bullets die de waarde benoemen",
            "question":     "eindigt met een specifieke vraag om comments te stimuleren",
            "hook_repeat":  "caption herhaalt of versterkt de hook van de video",
        }
        style_desc = style_descriptions.get(style_override, style_override)

        template = self._load_prompt("tasks/caption_writing.txt")
        _scenes = script.get("scenes", [])
        _full_vo = script.get("full_voiceover_text", "") or " ".join(
            s.get("voiceover", "") for s in _scenes
        )
        script_summary = (
            f"Hook: {_scenes[0].get('voiceover', '') if _scenes else ''}\n"
            f"Kernboodschap: {script.get('title', '')}\n"
            f"Volledige voiceover: {_full_vo[:800]}\n"
            f"CTA: {_scenes[-1].get('voiceover', '') if _scenes else ''}"
        )

        variant_instruction = (
            f"\n\nVARIANT INSTRUCTIE (EXPERIMENT):\n"
            f"Schrijf de caption uitsluitend in '{style_override}' stijl: {style_desc}.\n"
            f"Houd je strikt aan deze stijl — dit is de variabele die getest wordt."
        )

        prompt = self._fill_template(
            template,
            {
                "script_summary":  script_summary,
                "platform":        platform.upper(),
                "brand_voice":     memory.get("tone_of_voice", "direct en energiek"),
                "persona_context": _build_persona_context(memory),
                "target_audience": app.get("target_audience", "?"),
                "post_goal":       "awareness",
            },
        ) + variant_instruction

        system = self._build_system_prompt()
        raw    = self._call_api(system, prompt)
        result = self._parse_json_response(raw, default={})
        result["experiment_caption_style"] = style_override
        result["is_variant"] = True

        logger.success(f"[CaptionWriter] Variant caption (style={style_override}) | kosten=${self.total_cost_usd:.4f}")
        return result


class AnalystAgent(BaseAgent):
    """Analyseert performance en genereert brand memory updates."""
    task_name = "performance_analysis"

    def run(self, app_id: str, posts_data: list, memory: dict) -> dict:
        template = self._load_prompt("tasks/performance_analysis.txt")

        prompt = self._fill_template(
            template,
            {
                "posts_data": json.dumps(posts_data, ensure_ascii=False, indent=2),
                "brand_memory": str(memory),
                "app_id": app_id,
            },
        )

        system = self._build_system_prompt()
        raw = self._call_api(system, prompt)
        result = self._parse_json_response(raw)

        logger.success(f"[Analyst] Analyse klaar voor {app_id} | kosten=${self.total_cost_usd:.4f}")
        return result
