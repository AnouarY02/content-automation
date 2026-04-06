"""
Rate Limiting Middleware — Sliding window per IP-adres.

Beschermt de API tegen:
  - Brute force aanvallen (auth, goedkeuring)
  - Misbruik van dure endpoints (campaign start, AI calls)
  - Denial-of-service door herhaald pollen

Limieten:
  - Standaard:    120 requests/minuut per IP
  - Zware routes: 10 requests/minuut per IP (/start, /generate-ideas)
  - Auth fouten:  5 pogingen/minuut per IP (automatisch geblokkeerd)

In productie: vervang door Redis-backed implementatie (bijv. redis-py + lua script)
voor horizontale schaalbaarheid.
"""

import time
import threading
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger


# Configuratie
_DEFAULT_LIMIT   = 120   # requests per venster
_HEAVY_LIMIT     = 10    # requests per venster voor zware routes
_WINDOW_SEC      = 60    # venstergrootte in seconden
_CLEANUP_EVERY   = 300   # ruim verouderde entries op elke 5 minuten

# Routes die een lagere limiet krijgen (case-insensitive prefix match)
_HEAVY_ROUTES = {
    "/api/campaigns/start",
    "/api/campaigns/generate-ideas",
    "/api/campaigns/voices/preview",
}

# Thread-safe state
_lock = threading.Lock()
_windows: dict[str, deque] = defaultdict(lambda: deque(maxlen=150))  # max 150 timestamps per IP
_last_cleanup = time.monotonic()


def _cleanup_old_entries():
    """Verwijder verouderde windows om geheugen te besparen."""
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < _CLEANUP_EVERY:
        return
    cutoff = now - _WINDOW_SEC
    keys_to_delete = []
    for key, dq in _windows.items():
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            keys_to_delete.append(key)
    for k in keys_to_delete:
        del _windows[k]
    _last_cleanup = now


def _get_client_ip(request: Request) -> str:
    """Haal het echte IP-adres op (respecteer X-Forwarded-For voor proxies)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Eerste IP in de keten is het originele client-IP
        ip = forwarded.split(",")[0].strip()
        # Valideer dat het er als een IP uitziet (rudimentair)
        if ip and len(ip) <= 45:
            return ip
    return request.client.host if request.client else "unknown"


def _is_heavy_route(path: str) -> bool:
    return any(path.startswith(r) for r in _HEAVY_ROUTES)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding window rate limiter als ASGI middleware.

    Onbeschermde routes (geen limiet):
      - GET /health  (liveness probe)
      - GET /        (root)
      - GET /static/ (statische bestanden)
      - GET /assets/ (video bestanden)
    """

    EXEMPT_PREFIXES = ("/health", "/static/", "/assets/", "/favicon")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Sla rate limiting over voor gezondheids- en statische routes
        if any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        ip   = _get_client_ip(request)
        key  = f"{ip}:{path}" if _is_heavy_route(path) else ip
        limit = _HEAVY_LIMIT if _is_heavy_route(path) else _DEFAULT_LIMIT
        now   = time.monotonic()

        with _lock:
            _cleanup_old_entries()
            dq = _windows[key]

            # Verwijder timestamps buiten het venster
            cutoff = now - _WINDOW_SEC
            while dq and dq[0] < cutoff:
                dq.popleft()

            current_count = len(dq)

            if current_count >= limit:
                logger.warning(
                    f"[RateLimit] GEBLOKKEERD — IP={ip} route={path} "
                    f"count={current_count}/{limit}"
                )
                retry_after = int(_WINDOW_SEC - (now - dq[0])) + 1
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Te veel verzoeken. Probeer het over {retry_after} seconden opnieuw.",
                        "retry_after_seconds": retry_after,
                    },
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Window": str(_WINDOW_SEC),
                    },
                )

            dq.append(now)
            remaining = limit - len(dq)

        response = await call_next(request)

        # Voeg rate limit headers toe aan elk antwoord
        response.headers["X-RateLimit-Limit"]     = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"]    = str(_WINDOW_SEC)

        return response
