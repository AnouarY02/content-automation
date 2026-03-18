"""
Health Checker — componentgezondheidsbewaking

CHECKS PER COMPONENT:
  tiktok_publisher    → POST naar TikTok auth endpoint (token validatie)
  tiktok_fetcher      → GET naar TikTok API, check response code
  openai_api          → GET /v1/models (key validatie, geen credits)
  anthropic_api       → GET /v1/models (key validatie, optioneel)
  elevenlabs          → GET /user endpoint (gratis, geen credits verbruikt)
  kling_provider      → GET /account endpoint
  runway_provider     → GET /tasks (lege list, geen credits)
  filesystem          → Schrijf + lees test-bestand in data/
  scheduler           → Check of APScheduler draait (PID file)

FREQUENTIE:
  filesystem + scheduler: elke minuut (goedkoop)
  externe APIs: elke 5 minuten (voorkomt rate limiting)
  volledige snapshot: elke 5 minuten

CACHING:
  Health resultaten worden gecached voor 4 minuten.
  Zo belast een /health API-call de externe providers niet bij elk request.

STATUS REGELS:
  HEALTHY   → laatste check succesvol, latency normaal
  DEGRADED  → check succesvol maar traag (> 2x normaal), of 1 recente fout
  UNHEALTHY → laatste check mislukt, of 3+ opeenvolgende fouten
  UNKNOWN   → nog niet gecheckt
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import httpx
from loguru import logger

from backend.constants import (
    ELEVENLABS_CHARS_WARN,
    HEALTH_ANTHROPIC_DEGRADED_MS,
    HEALTH_EXTERNAL_TIMEOUT_SEC,
    HEALTH_FS_DEGRADED_MS,
    HEALTH_TIKTOK_TIMEOUT_SEC,
)
HEALTH_OPENAI_DEGRADED_MS = HEALTH_ANTHROPIC_DEGRADED_MS  # zelfde drempel
from observability.models import (
    ComponentHealth,
    ComponentName,
    HealthSnapshot,
    HealthStatus,
)
from utils.file_io import atomic_write_text

ROOT = Path(__file__).parent.parent
HEALTH_DIR = ROOT / "data" / "health"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_LATEST = HEALTH_DIR / "latest.json"
HEALTH_HISTORY = HEALTH_DIR / "history.jsonl"

# Cache: vorige check resultaten + tijdstip
_cache: dict[str, tuple[ComponentHealth, datetime]] = {}
CACHE_TTL_SEC = 240   # 4 minuten


class HealthChecker:
    """
    Voert health checks uit op alle systeem-componenten.
    Resultaten worden gecached en opgeslagen.
    """

    def check_all(self, force: bool = False) -> HealthSnapshot:
        """
        Voer alle health checks uit en sla snapshot op.

        Args:
            force: Sla cache over en voer alle checks opnieuw uit

        Returns:
            HealthSnapshot met status van elk component
        """
        components = {}
        checks: list[tuple[ComponentName, Callable]] = [
            (ComponentName.FILESYSTEM,       self._check_filesystem),
            (ComponentName.OPENAI_API,       self._check_openai),
            (ComponentName.TIKTOK_PUBLISHER, self._check_tiktok_publisher),
            (ComponentName.TIKTOK_FETCHER,   self._check_tiktok_fetcher),
            (ComponentName.ELEVENLABS,       self._check_elevenlabs),
            (ComponentName.KLING_PROVIDER,   self._check_kling),
            (ComponentName.RUNWAY_PROVIDER,  self._check_runway),
            (ComponentName.SCHEDULER,        self._check_scheduler),
        ]

        for name, check_fn in checks:
            try:
                if not force:
                    cached = self._get_cached(name)
                    if cached:
                        components[name.value] = cached
                        continue
                result = check_fn()
                components[name.value] = result
                _cache[name.value] = (result, datetime.utcnow())
            except Exception as e:
                logger.warning(f"[HealthChecker] Check fout voor {name}: {e}")
                components[name.value] = ComponentHealth(
                    component=name,
                    status=HealthStatus.UNKNOWN,
                    error_message=f"Check zelf mislukt: {str(e)[:100]}",
                )

        snapshot = HealthSnapshot(components=components)
        snapshot.overall_status = snapshot.compute_overall()
        snapshot.healthy_count = sum(1 for c in components.values() if c.status == HealthStatus.HEALTHY)
        snapshot.degraded_count = sum(1 for c in components.values() if c.status == HealthStatus.DEGRADED)
        snapshot.unhealthy_count = sum(1 for c in components.values() if c.status == HealthStatus.UNHEALTHY)

        self._save_snapshot(snapshot)
        return snapshot

    def check_one(self, component: ComponentName) -> ComponentHealth:
        """Voer één specifieke health check uit."""
        check_map = {
            ComponentName.FILESYSTEM:       self._check_filesystem,
            ComponentName.OPENAI_API:       self._check_openai,
            ComponentName.ANTHROPIC_API:    self._check_anthropic,
            ComponentName.TIKTOK_PUBLISHER: self._check_tiktok_publisher,
            ComponentName.TIKTOK_FETCHER:   self._check_tiktok_fetcher,
            ComponentName.ELEVENLABS:       self._check_elevenlabs,
            ComponentName.KLING_PROVIDER:   self._check_kling,
            ComponentName.RUNWAY_PROVIDER:  self._check_runway,
            ComponentName.SCHEDULER:        self._check_scheduler,
        }
        fn = check_map.get(component)
        if not fn:
            return ComponentHealth(component=component, status=HealthStatus.UNKNOWN)
        return fn()

    def load_latest(self) -> HealthSnapshot | None:
        """Laad de meest recente health snapshot (gecached resultaat)."""
        if not HEALTH_LATEST.exists():
            return None
        with open(HEALTH_LATEST, encoding="utf-8") as f:
            return HealthSnapshot(**json.load(f))

    # ──────────────────────────────────────────────
    # INDIVIDUELE CHECKS
    # ──────────────────────────────────────────────

    def _check_filesystem(self) -> ComponentHealth:
        """Schrijf + verwijder een test-bestand om disk access te valideren."""
        test_path = HEALTH_DIR / "_health_test.tmp"
        start = time.monotonic()
        try:
            test_path.write_text("ok", encoding="utf-8")
            content = test_path.read_text(encoding="utf-8")
            test_path.unlink()
            latency = (time.monotonic() - start) * 1000
            assert content == "ok"
            return ComponentHealth(
                component=ComponentName.FILESYSTEM,
                status=HealthStatus.HEALTHY if latency < HEALTH_FS_DEGRADED_MS else HealthStatus.DEGRADED,
                latency_ms=latency,
                last_success=datetime.utcnow(),
                details={"test_path": str(test_path), "disk": str(ROOT)},
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.FILESYSTEM,
                status=HealthStatus.UNHEALTHY,
                error_message=str(e),
            )

    def _check_openai(self) -> ComponentHealth:
        """Valideer OpenAI API key via /v1/models (geen credits verbruikt)."""
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return ComponentHealth(
                component=ComponentName.OPENAI_API,
                status=HealthStatus.UNHEALTHY,
                error_message="OPENAI_API_KEY niet ingesteld",
            )

        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_EXTERNAL_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            latency = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                return ComponentHealth(
                    component=ComponentName.OPENAI_API,
                    status=HealthStatus.HEALTHY if latency < HEALTH_OPENAI_DEGRADED_MS else HealthStatus.DEGRADED,
                    latency_ms=latency,
                    last_success=datetime.utcnow(),
                    details={"status_code": 200},
                )
            elif resp.status_code == 401:
                return ComponentHealth(
                    component=ComponentName.OPENAI_API,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=latency,
                    error_message="Ongeldige API key (401)",
                )
            else:
                return ComponentHealth(
                    component=ComponentName.OPENAI_API,
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    error_message=f"HTTP {resp.status_code}",
                )
        except httpx.TimeoutException:
            return ComponentHealth(
                component=ComponentName.OPENAI_API,
                status=HealthStatus.DEGRADED,
                error_message="Timeout bij API check",
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.OPENAI_API,
                status=HealthStatus.UNHEALTHY,
                error_message=str(e)[:200],
            )

    def _check_anthropic(self) -> ComponentHealth:
        """Valideer Anthropic API key zonder credits te verbruiken."""
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return ComponentHealth(
                component=ComponentName.ANTHROPIC_API,
                status=HealthStatus.UNHEALTHY,
                error_message="ANTHROPIC_API_KEY niet ingesteld",
            )

        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_EXTERNAL_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
            latency = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                return ComponentHealth(
                    component=ComponentName.ANTHROPIC_API,
                    status=HealthStatus.HEALTHY if latency < HEALTH_ANTHROPIC_DEGRADED_MS else HealthStatus.DEGRADED,
                    latency_ms=latency,
                    last_success=datetime.utcnow(),
                    details={"status_code": 200},
                )
            elif resp.status_code == 401:
                return ComponentHealth(
                    component=ComponentName.ANTHROPIC_API,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=latency,
                    error_message="Ongeldige API key (401)",
                )
            else:
                return ComponentHealth(
                    component=ComponentName.ANTHROPIC_API,
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    error_message=f"HTTP {resp.status_code}",
                )
        except httpx.TimeoutException:
            return ComponentHealth(
                component=ComponentName.ANTHROPIC_API,
                status=HealthStatus.DEGRADED,
                error_message="Timeout bij API check",
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.ANTHROPIC_API,
                status=HealthStatus.UNHEALTHY,
                error_message=str(e)[:200],
            )

    def _check_tiktok_publisher(self) -> ComponentHealth:
        """Valideer TikTok access token via userinfo endpoint."""
        token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
        if not token:
            return ComponentHealth(
                component=ComponentName.TIKTOK_PUBLISHER,
                status=HealthStatus.DEGRADED,
                error_message="TIKTOK_ACCESS_TOKEN niet ingesteld — publisher niet beschikbaar",
            )
        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_TIKTOK_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://open.tiktokapis.com/v2/user/info/",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "open_id,display_name"},
                )
            latency = (time.monotonic() - start) * 1000
            if resp.status_code in (200, 403):  # 403 = token ok maar scope beperkt
                return ComponentHealth(
                    component=ComponentName.TIKTOK_PUBLISHER,
                    status=HealthStatus.HEALTHY,
                    latency_ms=latency,
                    last_success=datetime.utcnow(),
                    details={"token_present": True, "status_code": resp.status_code},
                )
            return ComponentHealth(
                component=ComponentName.TIKTOK_PUBLISHER,
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                error_message=f"HTTP {resp.status_code}",
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.TIKTOK_PUBLISHER,
                status=HealthStatus.UNHEALTHY,
                error_message=str(e)[:200],
            )

    def _check_tiktok_fetcher(self) -> ComponentHealth:
        """Zelfde check als publisher maar aparte component voor granulariteit."""
        result = self._check_tiktok_publisher()
        result.component = ComponentName.TIKTOK_FETCHER
        return result

    def _check_elevenlabs(self) -> ComponentHealth:
        """Valideer ElevenLabs API key via /voices endpoint (werkt met alle key-scopes)."""
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            return ComponentHealth(
                component=ComponentName.ELEVENLABS,
                status=HealthStatus.DEGRADED,
                error_message="ELEVENLABS_API_KEY niet ingesteld — voiceover niet beschikbaar",
            )
        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_TIKTOK_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": api_key},
                )
            latency = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                voices = resp.json().get("voices", [])
                return ComponentHealth(
                    component=ComponentName.ELEVENLABS,
                    status=HealthStatus.HEALTHY,
                    latency_ms=latency,
                    last_success=datetime.utcnow(),
                    details={"voices_available": len(voices)},
                )
            return ComponentHealth(
                component=ComponentName.ELEVENLABS,
                status=HealthStatus.UNHEALTHY,
                error_message=f"HTTP {resp.status_code}",
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.ELEVENLABS,
                status=HealthStatus.DEGRADED,
                error_message=str(e)[:200],
            )

    def _check_kling(self) -> ComponentHealth:
        """Check Kling AI API beschikbaarheid."""
        api_key = os.getenv("KLING_API_KEY", "")
        if not api_key:
            return ComponentHealth(
                component=ComponentName.KLING_PROVIDER,
                status=HealthStatus.DEGRADED,
                error_message="KLING_API_KEY niet ingesteld — Kling provider niet beschikbaar",
            )
        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_TIKTOK_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://api.klingai.com/v1/account",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            latency = (time.monotonic() - start) * 1000
            status = HealthStatus.HEALTHY if resp.status_code in (200, 403) else HealthStatus.DEGRADED
            return ComponentHealth(
                component=ComponentName.KLING_PROVIDER,
                status=status,
                latency_ms=latency,
                last_success=datetime.utcnow() if status == HealthStatus.HEALTHY else None,
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.KLING_PROVIDER,
                status=HealthStatus.DEGRADED,
                error_message=str(e)[:200],
            )

    def _check_runway(self) -> ComponentHealth:
        """Check Runway ML API beschikbaarheid."""
        api_key = os.getenv("RUNWAY_API_KEY", "")
        if not api_key:
            return ComponentHealth(
                component=ComponentName.RUNWAY_PROVIDER,
                status=HealthStatus.DEGRADED,
                error_message="RUNWAY_API_KEY niet ingesteld — Runway niet beschikbaar",
            )
        start = time.monotonic()
        try:
            with httpx.Client(timeout=HEALTH_TIKTOK_TIMEOUT_SEC) as client:
                resp = client.get(
                    "https://api.runwayml.com/v1/tasks",
                    headers={"Authorization": f"Bearer {api_key}", "X-Runway-Version": "2024-11-06"},
                )
            latency = (time.monotonic() - start) * 1000
            status = HealthStatus.HEALTHY if resp.status_code in (200, 403) else HealthStatus.DEGRADED
            return ComponentHealth(
                component=ComponentName.RUNWAY_PROVIDER,
                status=status,
                latency_ms=latency,
                last_success=datetime.utcnow() if status == HealthStatus.HEALTHY else None,
            )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.RUNWAY_PROVIDER,
                status=HealthStatus.DEGRADED,
                error_message=str(e)[:200],
            )

    def _check_scheduler(self) -> ComponentHealth:
        """
        Check of de scheduler actief is via PID-file.
        De scheduler schrijft zijn PID bij opstarten.
        """
        pid_file = ROOT / "data" / "health" / "scheduler.pid"
        if not pid_file.exists():
            return ComponentHealth(
                component=ComponentName.SCHEDULER,
                status=HealthStatus.DEGRADED,
                error_message="Scheduler PID-bestand niet gevonden — scheduler niet gestart?",
            )
        try:
            pid = int(pid_file.read_text().strip())
            # Check of process actief is (Windows-compatibel)
            import signal
            try:
                os.kill(pid, 0)
                return ComponentHealth(
                    component=ComponentName.SCHEDULER,
                    status=HealthStatus.HEALTHY,
                    last_success=datetime.utcnow(),
                    details={"pid": pid},
                )
            except (ProcessLookupError, PermissionError):
                return ComponentHealth(
                    component=ComponentName.SCHEDULER,
                    status=HealthStatus.UNHEALTHY,
                    error_message=f"Scheduler process {pid} niet actief",
                )
        except Exception as e:
            return ComponentHealth(
                component=ComponentName.SCHEDULER,
                status=HealthStatus.UNKNOWN,
                error_message=str(e)[:100],
            )

    # ──────────────────────────────────────────────
    # PERSISTENTIE
    # ──────────────────────────────────────────────

    def _save_snapshot(self, snapshot: HealthSnapshot) -> None:
        """Sla snapshot op als latest.json (atomisch) en append naar history.jsonl."""
        data = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, default=str)

        atomic_write_text(HEALTH_LATEST, data)

        # history.jsonl — append-only, geen atomic write nodig
        with open(HEALTH_HISTORY, "a", encoding="utf-8") as f:
            f.write(data + "\n")

    def _get_cached(self, component: ComponentName) -> ComponentHealth | None:
        """Geef gecachede resultaat terug als het nog geldig is."""
        cached = _cache.get(component.value)
        if not cached:
            return None
        result, cached_at = cached
        if (datetime.utcnow() - cached_at).total_seconds() < CACHE_TTL_SEC:
            return result
        return None


# Module-level singleton
_checker: HealthChecker | None = None


def get_health_checker() -> HealthChecker:
    global _checker
    if _checker is None:
        _checker = HealthChecker()
    return _checker
