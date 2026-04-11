"""
Analytics API — gecombineerde cross-platform analytics voor alle kanalen.

Aggregeert data van:
- Gepubliceerde campagnes (uit data/campaigns/)
- Platform analytics (uit data/analytics/)
- Kosten (uit data/costs/)

Endpoints:
  GET /api/analytics/overview          — Dashboard KPIs (alle apps)
  GET /api/analytics/{app_id}/summary  — App-specifieke samenvatting
  GET /api/analytics/{app_id}/posts    — Posts met performance data
  GET /api/analytics/{app_id}/platforms — Per-platform breakdown
  GET /api/analytics/{app_id}/timeline  — Posts over tijd
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter()

_ROOT = Path(__file__).parent.parent.parent
_CAMPAIGNS_DIR = _ROOT / "data" / "campaigns"
_ANALYTICS_DIR = _ROOT / "data" / "analytics"
_COSTS_DIR = _ROOT / "data" / "costs"


def _load_campaigns(app_id: str | None = None) -> list[dict]:
    """Laad alle campagnes, optioneel gefilterd op app_id."""
    campaigns = []
    if not _CAMPAIGNS_DIR.exists():
        return campaigns
    tenant_dir = _CAMPAIGNS_DIR / "default"
    if not tenant_dir.exists():
        tenant_dir = _CAMPAIGNS_DIR
    for f in tenant_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if app_id is None or data.get("app_id") == app_id:
                campaigns.append(data)
        except Exception:
            pass
    return campaigns


def _load_analytics_raw(app_id: str) -> list[dict]:
    """Laad ruwe analytics per post."""
    raw_dir = _ANALYTICS_DIR / "raw" / app_id
    records = []
    if raw_dir.exists():
        for f in raw_dir.glob("*.json"):
            try:
                records.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
    return records


def _load_costs_today() -> list[dict]:
    """Laad dagelijkse kosten (laatste dag)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cost_file = _COSTS_DIR / f"{today}.json"
    if cost_file.exists():
        try:
            return json.loads(cost_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


@router.get("/overview")
def get_overview():
    """
    Dashboard KPIs — gecombineerd over alle apps en platforms.

    Geeft: totaal posts, bereik, engagement, kosten, per-platform breakdown.
    """
    all_campaigns = _load_campaigns()
    published = [c for c in all_campaigns if c.get("status") == "published"]

    # Per-platform stats
    platform_stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "cost_usd": 0.0, "viral_scores": []
    })
    total_cost = 0.0
    total_viral = []

    for c in all_campaigns:
        platform = c.get("platform", "tiktok")
        platform_stats[platform]["count"] += 1
        cost = c.get("total_cost_usd", 0) or 0
        platform_stats[platform]["cost_usd"] += cost
        total_cost += cost
        vs = c.get("viral_score")
        if vs and isinstance(vs, dict):
            score = vs.get("composite_score", 0)
            if score:
                platform_stats[platform]["viral_scores"].append(score)
                total_viral.append(score)

    # Bereken gemiddelden
    platform_breakdown = {}
    for platform, stats in platform_stats.items():
        scores = stats["viral_scores"]
        platform_breakdown[platform] = {
            "total_campaigns": stats["count"],
            "published": sum(1 for c in published if c.get("platform", "tiktok") == platform),
            "cost_usd": round(stats["cost_usd"], 4),
            "avg_viral_score": round(sum(scores) / len(scores), 1) if scores else 0,
        }

    return {
        "total_campaigns": len(all_campaigns),
        "published": len(published),
        "pending_approval": sum(1 for c in all_campaigns if c.get("status") == "pending_approval"),
        "failed": sum(1 for c in all_campaigns if c.get("status") == "failed"),
        "total_cost_usd": round(total_cost, 4),
        "avg_viral_score": round(sum(total_viral) / len(total_viral), 1) if total_viral else 0,
        "platforms": platform_breakdown,
        "channels_connected": list(platform_breakdown.keys()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{app_id}/summary")
def get_summary(app_id: str):
    """App-specifieke analytics samenvatting."""
    campaigns = _load_campaigns(app_id)
    published = [c for c in campaigns if c.get("status") == "published"]
    pending = [c for c in campaigns if c.get("status") == "pending_approval"]

    total_cost = sum(c.get("total_cost_usd", 0) or 0 for c in campaigns)
    viral_scores = [
        c["viral_score"]["composite_score"]
        for c in campaigns
        if c.get("viral_score") and isinstance(c.get("viral_score"), dict)
        and c["viral_score"].get("composite_score")
    ]

    # Platform breakdown
    platform_counts: dict[str, int] = defaultdict(int)
    for c in published:
        platform_counts[c.get("platform", "tiktok")] += 1

    # Recente posts
    recent = sorted(
        [c for c in published],
        key=lambda x: x.get("published_at") or x.get("created_at") or "",
        reverse=True,
    )[:5]

    recent_posts = []
    for c in recent:
        recent_posts.append({
            "id": c.get("id", "")[:8],
            "title": c.get("idea", {}).get("title") if isinstance(c.get("idea"), dict) else "—",
            "platform": c.get("platform", "tiktok"),
            "published_at": c.get("published_at"),
            "post_id": c.get("post_id"),
            "viral_score": c.get("viral_score", {}).get("composite_score") if isinstance(c.get("viral_score"), dict) else None,
            "cost_usd": round(c.get("total_cost_usd", 0) or 0, 4),
        })

    return {
        "app_id": app_id,
        "total_campaigns": len(campaigns),
        "published": len(published),
        "pending_approval": len(pending),
        "total_cost_usd": round(total_cost, 4),
        "avg_viral_score": round(sum(viral_scores) / len(viral_scores), 1) if viral_scores else 0,
        "platforms": dict(platform_counts),
        "recent_posts": recent_posts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{app_id}/posts")
def get_posts(app_id: str, limit: int = 20, platform: str | None = None):
    """
    Gepubliceerde posts met performance data.

    Combineert campagne metadata met beschikbare analytics metrics.
    """
    campaigns = _load_campaigns(app_id)
    published = [c for c in campaigns if c.get("status") == "published"]

    if platform:
        published = [c for c in published if c.get("platform", "tiktok") == platform]

    published.sort(
        key=lambda x: x.get("published_at") or x.get("created_at") or "",
        reverse=True,
    )

    # Laad analytics per post
    raw_analytics = {r.get("post_id"): r for r in _load_analytics_raw(app_id)}

    posts = []
    for c in published[:limit]:
        post_id = c.get("post_id")
        analytics = raw_analytics.get(post_id, {})
        idea = c.get("idea", {}) if isinstance(c.get("idea"), dict) else {}
        viral = c.get("viral_score", {}) if isinstance(c.get("viral_score"), dict) else {}
        caption = c.get("caption", {}) if isinstance(c.get("caption"), dict) else {}

        posts.append({
            "campaign_id": c.get("id"),
            "post_id": post_id,
            "platform": c.get("platform", "tiktok"),
            "title": idea.get("title", "—"),
            "hook": idea.get("hook", ""),
            "published_at": c.get("published_at"),
            "viral_score": viral.get("composite_score"),
            "viral_verdict": viral.get("verdict"),
            "cost_usd": round(c.get("total_cost_usd", 0) or 0, 4),
            # Platform metrics (indien beschikbaar)
            "views": analytics.get("views", 0),
            "likes": analytics.get("likes", 0),
            "comments": analytics.get("comments", 0),
            "shares": analytics.get("shares", 0),
            "reach": analytics.get("reach", 0),
            "engagement_rate": _calc_engagement(analytics),
            "watch_time_avg_sec": analytics.get("avg_watch_time_sec", 0),
        })

    return posts


@router.get("/{app_id}/platforms")
def get_platform_breakdown(app_id: str):
    """Per-platform breakdown: posts, kosten, engagement."""
    campaigns = _load_campaigns(app_id)

    breakdown: dict[str, dict] = {}
    for platform in ["tiktok", "instagram", "facebook", "youtube"]:
        platform_campaigns = [c for c in campaigns if c.get("platform", "tiktok") == platform]
        published = [c for c in platform_campaigns if c.get("status") == "published"]
        costs = [c.get("total_cost_usd", 0) or 0 for c in platform_campaigns]
        scores = [
            c["viral_score"]["composite_score"]
            for c in platform_campaigns
            if isinstance(c.get("viral_score"), dict) and c["viral_score"].get("composite_score")
        ]

        breakdown[platform] = {
            "total": len(platform_campaigns),
            "published": len(published),
            "pending": sum(1 for c in platform_campaigns if c.get("status") == "pending_approval"),
            "failed": sum(1 for c in platform_campaigns if c.get("status") == "failed"),
            "total_cost_usd": round(sum(costs), 4),
            "avg_cost_per_post": round(sum(costs) / len(platform_campaigns), 4) if platform_campaigns else 0,
            "avg_viral_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "connected": platform in ["tiktok", "facebook"],  # Bijwerken als meer platforms worden gekoppeld
        }

    return breakdown


@router.get("/{app_id}/timeline")
def get_timeline(app_id: str, days: int = 30):
    """Posts over de tijd — voor grafiek in dashboard."""
    campaigns = _load_campaigns(app_id)

    by_date: dict[str, dict] = defaultdict(lambda: {
        "date": "",
        "campaigns": 0,
        "published": 0,
        "cost_usd": 0.0,
        "avg_viral": [],
    })

    for c in campaigns:
        date_str = (c.get("created_at") or "")[:10]
        if not date_str:
            continue
        by_date[date_str]["date"] = date_str
        by_date[date_str]["campaigns"] += 1
        by_date[date_str]["cost_usd"] += c.get("total_cost_usd", 0) or 0
        if c.get("status") == "published":
            by_date[date_str]["published"] += 1
        vs = c.get("viral_score")
        if isinstance(vs, dict) and vs.get("composite_score"):
            by_date[date_str]["avg_viral"].append(vs["composite_score"])

    timeline = []
    for date_str, data in sorted(by_date.items())[-days:]:
        scores = data["avg_viral"]
        timeline.append({
            "date": date_str,
            "campaigns": data["campaigns"],
            "published": data["published"],
            "cost_usd": round(data["cost_usd"], 4),
            "avg_viral_score": round(sum(scores) / len(scores), 1) if scores else 0,
        })

    return timeline


def _calc_engagement(analytics: dict) -> float:
    """Bereken engagement rate: (likes + comments + shares) / views * 100."""
    views = analytics.get("views", 0) or 0
    if not views:
        return 0.0
    interactions = (
        (analytics.get("likes", 0) or 0) +
        (analytics.get("comments", 0) or 0) +
        (analytics.get("shares", 0) or 0)
    )
    return round(interactions / views * 100, 2)
