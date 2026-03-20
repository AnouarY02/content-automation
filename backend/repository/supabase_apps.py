from __future__ import annotations

from loguru import logger

from backend.supabase import get_supabase_client


APP_METADATA_FIELDS = {
    "brand_memory_file",
    "features",
    "tone",
    "content_angles",
    "last_updated",
}


class SupabaseAppRepository:
    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id
        self._client = get_supabase_client()
        if not self._client:
            raise RuntimeError("Supabase app repository vereist geldige SUPABASE env vars.")

    def _to_app_row(self, app: dict, tenant_id: str) -> dict:
        metadata = {key: app[key] for key in APP_METADATA_FIELDS if key in app}
        return {
            "id": app["id"],
            "name": app.get("name"),
            "url": app.get("url"),
            "description": app.get("description"),
            "target_audience": app.get("target_audience"),
            "usp": app.get("usp"),
            "niche": app.get("niche") or "health",
            "active_channels": app.get("active_channels") or ["tiktok"],
            "active": bool(app.get("active", True)),
            "tenant_id": app.get("tenant_id", tenant_id),
            "created_at": app.get("created_at"),
            "metadata": metadata,
        }

    def _from_app_row(self, row: dict) -> dict:
        metadata = row.get("metadata") or {}
        app = {
            "id": row["id"],
            "name": row.get("name"),
            "url": row.get("url", ""),
            "description": row.get("description", ""),
            "target_audience": row.get("target_audience", ""),
            "usp": row.get("usp", ""),
            "niche": row.get("niche", "health"),
            "active_channels": row.get("active_channels") or ["tiktok"],
            "active": bool(row.get("active", True)),
            "tenant_id": row.get("tenant_id", self._tenant_id),
            "created_at": row.get("created_at"),
        }
        app.update(metadata)
        app.setdefault("brand_memory_file", f"data/brand_memory/{app['id']}.json")
        return app

    def list_apps(self, tenant_id: str | None = None) -> list[dict]:
        resolved_tenant = tenant_id or self._tenant_id
        result = (
            self._client.table("apps")
            .select("*")
            .eq("tenant_id", resolved_tenant)
            .order("created_at", desc=False)
            .execute()
        )
        return [self._from_app_row(row) for row in (result.data or [])]

    def get_app(self, app_id: str, tenant_id: str | None = None) -> dict | None:
        resolved_tenant = tenant_id or self._tenant_id
        result = (
            self._client.table("apps")
            .select("*")
            .eq("tenant_id", resolved_tenant)
            .eq("id", app_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        return self._from_app_row(rows[0])

    def save_app(self, app: dict, tenant_id: str | None = None) -> dict:
        resolved_tenant = tenant_id or self._tenant_id
        payload = self._to_app_row(app, resolved_tenant)
        result = self._client.table("apps").upsert(payload).execute()
        rows = result.data or []
        if rows:
            return self._from_app_row(rows[0])
        return self.get_app(app["id"], resolved_tenant) or app

    def delete_app(self, app_id: str, tenant_id: str | None = None) -> bool:
        resolved_tenant = tenant_id or self._tenant_id
        result = (
            self._client.table("apps")
            .delete()
            .eq("tenant_id", resolved_tenant)
            .eq("id", app_id)
            .execute()
        )
        return bool(result.data)

    def get_brand_memory(self, app_id: str) -> dict:
        result = (
            self._client.table("brand_memory")
            .select("*")
            .eq("app_id", app_id)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return {}

        row = rows[0]
        memory = row.get("memory") or {}
        memory.setdefault("app_id", app_id)
        memory.setdefault("niche", row.get("niche"))
        memory.setdefault("tone_of_voice", row.get("tone_of_voice"))
        memory.setdefault("creator_persona", row.get("creator_persona"))
        memory.setdefault("top_performing_hooks", row.get("top_performing_hooks") or [])
        memory.setdefault("avoided_topics", row.get("avoided_topics") or [])
        memory.setdefault("performance_history", row.get("performance_history") or {})
        memory.setdefault("learned_insights", row.get("learned_insights") or [])
        if row.get("updated_at"):
            memory.setdefault("last_updated", str(row["updated_at"])[:10])
        return memory

    def save_brand_memory(self, app_id: str, memory: dict) -> dict:
        payload = {
            "app_id": app_id,
            "niche": memory.get("niche"),
            "tone_of_voice": memory.get("tone_of_voice"),
            "creator_persona": memory.get("creator_persona"),
            "top_performing_hooks": memory.get("top_performing_hooks") or [],
            "avoided_topics": memory.get("avoided_topics") or [],
            "performance_history": memory.get("performance_history") or {},
            "learned_insights": memory.get("learned_insights") or [],
            "memory": memory,
        }
        self._client.table("brand_memory").upsert(payload).execute()
        return self.get_brand_memory(app_id)

    def delete_brand_memory(self, app_id: str) -> bool:
        try:
            result = self._client.table("brand_memory").delete().eq("app_id", app_id).execute()
            return bool(result.data)
        except Exception as exc:
            logger.warning(f"[SupabaseAppRepo] Verwijderen brand_memory mislukt voor {app_id}: {exc}")
            return False
