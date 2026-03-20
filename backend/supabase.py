import os
from pathlib import Path
from urllib.parse import quote

import httpx

from supabase import Client, create_client


def _service_key() -> str:
    return (
        os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    )


def _service_url() -> str:
    return os.getenv("SUPABASE_URL", "").strip()


def _is_placeholder(value: str) -> bool:
    normalized = value.strip()
    return (
        not normalized
        or normalized == "..."
        or "xxxx.supabase.co" in normalized
        or "xxx.supabase.co" in normalized
    )


def has_supabase_env() -> bool:
    url = _service_url()
    key = _service_key()
    return not _is_placeholder(url) and not _is_placeholder(key)


def get_supabase_client() -> Client | None:
    if not has_supabase_env():
        return None

    return create_client(
        _service_url(),
        _service_key(),
    )


def get_public_storage_url(bucket: str, object_path: str) -> str:
    safe_path = quote(object_path.lstrip("/"), safe="/")
    return f"{_service_url()}/storage/v1/object/public/{bucket}/{safe_path}"


def upload_file_to_public_bucket(
    bucket: str,
    object_path: str,
    file_path: Path | str,
    *,
    content_type: str = "application/octet-stream",
) -> str:
    if not has_supabase_env():
        raise RuntimeError("Supabase env ontbreekt")

    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    safe_path = quote(object_path.lstrip("/"), safe="/")
    upload_url = f"{_service_url()}/storage/v1/object/{bucket}/{safe_path}"
    key = _service_key()

    with httpx.Client(timeout=120) as client:
        response = client.post(
            upload_url,
            headers={
                "Authorization": f"Bearer {key}",
                "apikey": key,
                "x-upsert": "true",
                "Content-Type": content_type,
            },
            content=file_path.read_bytes(),
        )
        response.raise_for_status()

    return get_public_storage_url(bucket, object_path)
