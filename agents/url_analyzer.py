"""
URL Analyzer Agent — bezoekt een app-URL, extraheert inhoud en genereert
automatisch app-beschrijving, doelgroep, USP en niche.

Gebruik:
    agent = URLAnalyzerAgent()
    result = agent.run(url="https://example.com")
    # result = { "description": "...", "target_audience": "...", "usp": "...", "niche": "..." }
"""

import re

import httpx
from loguru import logger

from agents.base_agent import BaseAgent


class URLAnalyzerAgent(BaseAgent):
    """Analyseert een website-URL en extraheert app-informatie voor content-generatie."""

    task_name = "idea_generation"  # Hergebruik snelle/goedkope model config

    def run(self, url: str, existing_info: dict | None = None, **kwargs) -> dict:
        """
        Bezoek de URL, haal de pagina-inhoud op, en laat de AI
        een gestructureerde app-beschrijving genereren.

        Args:
            url: Volledige URL naar de app/website
            existing_info: Eventueel al bekende info (naam, beschrijving)

        Returns:
            dict met keys: name, description, target_audience, usp, niche, features, tone
        """
        logger.info(f"[URLAnalyzer] Analyseer URL: {url}")

        # Stap 1: Haal pagina-inhoud op
        page_content = self._fetch_page(url)
        if not page_content:
            logger.warning("[URLAnalyzer] Kon pagina niet laden — gebruik alleen URL als hint")
            page_content = f"URL: {url} (pagina kon niet geladen worden)"

        # Stap 2: Beperk content tot bruikbaar formaat
        clean_content = self._clean_html(page_content)
        # Limiteer tot ~3000 chars om tokens te besparen
        if len(clean_content) > 3000:
            clean_content = clean_content[:3000] + "\n[... ingekort ...]"

        # Stap 3: AI analyse
        existing_str = ""
        if existing_info:
            parts = []
            if existing_info.get("name"):
                parts.append(f"Naam: {existing_info['name']}")
            if existing_info.get("description"):
                parts.append(f"Beschrijving: {existing_info['description']}")
            existing_str = "\n".join(parts)

        system = self._build_system_prompt(
            "Je bent een expert in het analyseren van apps en websites. "
            "Je extraheert gestructureerde informatie voor marketing-content generatie."
        )

        user_message = f"""Analyseer de volgende website en geef een gestructureerd profiel terug.

URL: {url}

PAGINA-INHOUD:
{clean_content}

{f'BEKENDE INFORMATIE:{chr(10)}{existing_str}' if existing_str else ''}

Geef een JSON object terug met exact deze velden:
{{
  "name": "Naam van de app/dienst (kort, krachtig)",
  "description": "Wat doet deze app? Max 2 zinnen, helder voor iedereen.",
  "target_audience": "Wie is de doelgroep? Leeftijd, beroep, interesses. Specifiek.",
  "usp": "Wat is het grootste voordeel? Eén zin, concreet en meetbaar als mogelijk.",
  "niche": "Eén van: productivity, finance, health, education, ecommerce, saas, entertainment, social, general",
  "features": ["Feature 1", "Feature 2", "Feature 3"],
  "tone": "Welke tone-of-voice past bij deze app? bijv. professioneel, speels, urgent, inspirerend",
  "content_angles": ["Marketing hoek 1 voor TikTok", "Marketing hoek 2", "Marketing hoek 3"]
}}

Antwoord ALLEEN met het JSON object, geen uitleg."""

        raw = self._call_api(system, user_message)
        result = self._parse_json_response(raw)

        logger.info(f"[URLAnalyzer] Analyse compleet: {result.get('name', '?')}")
        return result

    def _fetch_page(self, url: str) -> str | None:
        """Haal de HTML-inhoud op van een URL."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; AYMarketingBot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "nl,en;q=0.9",
            }
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            logger.warning(f"[URLAnalyzer] Ophalen mislukt ({url}): {e}")
            return None

    def _clean_html(self, html: str) -> str:
        """Strip HTML tags en extraheer leesbare tekst."""
        # Verwijder script en style blokken
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # Verwijder HTML tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Verwijder dubbele whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Verwijder niet-leesbare tekens
        text = re.sub(r'[^\w\s.,!?;:\-\'\"€$@#&()/\n]', '', text)
        return text
