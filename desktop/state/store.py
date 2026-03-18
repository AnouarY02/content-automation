"""
Central State Store

ONTWERP:
  Één singleton Store object dat alle UI-relevante data bevat.
  Views lezen alleen uit de store — ze schrijven nooit direct.
  Store emits signals zodra data verandert → Views refreshen reactief.

  Dit voorkomt inconsistente UI-state waarbij twee views
  verschillende versies van dezelfde data tonen.

PATTERN:
  ApiWorker haalt data op → roept store.update_*() aan
  Store slaat op + emiteert changed signal
  Alle geabonneerde views ontvangen het signaal en refreshen zichzelf

THREAD-SAFETY:
  Store wordt alleen geüpdatet vanuit de main thread via Qt's signal/slot systeem.
  ApiWorker emiteert een "data_ready" signal → main thread roept store.update() aan.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class AppStore(QObject):
    """
    Centrale state store — singleton.
    Gebruik AppStore.instance() overal in de app.
    """

    # ── Signals (views abonneren hierop) ──
    active_app_changed     = pyqtSignal(str)        # app_id
    campaigns_updated      = pyqtSignal(list)        # lijst van campaign dicts
    pending_updated        = pyqtSignal(list)        # pending approval dicts
    analytics_updated      = pyqtSignal(dict)        # {app_id: summary_dict}
    health_updated         = pyqtSignal(dict)        # health snapshot dict
    alerts_updated         = pyqtSignal(list)        # lijst van alert dicts
    audit_updated          = pyqtSignal(list)        # lijst van audit entry dicts
    dead_letters_updated   = pyqtSignal(list)        # lijst van dead letter dicts
    apps_updated           = pyqtSignal(list)        # beschikbare apps
    backend_status_changed = pyqtSignal(bool)        # True=bereikbaar, False=down
    error_occurred         = pyqtSignal(str, str)    # (title, message)
    notification           = pyqtSignal(str, str)    # (type, message) voor statusbalk

    _instance: AppStore | None = None

    def __init__(self):
        super().__init__()

        # ── State data ──
        self.active_app_id: str = ""
        self.available_apps: list[dict] = []

        self.campaigns: list[dict] = []
        self.pending_campaigns: list[dict] = []

        self.analytics: dict = {}          # app_id → summary
        self.health_snapshot: dict = {}
        self.alerts: list[dict] = []
        self.audit_entries: list[dict] = []
        self.dead_letters: list[dict] = []

        self.backend_online: bool = False
        self.last_refresh: str = ""

    @classmethod
    def instance(cls) -> AppStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Update methoden (aanroepen vanuit main thread) ──

    def set_active_app(self, app_id: str) -> None:
        if self.active_app_id != app_id:
            self.active_app_id = app_id
            self.active_app_changed.emit(app_id)

    def update_apps(self, apps: list[dict]) -> None:
        self.available_apps = apps
        self.apps_updated.emit(apps)
        if apps and not self.active_app_id:
            self.set_active_app(apps[0]["id"])

    def update_campaigns(self, campaigns: list[dict]) -> None:
        self.campaigns = campaigns
        self.campaigns_updated.emit(campaigns)

    def update_pending(self, pending: list[dict]) -> None:
        self.pending_campaigns = pending
        self.pending_updated.emit(pending)

    def update_analytics(self, app_id: str, summary: dict) -> None:
        self.analytics[app_id] = summary
        self.analytics_updated.emit(self.analytics)

    def update_health(self, snapshot: dict) -> None:
        self.health_snapshot = snapshot
        self.health_updated.emit(snapshot)

    def update_alerts(self, alerts: list[dict]) -> None:
        self.alerts = alerts
        self.alerts_updated.emit(alerts)

    def update_audit(self, entries: list[dict]) -> None:
        self.audit_entries = entries
        self.audit_updated.emit(entries)

    def update_dead_letters(self, dl: list[dict]) -> None:
        self.dead_letters = dl
        self.dead_letters_updated.emit(dl)

    def set_backend_status(self, online: bool) -> None:
        if self.backend_online != online:
            self.backend_online = online
            self.backend_status_changed.emit(online)

    def report_error(self, title: str, message: str) -> None:
        self.error_occurred.emit(title, message)

    def notify(self, msg_type: str, message: str) -> None:
        """msg_type: 'info' | 'success' | 'warning' | 'error'"""
        self.notification.emit(msg_type, message)

    # ── Computed properties ──

    @property
    def pending_count(self) -> int:
        return len(self.pending_campaigns)

    @property
    def active_alert_count(self) -> int:
        return len([a for a in self.alerts if not a.get("resolved")])

    @property
    def overall_health(self) -> str:
        return self.health_snapshot.get("overall_status", "unknown")

    def get_analytics_for_active_app(self) -> dict:
        return self.analytics.get(self.active_app_id, {})

    # ── Convenience refresh triggers (roepen API aan en updaten store) ──

    def refresh_health(self) -> None:
        from workers.api_worker import run_api
        from api.health_api import HealthApi
        run_api(fn=HealthApi().snapshot, on_success=self.update_health)

    def refresh_alerts(self) -> None:
        from workers.api_worker import run_api
        from api.health_api import AlertsApi
        run_api(fn=AlertsApi().active, on_success=self.update_alerts)

    def refresh_audit(self) -> None:
        from workers.api_worker import run_api
        from api.health_api import AuditApi
        run_api(fn=AuditApi().recent, on_success=self.update_audit)

    def refresh_dead_letters(self) -> None:
        from workers.api_worker import run_api
        from api.health_api import DeadLetterApi
        run_api(fn=DeadLetterApi().list, on_success=self.update_dead_letters)

    def load_pending(self) -> None:
        from workers.api_worker import run_api
        from api.campaigns_api import ApprovalsApi
        run_api(fn=ApprovalsApi().pending, on_success=self.update_pending)

    def set_apps(self, apps: list) -> None:
        """Alias voor update_apps — gebruikt door settings view."""
        self.update_apps(apps)
