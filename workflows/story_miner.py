"""
Story Miner — haalt echte gebruikersverhalen op van Reddit en forums.

Bronnen:
- r/Ozempic (GLP-1 ervaringen)
- r/WeightLoss (afvalverhalen)
- r/loseit (persoonlijke verhalen)

Output: data/story_seeds/{app_id}.json
Elke seed bevat: title, body, source, upvotes, theme, keywords

De IdeaGeneratorAgent laadt deze seeds als context zodat content gebaseerd
is op wat echte mensen daadwerkelijk meemaken en zeggen.
"""

import json
import os
import time
from pathlib import Path
from typing import Optional
from loguru import logger

import httpx

ROOT = Path(__file__).parent.parent
SEEDS_DIR = ROOT / "data" / "story_seeds"
SEEDS_DIR.mkdir(parents=True, exist_ok=True)

# Reddit subreddits relevant voor GLP Coach
GLP_SUBREDDITS = [
    "Ozempic",
    "WeightLoss",
    "loseit",
    "GLP1",
    "mounjaro",
]

# Zoektermen binnen subreddits
GLP_SEARCH_TERMS = [
    "my experience",
    "lost weight",
    "life changing",
    "side effects",
    "emotional eating",
    "first month",
    "transformation",
    "plateau",
    "hunger gone",
    "food noise",
]

# Minimum kwaliteitsdrempel
MIN_UPVOTES = 50
MAX_STORIES = 30


def _reddit_headers() -> dict:
    return {
        "User-Agent": "GLP-Content-Bot/1.0 (content research, non-commercial)",
        "Accept": "application/json",
    }


