"""
Comment Miner — haalt echte problemen en vragen op van de doelgroep.

Bronnen:
1. Facebook Graph API (eigen pagina + publieke pagina's) — wanneer token beschikbaar
2. Gecachede probleem-seeds (handmatig/web-gemined) — altijd beschikbaar
3. Periodieke WebSearch-gebaseerde mining (via aparte job)

Output: data/problem_seeds/{app_id}.json
Elke seed bevat: problem, source, theme, frequency_signal, content_angle

De IdeaGeneratorAgent en ScriptWriterAgent laden deze seeds zodat content
echte problemen oplost in plaats van generieke hooks te gebruiken.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional
from loguru import logger

ROOT = Path(__file__).parent.parent
PROBLEMS_DIR = ROOT / "data" / "problem_seeds"
PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)


def load_problem_seeds(app_id: str, theme: Optional[str] = None, limit: int = 6) -> list[dict]:
    """
    Laad opgeslagen probleem-seeds. Optioneel gefilterd op thema.
    """
    path = PROBLEMS_DIR / f"{app_id}.json"
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        seeds = data.get("problems", [])
        if theme:
            seeds = [s for s in seeds if s.get("theme") == theme]
        return seeds[:limit]
    except Exception as e:
        logger.warning(f"[CommentMiner] Problem seeds laden mislukt: {e}")
        return []


def format_problems_for_prompt(problems: list[dict]) -> str:
    """
    Formatteer probleem-seeds als tekst voor gebruik in een AI-prompt.
    """
    if not problems:
        return ""

    lines = ["=== ECHTE PROBLEMEN VAN DE DOELGROEP ===\n"]
    lines.append("Dit zijn de exacte vragen, twijfels en struggles die echte mensen")
    lines.append("uittypen in comments, forums en reviews. Schrijf content die DEZE problemen oplost.\n")

    for i, p in enumerate(problems, 1):
        lines.append(f"{i}. [{p['theme'].upper()}] Frequentie: {p.get('frequency_signal', 'vaak')}")
        lines.append(f"   Probleem: \"{p['problem']}\"")
        if p.get("exact_quote"):
            lines.append(f"   Echte quote: \"{p['exact_quote']}\"")
        if p.get("content_angle"):
            lines.append(f"   Content angle: {p['content_angle']}")
        lines.append("")

    lines.append("Gebruik de taal, emoties en specifieke woorden van deze mensen.")
    lines.append("Een goede post begint met het probleem erkennen — dan pas de oplossing.")
    return "\n".join(lines)


def read_facebook_comments(
    page_id: str,
    access_token: str,
    limit_posts: int = 10,
    limit_comments: int = 50,
) -> list[dict]:
    """
    Lees comments van een Facebook pagina via Graph API.
    Vereist: geldig access_token met pages_read_engagement permissie.
    """
    import httpx

    comments = []
    try:
        # Haal recente posts op
        posts_resp = httpx.get(
            f"https://graph.facebook.com/v21.0/{page_id}/posts",
            params={
                "access_token": access_token,
                "limit": limit_posts,
                "fields": "id,message,created_time",
            },
            timeout=15,
        )
        posts_resp.raise_for_status()
        posts = posts_resp.json().get("data", [])

        for post in posts:
            post_id = post["id"]
            # Haal comments op voor deze post
            comments_resp = httpx.get(
                f"https://graph.facebook.com/v21.0/{post_id}/comments",
                params={
                    "access_token": access_token,
                    "limit": limit_comments,
                    "fields": "message,like_count,created_time",
                    "filter": "stream",
                },
                timeout=15,
            )
            if comments_resp.status_code == 200:
                for c in comments_resp.json().get("data", []):
                    msg = c.get("message", "").strip()
                    if msg and len(msg) > 20:
                        comments.append({
                            "text": msg,
                            "likes": c.get("like_count", 0),
                            "post_id": post_id,
                        })
            time.sleep(0.5)

    except Exception as e:
        logger.warning(f"[CommentMiner] Facebook comments ophalen mislukt: {e}")

    return comments
