"""
Viral Algorithm Checker Agent.
Beoordeelt scripts op viral potentieel op basis van TikTok algoritme-signalen.
Kan optioneel herschrijf-instructies geven die de ScriptWriter gebruikt.
"""

from loguru import logger

from agents.base_agent import BaseAgent


class ViralCheckerAgent(BaseAgent):
    task_name = "viral_check"

    # Drempels
    VIRAL_READY = 80
    STRONG = 65
    NEEDS_WORK = 50

    def run(
        self,
        script: dict,
        app: dict,
        memory: dict,
    ) -> dict:
        """
        Beoordeel een script op viral potentieel.

        Args:
            script: Het gegenereerde script (output van ScriptWriterAgent)
            app: App-configuratie dict
            memory: Brand memory dict

        Returns:
            Viral check result dict met scores, verdict en rewrite_instructions
        """
        template = self._load_prompt("quality/viral_checker.txt")

        # Bouw context
        scenes_text = ""
        hook_line = ""
        for scene in script.get("scenes", []):
            scene_type = scene.get("type", "?")
            vo = scene.get("voiceover", "")
            dur = scene.get("duration_sec", "?")
            scenes_text += f"[{scene_type}] ({dur}s): {vo}\n"
            if scene_type == "hook":
                hook_line = vo

        full_voiceover = script.get("full_voiceover_text", "")
        if not full_voiceover:
            full_voiceover = " ".join(
                s.get("voiceover", "") for s in script.get("scenes", [])
            )

        top_hooks = memory.get("top_performing_hooks", [])
        target_audience = (
            memory.get("target_audience_details", {}).get("primary", "")
            or app.get("target_audience", "")
        )

        brand_voice = memory.get("tone_of_voice", "direct en energiek")

        prompt = self._fill_template(
            template,
            {
                "script": scenes_text,
                "hook_line": hook_line,
                "full_voiceover": full_voiceover,
                "app_context": (
                    f"Naam: {app.get('name', '?')}\n"
                    f"USP: {app.get('usp', '?')}\n"
                    f"Niche: {memory.get('niche', app.get('niche', '?'))}"
                ),
                "brand_voice": brand_voice,
                "target_audience": target_audience,
                "top_hooks": "\n".join(f"- {h}" for h in top_hooks) if top_hooks else "(nog geen data)",
            },
        )

        system = self._build_system_prompt(
            "Je bent een TikTok viral content expert. "
            "Je beoordeelt scripts puur op algoritme-potentieel. "
            "Wees EERLIJK en STRENG — liever een goede score die klopt "
            "dan een hoge score die misleidt."
        )

        raw = self._call_api(system, prompt)
        result = self._parse_json_response(raw, default={})

        # Zorg dat composite_score correct berekend wordt (7 dimensies)
        scores = result.get("scores", {})
        result["composite_score"] = round(
            scores.get("scroll_stop_power", 50) * 0.20
            + scores.get("watch_through_rate", 50) * 0.20
            + scores.get("share_save_trigger", 50) * 0.15
            + scores.get("comment_bait", 50) * 0.10
            + scores.get("authenticity_score", 50) * 0.10
            + scores.get("loop_potential", 50) * 0.10
            + scores.get("taal_kwaliteit", 50) * 0.15,
            1,
        )

        # Zorg dat verdict klopt met score
        cs = result["composite_score"]
        if cs >= self.VIRAL_READY:
            result["verdict"] = "VIRAL_READY"
        elif cs >= self.STRONG:
            result["verdict"] = "STRONG"
        elif cs >= self.NEEDS_WORK:
            result["verdict"] = "NEEDS_WORK"
        else:
            result["verdict"] = "WEAK"

        logger.info(
            f"[ViralChecker] Score: {cs}/100 → {result['verdict']} | "
            f"kosten=${self.total_cost_usd:.4f}"
        )

        return result

    def should_rewrite(self, viral_result: dict) -> bool:
        """Check of het script herschreven moet worden (< 80 = niet viral ready)."""
        return viral_result.get("composite_score", 0) < self.VIRAL_READY
