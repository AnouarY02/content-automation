"""
Analytics API endpoints.
"""

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "analytics"


@router.get("/{app_id}/summary")
def get_summary(app_id: str):
    """Haal analytics samenvatting op voor een app."""
    path = DATA_DIR / f"{app_id}_summary.json"
    if not path.exists():
        return {"app_id": app_id, "message": "Nog geen analytics beschikbaar", "posts": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.get("/{app_id}/posts")
def get_posts(app_id: str, limit: int = 20):
    """Haal gepubliceerde posts op met performance-data."""
    path = DATA_DIR / f"{app_id}_posts.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        posts = json.load(f)
    return posts[:limit]
