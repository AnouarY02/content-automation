import os

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
