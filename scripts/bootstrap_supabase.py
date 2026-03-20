from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = Path(r"C:\AI-Factory\.env")
REGISTRY_PATH = ROOT / "configs" / "app_registry.json"
CAMPAIGNS_DIR = ROOT / "data" / "campaigns"
BRAND_MEMORY_DIR = ROOT / "data" / "brand_memory"
SUPABASE_DIR = ROOT / "supabase"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_pg_connection(env: dict[str, str]):
    host = env["DB_POSTGRESDB_HOST"]
    port = env["DB_POSTGRESDB_PORT"]
    database = env["DB_POSTGRESDB_DATABASE"]
    user = env["DB_POSTGRESDB_USER"]
    password = env["DB_POSTGRESDB_PASSWORD"]

    try:
        import psycopg

        return psycopg.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            sslmode="require",
        )
    except ImportError:
        import psycopg2

        return psycopg2.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            sslmode="require",
        )


def execute_sql_file(cur, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    cur.execute(sql)


def load_registry() -> list[dict]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return data.get("apps", [])


def load_brand_memory(app_id: str) -> dict:
    path = BRAND_MEMORY_DIR / f"{app_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def iter_campaigns(valid_app_ids: set[str]):
    for path in CAMPAIGNS_DIR.glob("*.json"):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        app_id = bundle.get("app_id")
        if app_id not in valid_app_ids:
            continue
        yield bundle


def upsert_apps(cur, apps: list[dict]) -> None:
    for app in apps:
        metadata = {
            key: app[key]
            for key in ("brand_memory_file", "features", "tone", "content_angles", "last_updated")
            if key in app
        }
        cur.execute(
            """
            INSERT INTO apps (
              id, name, url, description, target_audience, usp,
              niche, active_channels, active, tenant_id, created_at, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
              name = EXCLUDED.name,
              url = EXCLUDED.url,
              description = EXCLUDED.description,
              target_audience = EXCLUDED.target_audience,
              usp = EXCLUDED.usp,
              niche = EXCLUDED.niche,
              active_channels = EXCLUDED.active_channels,
              active = EXCLUDED.active,
              tenant_id = EXCLUDED.tenant_id,
              metadata = EXCLUDED.metadata
            """,
            (
                app["id"],
                app.get("name"),
                app.get("url"),
                app.get("description"),
                app.get("target_audience"),
                app.get("usp"),
                app.get("niche") or "health",
                app.get("active_channels") or ["tiktok"],
                bool(app.get("active", True)),
                app.get("tenant_id", "default"),
                app.get("created_at"),
                json.dumps(metadata),
            ),
        )


def upsert_brand_memory(cur, app_id: str, memory: dict) -> None:
    cur.execute(
        """
        INSERT INTO brand_memory (
          app_id, niche, tone_of_voice, creator_persona,
          top_performing_hooks, avoided_topics,
          performance_history, learned_insights, updated_at, memory
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW(), %s::jsonb)
        ON CONFLICT (app_id) DO UPDATE SET
          niche = EXCLUDED.niche,
          tone_of_voice = EXCLUDED.tone_of_voice,
          creator_persona = EXCLUDED.creator_persona,
          top_performing_hooks = EXCLUDED.top_performing_hooks,
          avoided_topics = EXCLUDED.avoided_topics,
          performance_history = EXCLUDED.performance_history,
          learned_insights = EXCLUDED.learned_insights,
          memory = EXCLUDED.memory,
          updated_at = NOW()
        """,
        (
            app_id,
            memory.get("niche"),
            memory.get("tone_of_voice"),
            memory.get("creator_persona"),
            memory.get("top_performing_hooks") or [],
            memory.get("avoided_topics") or [],
            json.dumps(memory.get("performance_history") or {}),
            json.dumps(memory.get("learned_insights") or []),
            json.dumps(memory or {}),
        ),
    )


def upsert_campaign(cur, bundle: dict) -> None:
    video_metadata = {
        "thumbnail_path": bundle.get("thumbnail_path"),
        "scheduled_for": bundle.get("scheduled_for"),
        "rejection_reason": bundle.get("rejection_reason"),
    }
    cur.execute(
        """
        INSERT INTO campaigns (
          id, app_id, tenant_id, platform, status, display_name,
          idea, script, caption, viral_score,
          video_url, video_metadata,
          total_cost_usd, cost_breakdown,
          approved_by, approval_notes, approved_at,
          post_id, published_at, experiment_id,
          created_at, updated_at
        )
        VALUES (
          %s, %s, %s, %s, %s, %s,
          %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
          %s, %s::jsonb,
          %s, %s::jsonb,
          %s, %s, %s,
          %s, %s, %s,
          %s, %s
        )
        ON CONFLICT (id) DO UPDATE SET
          app_id = EXCLUDED.app_id,
          tenant_id = EXCLUDED.tenant_id,
          platform = EXCLUDED.platform,
          status = EXCLUDED.status,
          display_name = EXCLUDED.display_name,
          idea = EXCLUDED.idea,
          script = EXCLUDED.script,
          caption = EXCLUDED.caption,
          viral_score = EXCLUDED.viral_score,
          video_url = EXCLUDED.video_url,
          video_metadata = EXCLUDED.video_metadata,
          total_cost_usd = EXCLUDED.total_cost_usd,
          cost_breakdown = EXCLUDED.cost_breakdown,
          approved_by = EXCLUDED.approved_by,
          approval_notes = EXCLUDED.approval_notes,
          approved_at = EXCLUDED.approved_at,
          post_id = EXCLUDED.post_id,
          published_at = EXCLUDED.published_at,
          experiment_id = EXCLUDED.experiment_id,
          updated_at = EXCLUDED.updated_at
        """,
        (
            bundle["id"],
            bundle["app_id"],
            bundle.get("tenant_id", "default"),
            bundle.get("platform", "tiktok"),
            bundle.get("status", "draft"),
            bundle.get("display_name"),
            json.dumps(bundle.get("idea") or {}),
            json.dumps(bundle.get("script") or {}),
            json.dumps(bundle.get("caption") or {}),
            json.dumps(bundle.get("viral_score") or None),
            bundle.get("video_path"),
            json.dumps(video_metadata),
            float(bundle.get("total_cost_usd") or 0),
            json.dumps(bundle.get("cost_breakdown") or []),
            bundle.get("approved_by"),
            bundle.get("approval_notes"),
            bundle.get("approved_at"),
            bundle.get("post_id"),
            bundle.get("published_at"),
            bundle.get("experiment_id"),
            bundle.get("created_at"),
            bundle.get("updated_at") or bundle.get("created_at"),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap Supabase schema and local seed data.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--seed-app-id", action="append", default=[])
    args = parser.parse_args()

    env = load_env_file(args.env_file)
    seed_ids = set(args.seed_app_id)

    registry_apps = [
        app for app in load_registry()
        if app.get("id") in seed_ids
    ]
    valid_app_ids = {app["id"] for app in registry_apps}

    conn = get_pg_connection(env)
    try:
        with conn:
            with conn.cursor() as cur:
                if args.reset:
                    execute_sql_file(cur, SUPABASE_DIR / "reset.sql")
                execute_sql_file(cur, SUPABASE_DIR / "schema.sql")
                execute_sql_file(cur, SUPABASE_DIR / "storage.sql")
                upsert_apps(cur, registry_apps)
                for app in registry_apps:
                    upsert_brand_memory(cur, app["id"], load_brand_memory(app["id"]))
                for campaign in iter_campaigns(valid_app_ids):
                    upsert_campaign(cur, campaign)
        print(f"Seeded apps={len(registry_apps)} ids={sorted(seed_ids)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
