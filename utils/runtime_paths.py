import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP_ROOT = Path("/tmp/content-automation")


def is_vercel_runtime() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_writable_dir(path: Path, fallback: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return path
    except OSError:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def get_generated_assets_dir() -> Path:
    override = os.getenv("GENERATED_ASSETS_DIR", "").strip()
    if override:
      return Path(override)
    if is_vercel_runtime():
      return _TMP_ROOT / "assets" / "generated"
    return ROOT / "assets" / "generated"


def get_app_screenshots_dir() -> Path:
    override = os.getenv("APP_SCREENSHOTS_DIR", "").strip()
    if override:
        return Path(override)
    if is_vercel_runtime():
        return _TMP_ROOT / "assets" / "app_screenshots"
    return ROOT / "assets" / "app_screenshots"


def get_logs_dir() -> Path:
    override = os.getenv("LOGS_DIR", "").strip()
    if override:
        return Path(override)
    if is_vercel_runtime():
        return _TMP_ROOT / "logs"
    return ROOT / "logs"


def get_runtime_data_dir(*parts: str) -> Path:
    if is_vercel_runtime():
        base = _TMP_ROOT / "data"
    else:
        base = ROOT / "data"
    return base.joinpath(*parts)
