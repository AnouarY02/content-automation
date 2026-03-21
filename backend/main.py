"""
FastAPI Backend — AY Marketing OS
Start met: uvicorn backend.main:app --reload --port 8000

SECURITY MODEL:
  - CORSMiddleware:        Beperkt cross-origin requests tot bekende origins
  - RateLimitMiddleware:   Sliding window rate limiting per IP (120 req/min standaard)
  - SecurityHeadersMiddleware: X-Frame-Options, X-Content-Type-Options, etc.
  - API Key Auth:          X-API-Key header, timing-safe vergelijking (zie auth.py)
  - Input sanitization:    ID-validatie in elke router om path traversal te voorkomen
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

load_dotenv(Path(__file__).parent.parent / ".env")

from observability.logger import setup_logging
setup_logging(
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    environment=os.getenv("ENVIRONMENT", "development"),
)

from backend.api import campaigns, approvals, analytics, apps as apps_router
from backend.api import costs as costs_router
from backend.api import health as health_router
from backend.api import experiments as experiments_router
from backend.api import maturity as maturity_router
from backend.api import settings as settings_router
from backend.auth import get_current_tenant
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.middleware.security_headers import SecurityHeadersMiddleware
from utils.runtime_paths import ensure_dir, get_generated_assets_dir


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AY Marketing OS Backend gestart", extra={"component": "backend"})
    yield
    logger.info("Backend afgesloten", extra={"component": "backend"})


app = FastAPI(
    title="AY Marketing OS API",
    version="1.0.0",
    description="AI Marketing Operating System — intern gebruik",
    lifespan=lifespan,
    # Verberg de OpenAPI docs in productie
    docs_url="/docs" if os.getenv("ENVIRONMENT", "development") != "production" else None,
    redoc_url=None,
)

# ── Middleware stack (order matters — laatste middleware wordt eerst uitgevoerd) ──

# 1. Security headers op alle responses
app.add_middleware(SecurityHeadersMiddleware)

# 2. Rate limiting per IP
app.add_middleware(RateLimitMiddleware)

# 3. CORS — alleen bekende origins mogen cross-origin requests sturen.
#    Let op: localhost:8000 MOET hier staan — het web dashboard draait
#    op dezelfde poort als de API en doet fetch() calls naar /api/*.
_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",   # Next.js dashboard
    "http://127.0.0.1:3000",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://localhost:8080",
    "https://content-automation-theta.vercel.app",  # Vercel frontend
]

# In productie: voeg je echte domein toe via env var
_EXTRA_ORIGIN = os.getenv("ALLOWED_ORIGIN", "")
if _EXTRA_ORIGIN:
    _ALLOWED_ORIGINS.append(_EXTRA_ORIGIN)

# Vercel: auto-detect deployment URL en voeg toe aan CORS
_VERCEL_URL = os.getenv("VERCEL_URL", "")
if _VERCEL_URL:
    _ALLOWED_ORIGINS.append(f"https://{_VERCEL_URL}")
_VERCEL_PROJECT = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "")
if _VERCEL_PROJECT:
    _ALLOWED_ORIGINS.append(f"https://{_VERCEL_PROJECT}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,  # Preflight cache: 10 minuten
)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(campaigns.router,          prefix="/api/campaigns",    tags=["Campagnes"])
app.include_router(approvals.router,          prefix="/api/approvals",    tags=["Goedkeuring"])
app.include_router(analytics.router,          prefix="/api/analytics",    tags=["Analytics"])
app.include_router(apps_router.router,        prefix="/api/apps",         tags=["Apps"])
app.include_router(health_router.router,      prefix="/api/health",       tags=["Reliability"])
app.include_router(experiments_router.router, prefix="/api/experiments",  tags=["Experimenten"])
app.include_router(maturity_router.router,    prefix="/api/maturity",     tags=["Maturity"])
app.include_router(costs_router.router,       prefix="/api/costs",        tags=["Kosten"])
app.include_router(settings_router.router,    prefix="/api/settings",     tags=["Instellingen"])

# ── Statische bestanden ───────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
ASSETS_DIR = get_generated_assets_dir()

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")

ASSETS_DIR = ensure_dir(ASSETS_DIR)
ensure_dir(ASSETS_DIR / "videos")
app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


# ── Dashboard & root routes ───────────────────────────────────────────

@app.get("/dashboard", include_in_schema=False)
def dashboard():
    """Serve het web dashboard."""
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return {"error": "Dashboard niet gevonden", "hint": "Controleer frontend/index.html"}
    return FileResponse(str(index), headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/", include_in_schema=False)
def root():
    """Root redirect naar dashboard."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard")


@app.get("/health", include_in_schema=False)
def health_simple():
    """Snelle liveness check zonder dependencies."""
    import datetime
    return {
        "status": "alive",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "version": "1.0.0",
    }
