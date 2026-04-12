"""
Realness Checker Agent.
Beoordeelt scripts op authenticiteit — detecteert "nep" content voordat het gepubliceerd wordt.

Waarom: AI-gegenereerde content klinkt vaak te gepolijst, te marketing-achtig, of te generiek.
Mensen herkennen dit in 2 seconden en scrollen door.

De RealNessChecker geeft een score 0-100 op authenticiteit + concrete herschrijf-instructies
voor zinnen die klinken als een AI of advertentie.
"""

import json
import re
from loguru import logger
from agents.base_agent import BaseAgent


# Patronen die direct op AI/marketing-jargon wijzen
FAKE_PATTERNS = [
    r"ontdek (jouw|je|uw)",
    r"transformeer (jouw|je|uw)",
    r"begin (vandaag|jouw|je) reis",
    r"het geheim van",
    r"revolutionair(e)?",
    r"bewezen methode",
    r"wetenschappelijk onderbouwd",
    r"in \d+ (simpele|eenvoudige|makkelijke) stappen",
    r"het nummer \d+ (geheim|probleem|fout)",
    r"wat (niemand|ze) je (vertelt|vertellen)",
    r"dit wil(len)? ze niet dat je weet",
    r"jouw (droom)?lichaam",
    r"neem de controle",
    r"de (ultieme|complete|perfecte) gids",
    r"clickbait-vrije zone",
    r"eerlijk gezegd",  # AI-cliché opener
    r"laten we eerlijk zijn",
    r"hier is (het|de) (waarheid|geheim)",
    r"\bpro tip\b",
    r"game.changer",
]

HUMAN_SIGNALS = [
    r"\d+ (kilo|kg|pond)",           # specifieke gewichtsgetallen
    r"mijn (man|vrouw|moeder|kind|coach)",  # echte relaties
    r"gisteren|vorige week|vorig jaar",      # tijdsreferenties
    r"ik (schaamde|huilde|lachte|schrok)",   # echte emoties
    r"na \d+ (jaar|maanden|weken)",           # specifieke tijdsduur
    r"mijn (dokter|huisarts|specialist)",     # echte context
    r"plateau",                               # specifiek GLP-probleem
    r"food noise",                            # echte community-term
    r"jojo.dieet",                            # herkenbare strijd
]


class RealnessCheckerAgent(BaseAgent):
    task_name = "realness_check"

    REALNESS_THRESHOLD = 72   # Minimum om door te gaan
    MAX_REWRITES = 2

    def run(self, script: dict, app: dict, memory: dict) -> dict:
        """
        Beoordeel een script op authenticiteit.

        Returns:
            Dict met: realness_score, verdict, fake_lines, rewrite_instructions
        """
        voiceover = script.get("full_voiceover_text", "")
        scenes = script.get("scenes", [])
        hook_line = next(
            (s.get("voiceover", "") for s in scenes if s.get("type") == "hook"),
            voiceover[:200]
        )

        # Snelle patroon-scan (gratis, geen API call)
        fake_hits = self._scan_fake_patterns(voiceover)
        human_hits = self._scan_human_signals(voiceover)

        # Bouw prompt
        template = self._load_prompt("quality/realness_checker.txt")
        prompt = self._fill_template(
            template,
            {
                "hook_line": hook_line,
                "full_voiceover": voiceover[:1500],
                "fake_patterns_found": "\n".join(f"- \"{h}\"" for h in fake_hits) or "(geen gevonden)",
                "human_signals_found": "\n".join(f"- \"{h}\"" for h in human_hits) or "(geen gevonden)",
                "app_context": app.get("description", ""),
                "target_audience": memory.get("target_audience_details", {}).get("primary", ""),
            },
        )

        system = (
            "Je bent een sociale media gebruiker die razendsnel doorheeft of content nep is. "
            "Je hebt een hekel aan marketing-taal en AI-gegenereerde teksten. "
            "Beoordeel STRENG — een echte gebruiker scrolt weg bij de eerste nep-zin."
        )

        raw = self._call_api(system, prompt)
        result = self._parse_json_response(raw, default={})

        # Valideer en bereken composite score
        pattern_penalty = min(len(fake_hits) * 8, 40)  # Max -40 voor fake patterns
        human_bonus = min(len(human_hits) * 5, 20)     # Max +20 voor menselijke signalen
        base_score = result.get("realness_score", 60)

        adjusted = max(0, min(100, base_score - pattern_penalty + human_bonus))
        result["realness_score"] = adjusted
        result["fake_patterns_found"] = fake_hits
        result["human_signals_found"] = human_hits

        # Verdict
        if adjusted >= 85:
            result["verdict"] = "AUTHENTIEK"
        elif adjusted >= self.REALNESS_THRESHOLD:
            result["verdict"] = "ACCEPTABEL"
        else:
            result["verdict"] = "TE_NEP"

        logger.info(
            f"[RealnessChecker] Score: {adjusted}/100 → {result['verdict']} "
            f"| fake={len(fake_hits)} hits | human={len(human_hits)} signals"
        )

        return result

    def _scan_fake_patterns(self, text: str) -> list[str]:
        """Zoek AI/marketing-clichés in de tekst."""
        found = []
        text_lower = text.lower()
        for pattern in FAKE_PATTERNS:
            matches = re.findall(pattern, text_lower)
            if matches:
                found.append(f"{pattern}: {matches[0]}")
        return found

    def _scan_human_signals(self, text: str) -> list[str]:
        """Zoek menselijke, specifieke elementen in de tekst."""
        found = []
        text_lower = text.lower()
        for pattern in HUMAN_SIGNALS:
            matches = re.findall(pattern, text_lower)
            if matches:
                found.append(f"{matches[0]}")
        return found

    def should_rewrite(self, result: dict) -> bool:
        return result.get("realness_score", 0) < self.REALNESS_THRESHOLD
