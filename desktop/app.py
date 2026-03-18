"""
MainWindow — assembleert sidebar + content area + status bar.

STARTUP VOLGORDE:
  1. Laad QSS stylesheet
  2. Maak MainWindow aan (sidebar + stacked widget + status bar)
  3. Verbind store signals → sidebar badges
  4. Start PollWorker
  5. Laad initiële data (apps + eerste poll)
  6. Navigeer naar dashboard
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QMainWindow, QStackedWidget,
    QWidget,
)

from events.bus import bus
from state.settings import Settings
from state.store import AppStore
from widgets.sidebar import Sidebar
from widgets.status_bar import AppStatusBar
from workers.poll_worker import Workers

# Views — lazy import om startup te versnellen
from views.dashboard_view import DashboardView
from views.campaigns_view import CampaignsView
from views.approval_view import ApprovalView
from views.analytics_view import AnalyticsView
from views.observability_view import ObservabilityView
from views.scheduler_view import SchedulerView
from views.settings_view import SettingsView
from views.maturity_view import MaturityView


_VIEW_ORDER = [
    "dashboard",
    "campaigns",
    "approval",
    "analytics",
    "observability",
    "scheduler",
    "maturity",
    "settings",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._store = AppStore.instance()
        self._settings = Settings()
        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._start_workers()
        self._initial_load()

    def _setup_window(self):
        self.setWindowTitle("AY Marketing OS — Control Center")
        self.setMinimumSize(1100, 700)

        # Herstel venstergrootte/positie
        geom = self._settings.window_geometry
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(1280, 800)

        # Icon (optioneel)
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self._sidebar = Sidebar(self)
        main_layout.addWidget(self._sidebar)

        # Content area
        self._stack = QStackedWidget()
        main_layout.addWidget(self._stack)

        # Views aanmaken en toevoegen in vaste volgorde
        self._views: dict[str, QWidget] = {
            "dashboard":     DashboardView(self),
            "campaigns":     CampaignsView(self),
            "approval":      ApprovalView(self),
            "analytics":     AnalyticsView(self),
            "observability": ObservabilityView(self),
            "scheduler":     SchedulerView(self),
            "maturity":      MaturityView(self),
            "settings":      SettingsView(self),
        }
        for name in _VIEW_ORDER:
            self._stack.addWidget(self._views[name])

        # Status bar
        self._status_bar = AppStatusBar(self)
        self.setStatusBar(self._status_bar)

    def _connect_signals(self):
        # Navigatie
        bus().navigate_to.connect(self._navigate_to)
        bus().refresh_requested.connect(self._on_refresh_requested)
        bus().backend_url_changed.connect(self._on_backend_url_changed)

        # Store → sidebar badges
        self._store.pending_updated.connect(
            lambda lst: self._sidebar.set_pending_count(len(lst))
        )
        self._store.alerts_updated.connect(
            lambda lst: self._sidebar.set_alert_count(
                len([a for a in lst if a.get("status") not in ("acknowledged", "resolved")])
            )
        )

        # Status bar verbindt zichzelf met store in zijn eigen __init__
        # Approval beslissing → terug naar campaigns
        bus().approval_decision_made.connect(
            lambda _cid, _action: QTimer.singleShot(500, lambda: self._navigate_to("campaigns"))
        )

        # Active app veranderd → poll worker bijwerken
        self._store.active_app_changed.connect(self._on_active_app_changed)

    def _start_workers(self):
        interval = self._settings.poll_interval_sec
        self._workers_mgr = Workers.instance()
        poll = self._workers_mgr.start_polling(interval_sec=interval)

        # Verbind poll signals → store
        poll.campaigns_ready.connect(self._store.update_campaigns)
        poll.pending_ready.connect(self._store.update_pending)
        poll.health_ready.connect(self._store.update_health)
        poll.alerts_ready.connect(self._store.update_alerts)
        poll.audit_ready.connect(self._store.update_audit)
        poll.dead_letters_ready.connect(self._store.update_dead_letters)
        poll.analytics_ready.connect(self._store.update_analytics)
        poll.backend_status.connect(self._store.set_backend_status)

    def _initial_load(self):
        """Laad apps en start een directe poll na 500ms (geeft UI tijd te renderen)."""
        from workers.api_worker import run_api
        from api.campaigns_api import AppsApi

        run_api(
            fn=AppsApi().list,
            on_success=self._store.update_apps,
            parent=self,
        )

        # Herstel actieve app uit instellingen
        saved_app = self._settings.active_app_id
        if saved_app:
            self._store.set_active_app(saved_app)

        # Laad initiële data via aparte workers (niet-blokkerend)
        from api.campaigns_api import CampaignsApi, ApprovalsApi
        from api.health_api import HealthApi, AlertsApi
        run_api(fn=CampaignsApi().list, on_success=self._store.update_campaigns, parent=self)
        run_api(fn=ApprovalsApi().pending, on_success=self._store.update_pending, parent=self)
        run_api(fn=HealthApi().snapshot, on_success=self._store.update_health, parent=self)
        run_api(fn=AlertsApi().active, on_success=self._store.update_alerts, parent=self)

    def _navigate_to(self, view_name: str):
        if view_name in self._views:
            self._stack.setCurrentWidget(self._views[view_name])

    def _on_refresh_requested(self):
        if self._workers_mgr.poll:
            # Run poll in achtergrond (poll worker doet dit al in zijn eigen thread)
            pass

    def _on_backend_url_changed(self, url: str):
        from api.client import BackendClient
        BackendClient.instance()._base_url = url

    def _on_active_app_changed(self, app_id: str):
        if self._workers_mgr.poll:
            self._workers_mgr.poll.set_active_app(app_id)

    def closeEvent(self, event):
        # Sla venstergrootte op
        self._settings.window_geometry = self.saveGeometry()
        # Stop workers
        Workers.instance().stop_all()
        super().closeEvent(event)


def load_stylesheet(app: QApplication) -> None:
    """Laad het QSS bestand en pas toe op de applicatie."""
    qss_path = os.path.join(os.path.dirname(__file__), "assets", "styles", "main.qss")
    if os.path.exists(qss_path):
        with open(qss_path, encoding="utf-8") as f:
            app.setStyleSheet(f.read())
