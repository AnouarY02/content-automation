"""
Centrale constanten — drempelwaarden, timeouts en limieten.

Pas hier aan; gebruik NOOIT magic numbers in businesslogica.
Importeer vanuit dit module: `from backend.constants import HEALTH_FS_DEGRADED_MS`

──────────────────────────────────────────────────────────
ENVIRONMENT FLAGS (stel in via .env of shell-omgeving):
──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY       — Verplicht. Anthropic API sleutel.
TIKTOK_ACCESS_TOKEN     — Optioneel. TikTok publisher + fetcher.
TIKTOK_CLIENT_KEY       — Optioneel. TikTok OAuth client key.
ELEVENLABS_API_KEY      — Optioneel. ElevenLabs voiceover.
ELEVENLABS_VOICE_ID     — Optioneel. ElevenLabs stem-ID (default: 21m00Tcm4TlvDq8ikWAM).
KLING_API_KEY           — Optioneel. Kling AI video provider.
RUNWAY_API_KEY          — Optioneel. Runway ML video provider.
DID_API_KEY             — Optioneel. D-ID avatar provider.
ALERT_WEBHOOK_URL       — Optioneel. Webhook voor kritieke alerts.
EXPERIMENTS_ENABLED     — "true" activeert variant-generatie in pipeline (default: false).
LOG_LEVEL               — Loguru level (default: INFO).
ENVIRONMENT             — development / staging / production (default: development).
"""

# ── Health check latency drempelwaarden (ms) ──────────────────────────

HEALTH_FS_DEGRADED_MS        = 500    # filesystem: boven dit → DEGRADED
HEALTH_ANTHROPIC_DEGRADED_MS = 3000   # Anthropic API: boven dit → DEGRADED
HEALTH_TIKTOK_TIMEOUT_SEC    = 10     # HTTP timeout voor TikTok checks
HEALTH_EXTERNAL_TIMEOUT_SEC  = 10     # HTTP timeout voor overige externe checks

# ── ElevenLabs quota drempel ───────────────────────────────────────────

ELEVENLABS_CHARS_WARN = 5_000   # onder dit aantal resterende tekens → DEGRADED

# ── Campaign pipeline ──────────────────────────────────────────────────

PIPELINE_DEFAULT_DURATION_SEC = 45   # standaard video-doelduur in seconden

# ── Experiment replication kwalificatie ───────────────────────────────

EXP_MIN_EXPERIMENTS    = 3     # min. geconcludeerde experimenten per dimensie
EXP_MIN_CONSISTENCY    = 0.67  # min. winner-consistency (67 %)
EXP_MIN_CONFIDENCE     = 0.70  # min. gemiddeld causal_confidence (70 %)
EXP_CONCLUDED_THRESHOLD = 2    # min. concludeerde experimenten voor variant selectie

# ── API limieten ──────────────────────────────────────────────────────

HISTORY_LIMIT_MAX  = 100   # max. items in history endpoints
CAMPAIGNS_LIST_MAX = 500   # max. campagnes in list endpoint
ANALYTICS_POSTS_MAX = 100  # max. posts in analytics/posts endpoint

# ── Authenticatie ─────────────────────────────────────────────────────

# Endpoints die geen auth vereisen (prefix-match)
AUTH_EXEMPT_PREFIXES = [
    "/",
    "/health",
    "/api/health",
]
