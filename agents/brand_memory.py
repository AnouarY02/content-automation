"""
Brand Memory systeem.
Laadt, slaat op en updatet de merkgeheugen-bestanden per app.
"""

from datetime import date
from typing import Any

from loguru import logger

from backend.repository.factory import get_app_repo


def load(app_id: str) -> dict:
    """Laad brand memory voor een app. Geeft lege dict terug als niet gevonden."""
    memory = get_app_repo(tenant_id="default").get_brand_memory(app_id)
    if not memory:
        logger.warning(f"Brand memory niet gevonden voor {app_id}, geeft leeg object")
        return {"app_id": app_id, "learned_insights": [], "performance_history": {}}
    return memory


def save(app_id: str, memory: dict) -> None:
    """Sla brand memory op voor een app."""
    memory["last_updated"] = str(date.today())
    get_app_repo(tenant_id="default").save_brand_memory(app_id, memory)
    logger.info(f"Brand memory opgeslagen voor {app_id}")


def apply_updates(app_id: str, updates: dict) -> dict:
    """
    Pas brand memory updates toe vanuit analyst agent output.

    updates verwacht structuur:
    {
      "add_to_top_hooks": [...],
      "add_to_avoided": [...],
      "update_optimal_time": "HH:MM" or null,
      "update_best_format": "format" or null
    }
    """
    memory = load(app_id)

    if hooks := updates.get("add_to_top_hooks"):
        existing = memory.get("top_performing_hooks", [])
        for hook in hooks:
            if hook not in existing:
                existing.append(hook)
        memory["top_performing_hooks"] = existing[-10:]  # max 10 bewaren

    if avoided := updates.get("add_to_avoided"):
        existing = memory.get("avoided_topics", [])
        for item in avoided:
            if item not in existing:
                existing.append(item)
        memory["avoided_topics"] = existing

    if time_update := updates.get("update_optimal_time"):
        memory.setdefault("performance_history", {})["optimal_post_time"] = time_update

    if format_update := updates.get("update_best_format"):
        memory.setdefault("content_formats", {})["best_performing"] = format_update

    save(app_id, memory)
    return memory


def add_insight(app_id: str, insight: str) -> None:
    """Voeg een geleerde les toe aan brand memory."""
    memory = load(app_id)
    insights = memory.get("learned_insights", [])
    entry = {"date": str(date.today()), "insight": insight}
    if entry not in insights:
        insights.append(entry)
    memory["learned_insights"] = insights[-20:]  # max 20 bewaren
    save(app_id, memory)


def format_for_prompt(memory: dict) -> str:
    """Zet brand memory om naar een leesbaar formaat voor prompts."""
    lines = [
        f"App: {memory.get('app_name', memory.get('app_id', '?'))}",
        f"Niche: {memory.get('niche', 'niet gespecificeerd')}",
        f"Tone of voice: {memory.get('tone_of_voice', 'niet gespecificeerd')}",
        f"Target audience: {memory.get('target_audience', 'niet gespecificeerd')}",
        f"USP: {memory.get('usp', 'niet gespecificeerd')}",
    ]

    # Website URL voor context
    url = memory.get("url", "")
    if url:
        lines.append(f"Website: {url}")

    # Beschrijving
    desc = memory.get("description", "")
    if desc:
        lines.append(f"Beschrijving: {desc}")

    # Features
    features = memory.get("features", [])
    if features:
        lines.append("\nBelangrijkste features:")
        for feat in features[:5]:
            lines.append(f"  - {feat}")

    # Top hooks
    hooks = memory.get("top_performing_hooks", [])
    if hooks:
        lines.append("\nBeste hooks tot nu toe:")
        for hook in hooks:
            lines.append(f"  - {hook}")

    # Vermijden
    avoided = memory.get("avoided_topics", [])
    if avoided:
        lines.append("\nVermijd:")
        for item in avoided:
            lines.append(f"  - {item}")

    # Performance data
    perf = memory.get("performance_history", {})
    if perf:
        best_type = perf.get("best_post_type", "")
        opt_time = perf.get("optimal_post_time", "")
        if best_type:
            lines.append(f"\nBeste format: {best_type}")
        if opt_time:
            lines.append(f"Optimale posttijd: {opt_time}")

    # Content stijl richtlijnen
    content_formats = memory.get("content_formats", {})
    if content_formats:
        best_fmt = content_formats.get("best_performing", "")
        if best_fmt:
            lines.append(f"Best presterende format: {best_fmt}")

    # Geleerde lessen (meer context)
    insights = memory.get("learned_insights", [])
    if insights:
        lines.append("\nGeleerde lessen (gebruik deze als richtlijn):")
        for entry in insights[-8:]:
            lines.append(f"  [{entry.get('date', '?')}] {entry.get('insight', '')}")

    return "\n".join(lines)
