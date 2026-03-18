"""
Dashboard View — overzicht voor de actieve app.

LAYOUT:
  ┌─────────────────────────────────────────────────────┐
  │  App: [Voorbeeld App]          [▶ Start campagne]   │
  ├──────────┬──────────┬──────────┬────────────────────┤
  │ Pending  │ Published│ Failure  │ Health              │
  │    3     │   47     │  2.1%    │ ● HEALTHY           │
  ├──────────┴──────────┴──────────┴────────────────────┤
  │ Actieve Alerts                                       │
  │  [HIGH] Dead letter: video_generation voor app_001  │
  ├─────────────────────────────────────────────────────┤
  │ Recente campagnes                                    │
  │  ID | Status | Idee | Score | Tijd                  │
  └─────────────────────────────────────────────────────┘
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from widgets.metric_card import HealthBadge, MetricCard
from workers.api_worker import run_api


class DashboardView(QWidget):
    """Hoofd-dashboard view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._setup_ui()
        self._connect_store()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # ── Header ──
        header = QHBoxLayout()
        self._app_label = QLabel("Dashboard")
        self._app_label.setObjectName("viewTitle")
        header.addWidget(self._app_label)
        header.addStretch()

        self._start_btn = QPushButton("▶  Nieuwe campagne")
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.clicked.connect(self._on_start_campaign)
        header.addWidget(self._start_btn)
        root.addLayout(header)

        # ── Metric kaarten ──
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)

        self._pending_card   = MetricCard("Wacht op goedkeuring", "pending approvals", "#f59e0b")
        self._published_card = MetricCard("Gepubliceerd",          "totaal posts",      "#22c55e")
        self._failure_card   = MetricCard("Failure rate",          "laatste 24u",       "#ef4444")
        self._alerts_card    = MetricCard("Actieve alerts",        "onopgelost",        "#ef4444")

        for card in [self._pending_card, self._published_card,
                     self._failure_card, self._alerts_card]:
            cards_layout.addWidget(card)

        # Health badge rechts
        health_frame = QFrame()
        health_frame.setObjectName("metricCard")
        health_frame.setMinimumSize(140, 100)
        hf_layout = QVBoxLayout(health_frame)
        hf_layout.addWidget(QLabel("Systeem Health"))
        hf_layout.addStretch()
        self._health_badge = HealthBadge()
        hf_layout.addWidget(self._health_badge)
        hf_layout.addStretch()
        cards_layout.addWidget(health_frame)

        root.addLayout(cards_layout)

        # ── Actieve alerts ──
        alerts_group = QGroupBox("Actieve Alerts")
        alerts_group.setObjectName("groupBox")
        alerts_layout = QVBoxLayout(alerts_group)
        self._alerts_label = QLabel("Geen actieve alerts.")
        self._alerts_label.setObjectName("infoLabel")
        self._alerts_label.setWordWrap(True)
        alerts_layout.addWidget(self._alerts_label)
        root.addWidget(alerts_group)

        # ── Recente campagnes tabel ──
        campaigns_group = QGroupBox("Recente Campagnes")
        campaigns_group.setObjectName("groupBox")
        table_layout = QVBoxLayout(campaigns_group)

        self._campaigns_table = QTableWidget(0, 5)
        self._campaigns_table.setObjectName("dataTable")
        self._campaigns_table.setHorizontalHeaderLabels(
            ["ID", "Status", "Idee", "Kosten", "Aangemaakt"]
        )
        self._campaigns_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._campaigns_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._campaigns_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._campaigns_table.doubleClicked.connect(self._on_campaign_double_click)
        table_layout.addWidget(self._campaigns_table)
        root.addWidget(campaigns_group)

    def _connect_store(self):
        store = self._store
        store.campaigns_updated.connect(self._refresh_campaigns)
        store.pending_updated.connect(self._refresh_pending)
        store.health_updated.connect(self._refresh_health)
        store.alerts_updated.connect(self._refresh_alerts)
        store.active_app_changed.connect(self._on_app_changed)

    # ── Refresh methoden ──

    def _on_app_changed(self, app_id: str):
        # Laad app naam
        apps = self._store.available_apps
        name = next((a.get("name", app_id) for a in apps if a.get("id") == app_id), app_id)
        self._app_label.setText(f"Dashboard — {name}")

    def _refresh_campaigns(self, campaigns: list):
        self._campaigns_table.setRowCount(0)
        # Toon alleen de actieve app's campagnes, max 15
        app_id = self._store.active_app_id
        filtered = [c for c in campaigns if not app_id or c.get("app_id") == app_id][:15]

        STATUS_COLORS = {
            "published":        "#22c55e",
            "pending_approval": "#f59e0b",
            "failed":           "#ef4444",
            "generating":       "#6C63FF",
        }

        for row, c in enumerate(filtered):
            self._campaigns_table.insertRow(row)
            status = c.get("status", "")
            color = STATUS_COLORS.get(status, "#94a3b8")

            cells = [
                c.get("id", "")[:12],
                status,
                c.get("idea_title", "—"),
                f"${c.get('total_cost_usd', 0):.3f}",
                str(c.get("created_at", ""))[:16],
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color))
                self._campaigns_table.setItem(row, col, item)

        # Update published count
        published = len([c for c in campaigns if c.get("status") == "published"])
        self._published_card.set_value(published)

    def _refresh_pending(self, pending: list):
        self._pending_card.set_value(len(pending))

    def _refresh_health(self, snapshot: dict):
        overall = snapshot.get("overall_status", "unknown")
        self._health_badge.set_status(overall)

        # Failure rate via health data
        # (wordt apart opgehaald — hier placeholder)

    def _refresh_alerts(self, alerts: list):
        active = [a for a in alerts if not a.get("resolved")]
        self._alerts_card.set_value(len(active))
        self._alerts_card.set_color("#ef4444" if active else "#22c55e")

        if active:
            lines = []
            for a in active[:3]:
                sev = a.get("severity", "").upper()
                lines.append(f"[{sev}] {a.get('title', '')}")
            if len(active) > 3:
                lines.append(f"... en {len(active) - 3} meer")
            self._alerts_label.setText("\n".join(lines))
            self._alerts_label.setStyleSheet("color: #ef4444;")
        else:
            self._alerts_label.setText("Geen actieve alerts.")
            self._alerts_label.setStyleSheet("color: #22c55e;")

    def _on_start_campaign(self):
        app_id = self._store.active_app_id
        if not app_id:
            self._store.report_error("Geen app", "Selecteer eerst een app in Settings.")
            return
        run_api(
            fn=lambda: __import__("api.campaigns_api", fromlist=["CampaignsApi"]).CampaignsApi().start(app_id),
            on_success=lambda _: self._store.notify("success", f"Campagne pipeline gestart voor {app_id}"),
            parent=self,
        )

    def _on_campaign_double_click(self, index):
        row = index.row()
        item = self._campaigns_table.item(row, 0)
        if item:
            bus().view_campaign.emit(item.text())
