"""
Alerting Service

TRIGGER CONDITIES:
  CRITICAL → component UNHEALTHY + publisher/AI agents betrokken
           → dead letter voor publish-job
           → scheduler crasht
  HIGH     → dead letter voor video_generation of campaign_pipeline
           → API failure rate > 50% in laatste uur
           → ElevenLabs tegoed bijna op
  WARNING  → component DEGRADED
           → failure rate > 20% in laatste uur
           → dead letter voor analytics (niet kritiek)
  INFO     → eerste keer dat een component herstelt na UNHEALTHY

DEDUPLICATIE (spam preventie):
  Elke alert heeft een deduplication_key.
  Als dezelfde key al binnen SUPPRESSION_WINDOW minuten getriggerd is → stil.
  Na SUPPRESSION_WINDOW → mag opnieuw getriggerd worden.
  Hierdoor krijg je max 1 alert per component per N minuten.

ALERT DESTINATIONS (uitbreidbaar):
  1. File-based: data/alerts/alerts_{YYYY-MM}.jsonl (altijd actief)
  2. Webhook: POST naar ALERT_WEBHOOK_URL als ingesteld in .env
  3. Toekomst (fase 2): Slack, email, PagerDuty

VOORBEELD ALERT PAYLOAD:
{
  "alert_id": "alrt_u5v6w7x8",
  "severity": "high",
  "title": "Dead letter: video_generation voor app_001",
  "message": "Kling API timeout na 2 pogingen. Manual review vereist.",
  "component": "video_engine",
  "app_id": "app_001",
  "triggered_at": "2026-03-10T09:05:00Z",
  "deduplication_key": "video_generation_app_001_dead_letter"
}
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from observability.models import AlertRecord, Severity

ROOT = Path(__file__).parent.parent
ALERTS_DIR = ROOT / "data" / "alerts"
ALERTS_DIR.mkdir(parents=True, exist_ok=True)

# Suppressie per severity niveau
SUPPRESSION_WINDOWS: dict[Severity, int] = {
    Severity.DEBUG:    5,    # minuten
    Severity.INFO:     15,
    Severity.WARNING:  30,
    Severity.HIGH:     60,
    Severity.CRITICAL: 120,
}

# In-memory suppressie cache (persist over restarts via alert store)
_suppression_cache: dict[str, datetime] = {}


class AlertingService:
    """
    Verstuurt alerts via file-based opslag en optioneel webhook.
    Implementeert deduplicatie om spam te voorkomen.
    """

    def send(
        self,
        severity: Severity,
        title: str,
        message: str,
        component: str | None = None,
        app_id: str | None = None,
        campaign_id: str | None = None,
        correlation_id: str | None = None,
        deduplication_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AlertRecord | None:
        """
        Verstuur een alert.

        Returns:
            AlertRecord als het alert daadwerkelijk verstuurd is,
            None als het gesupprimeerd is (deduplicatie).
        """
        # Genereer deduplication key als niet opgegeven
        if not deduplication_key:
            deduplication_key = self._generate_dedup_key(severity, title, component, app_id)

        # Deduplicatie check
        if self._is_suppressed(deduplication_key, severity):
            logger.debug(
                f"[Alerting] Alert gesupprimeerd: {title}",
                extra={"component": "alerting"},
            )
            return None

        # Bepaal suppression window
        window_min = SUPPRESSION_WINDOWS.get(severity, 30)
        suppressed_until = datetime.utcnow() + timedelta(minutes=window_min)

        alert = AlertRecord(
            severity=severity,
            title=title,
            message=message,
            component=component,
            app_id=app_id,
            campaign_id=campaign_id,
            correlation_id=correlation_id,
            deduplication_key=deduplication_key,
            suppressed_until=suppressed_until,
            metadata=metadata or {},
        )

        # Sla op
        self._save_alert(alert)

        # Log op passend niveau
        log_method = {
            Severity.DEBUG:    logger.debug,
            Severity.INFO:     logger.info,
            Severity.WARNING:  logger.warning,
            Severity.HIGH:     logger.error,
            Severity.CRITICAL: logger.critical,
        }.get(severity, logger.warning)

        log_method(
            f"[ALERT {severity.upper()}] {title}: {message[:100]}",
            extra={"component": "alerting", "app_id": app_id, "alert_id": alert.alert_id},
        )

        # Registreer suppression
        _suppression_cache[deduplication_key] = suppressed_until

        # Stuur naar webhook als geconfigureerd
        self._send_webhook(alert)

        return alert

    def get_active_alerts(
        self,
        app_id: str | None = None,
        severity: Severity | None = None,
    ) -> list[AlertRecord]:
        """Haal niet-opgeloste alerts op voor de huidige maand."""
        alerts = self._load_current_month()
        if app_id:
            alerts = [a for a in alerts if a.app_id == app_id]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        return [a for a in alerts if not a.resolved]

    def acknowledge(self, alert_id: str) -> bool:
        """Markeer een alert als gezien."""
        return self._update_alert(alert_id, {"acknowledged": True})

    def resolve(self, alert_id: str) -> bool:
        """Markeer een alert als opgelost."""
        return self._update_alert(alert_id, {
            "resolved": True,
            "resolved_at": datetime.utcnow().isoformat(),
        })

    # ──────────────────────────────────────────────
    # WEBHOOK
    # ──────────────────────────────────────────────

    def _send_webhook(self, alert: AlertRecord) -> None:
        """
        Stuur alert naar externe webhook als ALERT_WEBHOOK_URL ingesteld is.
        Timeout van 5s — alert staat al in file, webhook is best-effort.
        """
        webhook_url = os.getenv("ALERT_WEBHOOK_URL", "")
        if not webhook_url:
            return

        payload = {
            "alert_id": alert.alert_id,
            "severity": alert.severity,
            "title": alert.title,
            "message": alert.message,
            "component": alert.component,
            "app_id": alert.app_id,
            "triggered_at": alert.triggered_at.isoformat(),
        }

        try:
            with httpx.Client(timeout=5) as client:
                resp = client.post(webhook_url, json=payload)
                if resp.status_code not in (200, 201, 202, 204):
                    logger.warning(f"[Alerting] Webhook HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"[Alerting] Webhook mislukt (niet kritiek): {e}")

    # ──────────────────────────────────────────────
    # DEDUPLICATIE
    # ──────────────────────────────────────────────

    def _is_suppressed(self, dedup_key: str, severity: Severity) -> bool:
        """Check of dit alert gesupprimeerd moet worden."""
        # In-memory check (snel)
        if dedup_key in _suppression_cache:
            if datetime.utcnow() < _suppression_cache[dedup_key]:
                return True
            else:
                del _suppression_cache[dedup_key]

        # Persistente check (voor restarts)
        recent = self._load_current_month()
        window_min = SUPPRESSION_WINDOWS.get(severity, 30)
        cutoff = datetime.utcnow() - timedelta(minutes=window_min)

        for alert in recent:
            if (
                alert.deduplication_key == dedup_key
                and alert.triggered_at > cutoff
                and not alert.resolved
            ):
                return True

        return False

    def _generate_dedup_key(
        self,
        severity: Severity,
        title: str,
        component: str | None,
        app_id: str | None,
    ) -> str:
        parts = [severity.value]
        if component:
            parts.append(component.replace(" ", "_")[:20])
        if app_id:
            parts.append(app_id)
        parts.append(title.replace(" ", "_")[:30].lower())
        return "_".join(parts)

    # ──────────────────────────────────────────────
    # PERSISTENTIE
    # ──────────────────────────────────────────────

    def _get_path(self) -> Path:
        ym = datetime.utcnow().strftime("%Y-%m")
        return ALERTS_DIR / f"alerts_{ym}.jsonl"

    def _save_alert(self, alert: AlertRecord) -> None:
        path = self._get_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n")

    def _load_current_month(self) -> list[AlertRecord]:
        path = self._get_path()
        if not path.exists():
            return []
        alerts = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        alerts.append(AlertRecord(**json.loads(line)))
                    except Exception:
                        pass
        return alerts

    def _update_alert(self, alert_id: str, updates: dict) -> bool:
        """Update een alert in het JSONL bestand (herschrijf hele bestand)."""
        path = self._get_path()
        if not path.exists():
            return False
        alerts = self._load_current_month()
        updated = False
        for alert in alerts:
            if alert.alert_id == alert_id:
                for key, value in updates.items():
                    setattr(alert, key, value)
                updated = True
        if updated:
            with open(path, "w", encoding="utf-8") as f:
                for alert in alerts:
                    f.write(json.dumps(alert.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n")
        return updated


# Module-level singleton
_alerting: AlertingService | None = None


def get_alerting_service() -> AlertingService:
    global _alerting
    if _alerting is None:
        _alerting = AlertingService()
    return _alerting
