"""
Script Writer Agent.
Schrijft een gedetailleerd video-script op basis van een campagne-idee.
"""

from loguru import logger

from agents.base_agent import BaseAgent
from agents import brand_memory as bm
from analytics.feedback_injector import load_agent_context


class ScriptWriterAgent(BaseAgent):
    task_name = "script_writing"

    def run(
        self,
        idea: dict,
        app: dict,
        memory: dict,
        platform: str = "tiktok",
        target_duration_sec: int = 45,
        video_type: str = "screen_demo",
        extra_instruction: str = "",
    ) -> dict:
        """
        Schrijf een volledig video-script.

        Args:
            idea: Campagne-idee dict (output van IdeaGeneratorAgent)
            app: App-configuratie dict
            memory: Brand memory dict
            platform: Doelplatform
            target_duration_sec: Gewenste videoduur in seconden
            video_type: screen_demo | talking_head | text_on_screen | mixed
            extra_instruction: Optionele extra instructies (bijv. van viral checker rewrite)

        Returns:
            Script als dict met scenes
        """
        template = self._load_prompt("tasks/script_writing.txt")

        brand_voice = (
            f"Tone of voice: {memory.get('tone_of_voice', 'direct en energiek')}\n"
            f"Beste CTA: {memory.get('best_cta', 'Probeer gratis via link in bio')}\n"
            f"Visuele stijl: {memory.get('visual_style', {})}"
        )

        # Bouw creator persona context
        persona = memory.get("creator_persona", {})
        if persona:
            persona_parts = [
                f"Naam: {persona.get('naam', '?')}, {persona.get('leeftijd', '?')} jaar, {persona.get('woonplaats', '')}".rstrip(", "),
                f"Achtergrond: {persona.get('achtergrond', '')}",
                f"Persoonlijkheid: {persona.get('persoonlijkheid', '')}",
                f"Spreekstijl: {persona.get('spreekstijl', '')}",
                f"Content aanpak: {persona.get('content_aanpak', '')}",
                f"Signature stijl: {persona.get('signature_stijl', '')}",
                f"Vermijdt: {persona.get('vermijdt', '')}",
            ]
            verboden = persona.get("verboden_zinnen", [])
            if verboden:
                persona_parts.append(f"VERBODEN ZINNEN (nooit gebruiken in script): {', '.join(verboden)}")
            persona_context = "\n".join(p for p in persona_parts if p.strip().split(": ", 1)[-1])
        else:
            # Fallback: anonieme persona zodat het script altijd in eerste persoon klinkt
            niche = memory.get("niche", app.get("niche", ""))
            persona_context = (
                f"Je bent een gewone gebruiker van 24-28 jaar die {app.get('name', 'dit product')} "
                f"zelf gebruikt en er oprecht enthousiast over is — niet als marketeer, maar als iemand "
                f"die het per ongeluk ontdekte en het nu aanraadt aan vrienden.\n"
                f"Spreekstijl: casual, direct, met spreektaal-imperfecties."
                + (f" Niche: {niche}." if niche else "")
                + "\nVermijdt: reclametaal, opsommingen, perfecte zinnen."
            )

        # Bouw viral context uit idee als het er is
        viral_context = ""
        if idea.get("open_loop"):
            viral_context += f"\nOPEN LOOP HINT: {idea['open_loop']}"
        if idea.get("share_reason"):
            viral_context += f"\nSHARE TRIGGER HINT: {idea['share_reason']}"
        if idea.get("comment_trigger"):
            viral_context += f"\nCOMMENT TRIGGER HINT: {idea['comment_trigger']}"

        # Bouw domein-specifieke metriek-gids op basis van niche
        niche = app.get("niche", memory.get("niche", ""))
        market_data = memory.get("marktdata", {})
        domain_metrics_hint = ""
        if market_data:
            domain_metrics_hint = "\nCONCRETE STATISTIEKEN voor dit domein (gebruik minstens 1 in het script):\n"
            for key, val in list(market_data.items())[:5]:
                domain_metrics_hint += f"  - {key}: {val}\n"
            if niche and any(k in niche.lower() for k in ["health", "wellness", "glp", "weight", "lifestyle", "coach"]):
                domain_metrics_hint += (
                    "CTA SLOTZIN — gebruik ALTIJD een gezondheidsmetriek (kg, %, aantal pogingen), NOOIT 'uur per week':\n"
                    "  → 'Hoeveel keer heb jij het al geprobeerd? Type het getal ↓'\n"
                    "  → 'Ben jij ook die 50%? Ja of nee ↓'\n"
                    "  → 'Hoeveel kilo wil jij nog kwijt? Type het getal ↓'\n"
                )

        full_extra = ""
        if extra_instruction or viral_context or domain_metrics_hint:
            full_extra = "═══════════════════════════════════════════════════\n"
            full_extra += "EXTRA INSTRUCTIES — VOLG DEZE EXACT\n"
            full_extra += "═══════════════════════════════════════════════════\n"
            if extra_instruction:
                full_extra += extra_instruction + "\n"
            if viral_context:
                full_extra += viral_context + "\n"
            if domain_metrics_hint:
                full_extra += domain_metrics_hint + "\n"

        # Bouw psychografische context uit brand memory
        psycho = memory.get("psychografisch_taalgebruik", {})
        bewezen_hooks = memory.get("bewezen_hooks", [])
        high_resonance = memory.get("high_resonance_topics", [])

        app_context_parts = [
            f"Naam: {app.get('name', '?')}",
            f"USP: {app.get('usp', '?')}",
            f"Target audience: {app.get('target_audience', '?')}",
        ]
        if psycho.get("gebruik_deze_zinnen"):
            app_context_parts.append(
                "\nPSYCHOGRAFISCHE TAAL — GEBRUIK DEZE ZINNEN (klinken als de doelgroep):\n"
                + "\n".join(f"- \"{z}\"" for z in psycho["gebruik_deze_zinnen"])
            )
        if psycho.get("vermijd_altijd"):
            app_context_parts.append(
                "\nVERMIJD ALTIJD (klinkt niet als doelgroep):\n"
                + ", ".join(f"\"{z}\"" for z in psycho["vermijd_altijd"])
            )
        if bewezen_hooks:
            app_context_parts.append(
                "\nBEWEZEN HOOKS (getest op deze doelgroep — gebruik als inspiratie):\n"
                + "\n".join(f"- {h}" for h in bewezen_hooks[:6])
            )
        if high_resonance:
            app_context_parts.append(
                "\nHOGE RESONANTIE ONDERWERPEN:\n"
                + "\n".join(f"- {t}" for t in high_resonance)
            )

        prompt = self._fill_template(
            template,
            {
                "campaign_idea": str(idea),
                "app_context": "\n".join(app_context_parts),
                "brand_voice": brand_voice,
                "persona_context": persona_context,
                "platform": platform.upper(),
                "target_duration_sec": target_duration_sec,
                "video_type": video_type,
                "extra_instruction": full_extra,
            },
        )

        # Injecteer script-optimalisatie patronen uit eerdere performance-data
        try:
            feedback_ctx = load_agent_context(app.get("id", ""), "script_writer")
        except Exception:
            feedback_ctx = ""

        system = self._build_system_prompt(extra=feedback_ctx)
        raw = self._call_api(system, prompt)
        script = self._parse_json_response(raw, default={})

        # Veiligheidscheck: _parse_json_response kan een list retourneren als JSON malformed is
        # (bijv. parser valt terug op inner array). Gebruik dan een lege dict als fallback.
        if not isinstance(script, dict):
            logger.warning(f"[ScriptWriter] Onverwacht type van JSON parser: {type(script).__name__}. Fallback naar lege dict.")
            script = {}

        # De pipeline bepaalt het gewenste video-type. Laat het model dat niet
        # stil terugdraaien naar een andere route.
        script["video_type"] = video_type

        logger.success(
            f"[ScriptWriter] Script geschreven: '{script.get('title', '?')}' "
            f"({script.get('total_duration_sec', '?')}s) | kosten=${self.total_cost_usd:.4f}"
        )
        return script

    def generate_with_hook_override(
        self,
        idea: dict,
        app: dict,
        memory: dict,
        hook_type_override: str,
        platform: str = "tiktok",
        target_duration_sec: int = 45,
        video_type: str = "screen_demo",
        extra_instruction: str = "",
    ) -> dict:
        """
        Genereert een challenger script met een specifiek hook type.
        Gebruikt voor variant-generatie in experimenten (stap 9).

        Identiek aan run(), maar voegt een variant-addendum toe aan het prompt
        zodat de agent uitsluitend de hook overschrijft.

        Returns:
            Script dict met "experiment_hook_type" en "is_variant" tags
        """
        base_template    = self._load_prompt("tasks/script_writing.txt")
        variant_addendum = self._load_prompt("variant_generation/hook_variant.txt")

        brand_voice = (
            f"Tone of voice: {memory.get('tone_of_voice', 'direct en energiek')}\n"
            f"Beste CTA: {memory.get('best_cta', 'Probeer gratis via link in bio')}\n"
            f"Visuele stijl: {memory.get('visual_style', {})}"
        )

        persona = memory.get("creator_persona", {})
        if persona:
            _p2 = [
                f"Naam: {persona.get('naam', '?')}, {persona.get('leeftijd', '?')} jaar, {persona.get('woonplaats', '')}".rstrip(", "),
                f"Achtergrond: {persona.get('achtergrond', '')}",
                f"Persoonlijkheid: {persona.get('persoonlijkheid', '')}",
                f"Spreekstijl: {persona.get('spreekstijl', '')}",
                f"Content aanpak: {persona.get('content_aanpak', '')}",
                f"Signature stijl: {persona.get('signature_stijl', '')}",
                f"Vermijdt: {persona.get('vermijdt', '')}",
            ]
            _v2 = persona.get("verboden_zinnen", [])
            if _v2:
                _p2.append(f"VERBODEN ZINNEN: {', '.join(_v2)}")
            persona_context = "\n".join(p for p in _p2 if p.strip().split(": ", 1)[-1])
        else:
            niche = memory.get("niche", app.get("niche", ""))
            persona_context = (
                f"Je bent een gewone gebruiker van 24-28 jaar die {app.get('name', 'dit product')} "
                f"zelf gebruikt en er oprecht enthousiast over is — niet als marketeer, maar als iemand "
                f"die het per ongeluk ontdekte en het nu aanraadt aan vrienden.\n"
                f"Spreekstijl: casual, direct, met spreektaal-imperfecties."
                + (f" Niche: {niche}." if niche else "")
                + "\nVermijdt: reclametaal, opsommingen, perfecte zinnen."
            )

        # Psychografische context voor variant generatie
        _psycho = memory.get("psychografisch_taalgebruik", {})
        _bewezen_hooks = memory.get("bewezen_hooks", [])
        _high_resonance = memory.get("high_resonance_topics", [])
        _app_ctx_parts = [
            f"Naam: {app.get('name', '?')}",
            f"USP: {app.get('usp', '?')}",
            f"Target audience: {app.get('target_audience', '?')}",
        ]
        if _psycho.get("gebruik_deze_zinnen"):
            _app_ctx_parts.append(
                "\nPSYCHOGRAFISCHE TAAL:\n"
                + "\n".join(f"- \"{z}\"" for z in _psycho["gebruik_deze_zinnen"])
            )
        if _bewezen_hooks:
            _app_ctx_parts.append(
                "\nBEWEZEN HOOKS:\n"
                + "\n".join(f"- {h}" for h in _bewezen_hooks[:6])
            )
        if _high_resonance:
            _app_ctx_parts.append(
                "\nHOGE RESONANTIE ONDERWERPEN:\n"
                + "\n".join(f"- {t}" for t in _high_resonance)
            )

        base_prompt = self._fill_template(
            base_template,
            {
                "campaign_idea": str(idea),
                "app_context": "\n".join(_app_ctx_parts),
                "brand_voice": brand_voice,
                "persona_context": persona_context,
                "platform": platform.upper(),
                "target_duration_sec": target_duration_sec,
                "video_type": video_type,
                "extra_instruction": extra_instruction or "",
            },
        )

        addendum = self._fill_template(
            variant_addendum,
            {
                "challenger_hook_type": hook_type_override,
                "control_hook_type":    idea.get("hook_type", "onbekend"),
                "idea_title":           idea.get("title", ""),
                "brand_context":        brand_voice,
                "hypothesis_context":   extra_instruction or f"Test hook type: {hook_type_override}",
            },
        )

        system = self._build_system_prompt()
        raw    = self._call_api(system, f"{base_prompt}\n\n---\n\n{addendum}")
        script = self._parse_json_response(raw)

        if not isinstance(script, dict):
            logger.warning(
                f"[ScriptWriter] Variant JSON parser gaf {type(script).__name__}; "
                "fallback naar lege dict."
            )
            script = {}

        # Variant output moet dezelfde videoroute respecteren als de aangevraagde run.
        script["video_type"] = video_type
        script["experiment_hook_type"] = hook_type_override
        script["is_variant"] = True

        logger.success(
            f"[ScriptWriter] Variant script ({hook_type_override}): '{script.get('title', '?')}' "
            f"| kosten=${self.total_cost_usd:.4f}"
        )
        return script