def _fetch_top_posts(subreddit: str, limit: int = 25) -> list[dict]:
    """Haal top posts op van een subreddit via de publieke JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/top.json"
    try:
        resp = httpx.get(
            url,
            headers=_reddit_headers(),
            params={"limit": limit, "t": "month"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("data", {}).get("children", [])
        return [p["data"] for p in posts]
    except Exception as e:
        logger.warning(f"[StoryMiner] r/{subreddit} ophalen mislukt: {e}")
        return []


def _fetch_search_posts(subreddit: str, query: str, limit: int = 10) -> list[dict]:
    """Zoek posts in een subreddit op een zoekterm."""
    url = f"https://www.reddit.com/r/{subreddit}/search.json"
    try:
        resp = httpx.get(
            url,
            headers=_reddit_headers(),
            params={"q": query, "limit": limit, "sort": "top", "t": "year", "restrict_sr": 1},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("data", {}).get("children", [])
        return [p["data"] for p in posts]
    except Exception as e:
        logger.warning(f"[StoryMiner] r/{subreddit} zoeken '{query}' mislukt: {e}")
        return []


def _classify_theme(title: str, body: str) -> str:
    """Bepaal het thema van een verhaal op basis van keywords."""
    text = (title + " " + body).lower()
    if any(w in text for w in ["food noise", "hunger", "cravings", "appetite"]):
        return "hunger_control"
    if any(w in text for w in ["lost", "pounds", "kg", "weight loss", "transformation"]):
        return "weight_loss"
    if any(w in text for w in ["energy", "tired", "fatigue", "sleep"]):
        return "energy"
    if any(w in text for w in ["emotional", "relationship with food", "binge", "mindset"]):
        return "emotional_eating"
    if any(w in text for w in ["side effect", "nausea", "sick", "injection"]):
        return "side_effects"
    if any(w in text for w in ["plateau", "stall", "stuck", "no weight"]):
        return "plateau"
    if any(w in text for w in ["doctor", "prescription", "insurance", "cost"]):
        return "access"
    return "general"


def _extract_keywords(title: str, body: str) -> list[str]:
    """Extraheer opvallende keywords/zinnen."""
    import re
    text = (title + " " + body).lower()
    keywords = []
    patterns = [
        r"\d+\s*(?:lbs?|pounds?|kg|kilo)",
        r"\d+\s*(?:weeks?|months?|years?)",
        r"(?:first|after|within)\s+\w+\s+(?:week|month)",
        r"life.changing",
        r"food noise",
        r"no longer (?:hungry|craving)",
        r"finally",
    ]
    for pat in patterns:
        matches = re.findall(pat, text)
        keywords.extend(matches[:2])
    return list(set(keywords))[:8]


def _post_to_seed(post: dict, source_sub: str) -> Optional[dict]:
    """Converteer een Reddit post naar een story seed."""
    title = post.get("title", "").strip()
    body = post.get("selftext", "").strip()
    upvotes = post.get("score", 0)
    num_comments = post.get("num_comments", 0)

    # Filter: te weinig upvotes, verwijderde posts, te korte body
    if upvotes < MIN_UPVOTES:
        return None
    if body in ("[deleted]", "[removed]", ""):
        return None
    if len(body) < 80:
        return None

    # Trim body tot max 800 tekens (genoeg voor context)
    body_trimmed = body[:800] + ("..." if len(body) > 800 else "")

    theme = _classify_theme(title, body)
    keywords = _extract_keywords(title, body)

    return {
        "id": post.get("id", ""),
        "title": title,
        "body": body_trimmed,
        "source": f"r/{source_sub}",
        "upvotes": upvotes,
        "comments": num_comments,
        "theme": theme,
        "keywords": keywords,
        "url": f"https://reddit.com{post.get('permalink', '')}",
    }


def mine_stories(app_id: str, max_stories: int = MAX_STORIES) -> list[dict]:
    """
    Haal echte gebruikersverhalen op en sla ze op als story seeds.

    Returns:
        Lijst van story seeds, gesorteerd op upvotes
    """
    logger.info(f"[StoryMiner] Start mining voor app={app_id}...")
    all_seeds = []
    seen_ids = set()

    # Top posts per subreddit
    for sub in GLP_SUBREDDITS[:3]:  # max 3 subs om rate limit te vermijden
        posts = _fetch_top_posts(sub, limit=25)
        for post in posts:
            seed = _post_to_seed(post, sub)
            if seed and seed["id"] not in seen_ids:
                all_seeds.append(seed)
                seen_ids.add(seed["id"])
        time.sleep(1)  # Reddit rate limit

    # Gerichte zoekopdrachten
    for term in GLP_SEARCH_TERMS[:4]:  # max 4 searches
        posts = _fetch_search_posts("Ozempic", term, limit=8)
        for post in posts:
            seed = _post_to_seed(post, "Ozempic")
            if seed and seed["id"] not in seen_ids:
                all_seeds.append(seed)
                seen_ids.add(seed["id"])
        time.sleep(1)

    # Sorteer op engagement (upvotes + comments gewogen)
    all_seeds.sort(key=lambda s: s["upvotes"] + s["comments"] * 2, reverse=True)
    top_seeds = all_seeds[:max_stories]

    # Opslaan
    output_path = SEEDS_DIR / f"{app_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "app_id": app_id,
            "mined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(top_seeds),
            "seeds": top_seeds,
        }, f, ensure_ascii=False, indent=2)

    logger.success(f"[StoryMiner] {len(top_seeds)} verhalen opgeslagen → {output_path}")
    return top_seeds


def load_story_seeds(app_id: str, theme: Optional[str] = None, limit: int = 5) -> list[dict]:
    """
    Laad opgeslagen story seeds. Optioneel gefilterd op thema.
    Retourneert lege lijst als geen seeds beschikbaar.
    """
    path = SEEDS_DIR / f"{app_id}.json"
    if not path.exists():
        return []

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        seeds = data.get("seeds", [])
        if theme:
            seeds = [s for s in seeds if s.get("theme") == theme]
        return seeds[:limit]
    except Exception as e:
        logger.warning(f"[StoryMiner] Seeds laden mislukt: {e}")
        return []


def format_seeds_for_prompt(seeds: list[dict]) -> str:
    """
    Formatteer story seeds als tekst voor gebruik in een AI-prompt.
    """
    if not seeds:
        return ""

    lines = ["=== ECHTE GEBRUIKERSVERHALEN (Reddit) ===\n"]
    for i, seed in enumerate(seeds, 1):
        lines.append(f"{i}. [{seed['theme'].upper()}] r/{seed['source'].lstrip('r/')} ({seed['upvotes']} upvotes)")
        lines.append(f"   \"{seed['title']}\"")
        if seed.get("keywords"):
            lines.append(f"   Keywords: {', '.join(seed['keywords'])}")
        lines.append(f"   {seed['body'][:300]}...")
        lines.append("")

    lines.append("Gebruik deze echte ervaringen als inspiratie. Baseer hooks en scripts op wat mensen")
    lines.append("daadwerkelijk meemaken — gebruik hun taalgebruik, emoties en specifieke details.")
    return "\n".join(lines)
