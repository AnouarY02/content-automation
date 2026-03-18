"""
Structured JSON Logger — AY Marketing OS

ONTWERP:
  - Loguru als backend (al in requirements.txt)
  - Alle logs gaan naar JSONL bestanden (line-delimited JSON)
  - Elk log-record bevat: timestamp, level, correlation_id, component, message, extra
  - Aparte sinks per log-type (system, errors, audit, scheduler)
  - Rotatie: per dag, max 30 dagen bewaren

LOG-LEVEL STRATEGIE:
  DEBUG    → ontwikkeling, API responses, timing details
  INFO     → normale operaties, start/stop van jobs
  SUCCESS  → taak voltooid (loguru-specifiek level)
  WARNING  → onverwachte situaties zonder impact (bijv. API fallback gebruikt)
  ERROR    → gefaalde operaties, retries getriggerd
  CRITICAL → systeem-brede problemen, dead letters, circuit breakers

VOORBEELD LOG-ENTRY (JSON):
{
  "timestamp": "2026-03-10T09:02:34.123Z",
  "level": "INFO",
  "correlation_id": "corr_app001_abc123_20260310_090000",
  "job_id": "job_x1y2z3",
  "component": "campaign_pipeline",
  "app_id": "app_001",
  "campaign_id": "abc123",
  "message": "Stap 3/6: Script schrijven gestart",
  "duration_ms": null,
  "extra": {"idea_title": "Freelancer time-saver", "video_type": "screen_demo"}
}
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from observability.correlation import get_correlation_id, get_job_id

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_configured = False


def _json_formatter(record: dict) -> str:
    """
    Formatteer een loguru record als JSON-string.
    Elke log-regel is één geldige JSON-object (JSONL formaat).
    """
    extra = record.get("extra", {})

    log_obj = {
        "timestamp": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": record["level"].name,
        "correlation_id": extra.get("correlation_id") or get_correlation_id() or "",
        "job_id": extra.get("job_id") or get_job_id() or "",
        "component": extra.get("component", ""),
        "app_id": extra.get("app_id", ""),
        "campaign_id": extra.get("campaign_id", ""),
        "message": record["message"],
        "duration_ms": extra.get("duration_ms"),
        "module": f"{record['name']}:{record['line']}",
        "extra": {
            k: v for k, v in extra.items()
            if k not in {"correlation_id", "job_id", "component", "app_id", "campaign_id", "duration_ms"}
        },
    }

    # Voeg exception info toe als aanwezig
    if record.get("exception"):
        exc = record["exception"]
        log_obj["exception"] = {
            "type": exc.type.__name__ if exc.type else None,
            "value": str(exc.value) if exc.value else None,
        }

    # Escape braces zodat loguru ze niet als format-placeholders interpreteert
    raw = json.dumps(log_obj, ensure_ascii=False, default=str)
    return raw.replace("{", "{{").replace("}", "}}") + "\n"


def _human_format(record: dict) -> str:
    """Human-readable formaat voor console output tijdens development."""
    extra = record.get("extra", {})
    cid = extra.get("correlation_id") or get_correlation_id()
    component = extra.get("component", record["name"].split(".")[-1])
    cid_part = f" [{cid[:20]}]" if cid else ""
    return (
        f"<green>{record['time'].strftime('%H:%M:%S')}</green> "
        f"<level>{record['level'].name:8}</level> "
        f"<cyan>{component:25}</cyan>"
        f"{cid_part} "
        f"{record['message']}\n"
    )


def setup_logging(
    log_level: str = "INFO",
    enable_console: bool = True,
    environment: str = "development",
) -> None:
    """
    Configureer het volledige logging systeem.
    Roep dit eenmalig aan bij startup van backend of scheduler.

    Args:
        log_level: Minimum log-level (DEBUG / INFO / WARNING / ERROR)
        enable_console: Toon logs ook in terminal
        environment: "development" → human format, "production" → JSON in console
    """
    global _configured
    if _configured:
        return

    logger.remove()  # Verwijder default loguru handler

    # ── Console sink ──
    if enable_console:
        if environment == "production":
            logger.add(sys.stdout, format=_json_formatter, level=log_level, colorize=False)
        else:
            logger.add(sys.stderr, format=_human_format, level=log_level, colorize=False)

    # ── System log (alle levels) ──
    logger.add(
        LOGS_DIR / "system.jsonl",
        format=_json_formatter,
        level="DEBUG",
        rotation="00:00",           # Nieuw bestand elke dag om middernacht
        retention="30 days",
        compression="gz",
        serialize=False,
        colorize=False,
        enqueue=True,               # Thread-safe
    )

    # ── Error log (WARNING en hoger) ──
    logger.add(
        LOGS_DIR / "errors.jsonl",
        format=_json_formatter,
        level="WARNING",
        rotation="00:00",
        retention="90 days",        # Errors langer bewaren
        compression="gz",
        colorize=False,
        enqueue=True,
    )

    # ── Scheduler log ──
    logger.add(
        LOGS_DIR / "scheduler.jsonl",
        format=_json_formatter,
        level="INFO",
        rotation="00:00",
        retention="14 days",
        colorize=False,
        filter=lambda r: "scheduler" in r["extra"].get("component", "").lower()
                         or "Scheduler" in r["message"],
        enqueue=True,
    )

    # ── Audit log (SUCCESS en INFO voor audit events) ──
    logger.add(
        LOGS_DIR / "audit.jsonl",
        format=_json_formatter,
        level="INFO",
        rotation="00:00",
        retention="365 days",       # Audit logs 1 jaar bewaren
        colorize=False,
        filter=lambda r: r["extra"].get("is_audit", False),
        enqueue=True,
    )

    _configured = True
    logger.info("Logging systeem geconfigureerd", extra={"component": "logger"})


def get_logger(component: str, **default_extra):
    """
    Geef een component-specifieke logger terug.

    Gebruik:
      log = get_logger("campaign_pipeline", app_id="app_001")
      log.info("Pipeline gestart")
      log.error("Stap mislukt", extra={"step": "video_generation"})
    """
    return logger.bind(component=component, **default_extra)


def log_operation(
    component: str,
    message: str,
    level: str = "INFO",
    duration_ms: float | None = None,
    is_audit: bool = False,
    **extra,
) -> None:
    """
    Log één operationeel event met alle standaard velden.
    Convenience wrapper voor veelgebruikte logging patronen.
    """
    bound = logger.bind(
        component=component,
        correlation_id=get_correlation_id(),
        job_id=get_job_id(),
        duration_ms=duration_ms,
        is_audit=is_audit,
        **extra,
    )
    getattr(bound, level.lower())(message)
