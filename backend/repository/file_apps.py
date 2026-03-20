from __future__ import annotations

import json
from pathlib import Path

from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_writable_dir, get_runtime_data_dir


ROOT = Path(__file__).parent.parent.parent
SOURCE_REGISTRY_PATH = ROOT / "configs" / "app_registry.json"
SOURCE_BRAND_MEMORY_DIR = ROOT / "data" / "brand_memory"


def _registry_dir() -> Path:
    return ensure_writable_dir(ROOT / "configs", get_runtime_data_dir("configs"))


def _registry_path() -> Path:
    target = _registry_dir() / "app_registry.json"
    if target != SOURCE_REGISTRY_PATH and not target.exists() and SOURCE_REGISTRY_PATH.exists():
        atomic_write_json(target, json.loads(SOURCE_REGISTRY_PATH.read_text(encoding="utf-8")))
    return target


def _brand_memory_dir() -> Path:
    return ensure_writable_dir(SOURCE_BRAND_MEMORY_DIR, get_runtime_data_dir("brand_memory"))


def _brand_memory_path(app_id: str) -> Path:
    target = _brand_memory_dir() / f"{app_id}.json"
    source = SOURCE_BRAND_MEMORY_DIR / f"{app_id}.json"
    if target != source and not target.exists() and source.exists():
        atomic_write_json(target, json.loads(source.read_text(encoding="utf-8")))
    return target


class FileAppRepository:
    def __init__(self, tenant_id: str = "default") -> None:
        self._tenant_id = tenant_id

    def _load_registry(self) -> dict:
        path = _registry_path()
        if not path.exists():
            return {"apps": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_registry(self, data: dict) -> None:
        atomic_write_json(_registry_path(), data)

    def list_apps(self, tenant_id: str | None = None) -> list[dict]:
        resolved_tenant = tenant_id or self._tenant_id
        apps = self._load_registry().get("apps", [])
        return [app for app in apps if app.get("tenant_id", "default") == resolved_tenant]

    def get_app(self, app_id: str, tenant_id: str | None = None) -> dict | None:
        resolved_tenant = tenant_id or self._tenant_id
        for app in self._load_registry().get("apps", []):
            if app.get("id") == app_id and app.get("tenant_id", "default") == resolved_tenant:
                return app
        return None

    def save_app(self, app: dict, tenant_id: str | None = None) -> dict:
        resolved_tenant = tenant_id or self._tenant_id
        registry = self._load_registry()
        app = dict(app)
        app["tenant_id"] = app.get("tenant_id", resolved_tenant)

        updated = False
        for idx, existing in enumerate(registry.get("apps", [])):
            if existing.get("id") == app.get("id"):
                registry["apps"][idx] = app
                updated = True
                break
        if not updated:
            registry.setdefault("apps", []).append(app)

        self._save_registry(registry)
        return app

    def delete_app(self, app_id: str, tenant_id: str | None = None) -> bool:
        resolved_tenant = tenant_id or self._tenant_id
        registry = self._load_registry()
        original_len = len(registry.get("apps", []))
        registry["apps"] = [
            app
            for app in registry.get("apps", [])
            if not (app.get("id") == app_id and app.get("tenant_id", "default") == resolved_tenant)
        ]
        if len(registry["apps"]) == original_len:
            return False
        self._save_registry(registry)
        return True

    def get_brand_memory(self, app_id: str) -> dict:
        path = _brand_memory_path(app_id)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_brand_memory(self, app_id: str, memory: dict) -> dict:
        atomic_write_json(_brand_memory_path(app_id), memory)
        return memory

    def delete_brand_memory(self, app_id: str) -> bool:
        path = _brand_memory_path(app_id)
        if not path.exists():
            return False
        path.unlink()
        return True
