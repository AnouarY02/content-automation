from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "configs" / "app_registry.json"
CAMPAIGNS_DIR = ROOT / "data" / "campaigns"
BRAND_MEMORY_DIR = ROOT / "data" / "brand_memory"


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def request_json(method: str, url: str, api_key: str, payload=None):
    data = None
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body) if body else None


def upsert_row(base_url: str, api_key: str, table: str, row: dict, on_conflict: str):
    qs = urllib.parse.urlencode({"on_conflict": on_conflict})
    url = f"{base_url}/rest/v1/{table}?{qs}"
    return request_json("POST", url, api_key, [row])


def list_campaigns_for_app(app_id: str) -> list[dict]:
    bundles: list[dict] = []
    for path in CAMPAIGNS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("app_id") == app_id:
            bundles.append(data)
    return bundles


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase tables via REST API.")
    parser.add_argument("--app-id", action="append", required=True)
    parser.add_argument("--url", default=os.getenv("SUPABASE_URL", ""))
    parser.add_argument("--service-key", default=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", ""))
    args = parser.parse_args()

    if not args.url or not args.service_key:
        raise SystemExit("SUPABASE_URL en service-role key zijn verplicht.")

    registry = load_json(REGISTRY_PATH, {"apps": []}).get("apps", [])
    app_map = {app["id"]: app for app in registry}

    seeded_apps = 0
    seeded_memory = 0
    seeded_campaigns = 0

    for app_id in args.app_id:
        app = app_map.get(app_id)
        if not app:
            raise SystemExit(f"App niet gevonden in registry: {app_id}")

        metadata = {
            key: app[key]
            for key in ("brand_memory_file", "features", "tone", "content_angles", "last_updated")
            if key in app
        }
        app_row = {
            "id": app["id"],
            "name": app.get("name"),
            "url": app.get("url"),
            "description": app.get("description"),
            "target_audience": app.get("target_audience"),
            "usp": app.get("usp"),
            "niche": app.get("niche") or "health",
            "active_channels": app.get("active_channels") or ["tiktok"],
            "active": bool(app.get("active", True)),
            "tenant_id": app.get("tenant_id", "default"),
            "created_at": app.get("created_at"),
            "metadata": metadata,
        }
        upsert_row(args.url, args.service_key, "apps", app_row, "id")
        seeded_apps += 1

        memory = load_json(BRAND_MEMORY_DIR / f"{app_id}.json", {})
        memory_row = {
            "app_id": app_id,
            "niche": memory.get("niche"),
            "tone_of_voice": memory.get("tone_of_voice"),
            "creator_persona": memory.get("creator_persona"),
            "top_performing_hooks": memory.get("top_performing_hooks") or [],
            "avoided_topics": memory.get("avoided_topics") or [],
            "performance_history": memory.get("performance_history") or {},
            "learned_insights": memory.get("learned_insights") or [],
            "memory": memory or {"app_id": app_id},
        }
        upsert_row(args.url, args.service_key, "brand_memory", memory_row, "app_id")
        seeded_memory += 1

        for bundle in list_campaigns_for_app(app_id):
            campaign_row = {
                "id": bundle["id"],
                "app_id": bundle["app_id"],
                "tenant_id": bundle.get("tenant_id", "default"),
                "platform": bundle.get("platform", "tiktok"),
                "status": bundle.get("status", "draft"),
                "display_name": bundle.get("display_name"),
                "idea": bundle.get("idea") or {},
                "script": bundle.get("script") or {},
                "caption": bundle.get("caption") or {},
                "viral_score": bundle.get("viral_score"),
                "video_url": bundle.get("video_path"),
                "video_metadata": {
                    "thumbnail_path": bundle.get("thumbnail_path"),
                    "scheduled_for": bundle.get("scheduled_for"),
                    "rejection_reason": bundle.get("rejection_reason"),
                },
                "total_cost_usd": float(bundle.get("total_cost_usd") or 0),
                "cost_breakdown": bundle.get("cost_breakdown") or [],
                "approved_by": bundle.get("approved_by"),
                "approval_notes": bundle.get("approval_notes"),
                "approved_at": bundle.get("approved_at"),
                "post_id": bundle.get("post_id"),
                "published_at": bundle.get("published_at"),
                "experiment_id": bundle.get("experiment_id"),
                "created_at": bundle.get("created_at"),
                "updated_at": bundle.get("updated_at") or bundle.get("created_at"),
            }
            upsert_row(args.url, args.service_key, "campaigns", campaign_row, "id")
            seeded_campaigns += 1

    print(
        f"seeded apps={seeded_apps} brand_memory={seeded_memory} campaigns={seeded_campaigns}"
    )


if __name__ == "__main__":
    main()
