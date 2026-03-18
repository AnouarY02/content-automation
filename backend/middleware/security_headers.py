"""
Security Headers Middleware.

Voegt standaard security response headers toe aan elke API respons
om veelvoorkomende web-aanvallen te blokkeren.

Headers die worden toegevoegd:
  X-Content-Type-Options    — Voorkomt MIME-type sniffing
  X-Frame-Options           — Voorkomt clickjacking (iframes)
  X-XSS-Protection          — Legacy XSS-filter (browsers)
  Referrer-Policy           — Beperkt referrer-lek bij externe links
  Permissions-Policy        — Blokkeert camera, microfoon, locatie
  Cache-Control             — Voorkomt caching van API-responses
  X-Request-ID              — Uniek request ID voor audit trail

HSTS (Strict-Transport-Security) wordt alleen toegevoegd in productie
omdat development geen HTTPS heeft.

Opmerking: CSP (Content-Security-Policy) is weggelaten voor de dashboard
route omdat de frontend inline scripts en externe CDN's gebruikt.
Een volledige CSP vereist een redesign met nonces (server-side rendering).
"""

import uuid
import os
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


# Routes die we als "API" beschouwen (geen HTML) — strikte caching headers
_API_PREFIXES = ("/api/",)

# Routes die HTML serveren — iets soepelere headers
_DASHBOARD_PATHS = ("/dashboard", "/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Voegt security headers toe aan elke response."""

    IS_PRODUCTION = os.getenv("ENVIRONMENT", "development") == "production"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Genereer uniek request ID voor traceerbaarheid
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)

        # ── Universele security headers ──────────────────────────
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]         = "DENY"
        response.headers["X-XSS-Protection"]        = "1; mode=block"
        response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
        response.headers["X-Request-ID"]            = request_id
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )

        # ── API-specifieke headers ────────────────────────────────
        if any(path.startswith(p) for p in _API_PREFIXES):
            # API responses mogen nooit gecachet worden (bevatten dynamische data)
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"]        = "no-cache"

        # ── HSTS: alleen in productie (HTTPS vereist) ────────────
        if self.IS_PRODUCTION:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        # ── Server header verwijderen (lekt framework info) ──────
        if "server" in response.headers:
            del response.headers["server"]
        if "x-powered-by" in response.headers:
            del response.headers["x-powered-by"]

        return response
