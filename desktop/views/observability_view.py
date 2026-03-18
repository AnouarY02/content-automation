"""
Observability View — health, audit, alerts, dead letter queue.

LAYOUT:
  ┌──────────────────────────────────────────────────────────┐
  │  Observability  [↻ Ververs]                              │
  ├──────────────┬───────────────────────────────────────────┤
  │ Health       │ Alerts                                    │
  │  ○ fs: OK   │  [!] Component X failed since ...         │
  │  ○ ai: OK   │  [ACK] [RESOLVE]                          │
  │  ...        ├───────────────────────────────────────────┤
  │             │ Audit Trail (laatste 50)                  │
  │             │  Tijd | Job | App | Outcome               │
  │             ├───────────────────────────────────────────┤
  │             │ Dead Letter Queue                         │
  │             │  ID | Reden | [Resolve]                   │
  └──────────────┴───────────────────────────────────────────┘
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api


_HEALTH_COLORS = {
    "healthy": "#22c55e",
    "degraded": "#f59e0b",
    "unhealthy": "#ef4444",
    "unknown": "#94a3b8",
}

_SEV_COLORS = {
    "CRITICAL": "#ef4444",
    "HIGH":     "#f97316",
    "WARNING":  "#f59e0b",
    "INFO":     "#60a5fa",
    "DEBUG":    "#94a3b8",
}


class ObservabilityView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._setup_ui()

        self._store.health_updated.connect(self._render_health)
        self._store.alerts_updated.connect(self._render_alerts)
        self._store.audit_updated.connect(self._render_audit)
        self._store.dead_letters_updated.connect(self._render_dead_letters)
        bus().refresh_requested.connect(self._load_data)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Observability")
        title.setObjectName("viewTitle")
        header.addWidget(title)
        header.addStretch()

        self._failure_rate_label = QLabel()
        self._failure_rate_label.setObjectName("metaValue")
        header.addWidget(self._failure_rate_label)

        refresh_btn = QPushButton("↻  Ververs")
        refresh_btn.clicked.connect(self._load_data)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Links: health + DLQ
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        health_group = QGroupBox("Component Health")
        health_vbox = QVBoxLayout(health_group)
        self._health_scroll = QScrollArea()
        self._health_scroll.setWidgetResizable(True)
        self._health_container = QWidget()
        self._health_vbox = QVBoxLayout(self._health_container)
        self._health_vbox.setSpacing(4)
        self._health_vbox.addStretch()
        self._health_scroll.setWidget(self._health_container)
        health_vbox.addWidget(self._health_scroll)
        left_layout.addWidget(health_group, stretch=2)

        dlq_group = QGroupBox("Dead Letter Queue")
        dlq_vbox = QVBoxLayout(dlq_group)
        self._dlq_table = QTableWidget(0, 3)
        self._dlq_table.setObjectName("dataTable")
        self._dlq_table.setHorizontalHeaderLabels(["ID", "Reden", ""])
        self._dlq_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._dlq_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._dlq_table.setMaximumHeight(160)
        dlq_vbox.addWidget(self._dlq_table)
        left_layout.addWidget(dlq_group, stretch=1)

        splitter.addWidget(left)

        # Rechts: alerts + audit
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        alerts_group = QGroupBox("Actieve Alerts")
        alerts_vbox = QVBoxLayout(alerts_group)
        self._alerts_scroll = QScrollArea()
        self._alerts_scroll.setWidgetResizable(True)
        self._alerts_container = QWidget()
        self._alerts_vbox = QVBoxLayout(self._alerts_container)
        self._alerts_vbox.setSpacing(6)
        self._alerts_vbox.addStretch()
        self._alerts_scroll.setWidget(self._alerts_container)
        alerts_vbox.addWidget(self._alerts_scroll)
        right_layout.addWidget(alerts_group, stretch=1)

        audit_group = QGroupBox("Audit Trail (laatste 50)")
        audit_vbox = QVBoxLayout(audit_group)
        self._audit_table = QTableWidget(0, 5)
        self._audit_table.setObjectName("dataTable")
        self._audit_table.setHorizontalHeaderLabels(["Tijd", "Job", "App", "Uitkomst", "Duur"])
        self._audit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._audit_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        audit_vbox.addWidget(self._audit_table)
        right_layout.addWidget(audit_group, stretch=2)

        splitter.addWidget(right)
        splitter.setSizes([340, 560])
        root.addWidget(splitter)

    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._load_data()

    def _load_data(self):
        self._store.refresh_health()
        self._store.refresh_alerts()
        self._store.refresh_audit()
        self._store.refresh_dead_letters()

    # ------------------------------------------------------------------
    # Render health
    # ------------------------------------------------------------------

    def _render_health(self, snapshot: dict):
        while self._health_vbox.count() > 1:
            item = self._health_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        components = snapshot.get("components", {})
        overall = snapshot.get("overall_status", "unknown")
        overall_color = _HEALTH_COLORS.get(overall, "#94a3b8")

        overall_lbl = QLabel(f"Overall: {overall.upper()}")
        overall_lbl.setStyleSheet(f"color: {overall_color}; font-weight: bold;")
        self._health_vbox.insertWidget(0, overall_lbl)

        for i, (name, info) in enumerate(components.items()):
            status = info.get("status", "unknown")
            latency = info.get("latency_ms")
            color = _HEALTH_COLORS.get(status, "#94a3b8")

            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color};")
            row.addWidget(dot)

            label_text = f"{name}: {status}"
            if latency is not None:
                label_text += f"  ({latency:.0f}ms)"
            lbl = QLabel(label_text)
            lbl.setObjectName("healthRow")
            row.addWidget(lbl)
            row.addStretch()

            if info.get("error"):
                err_lbl = QLabel(info["error"][:60])
                err_lbl.setStyleSheet("color: #ef4444; font-size: 10px;")
                err_lbl.setWordWrap(True)
                row.addWidget(err_lbl)

            container = QWidget()
            container.setLayout(row)
            self._health_vbox.insertWidget(i + 1, container)

        # Failure rate ophalen
        from api.health_api import AuditApi
        run_api(
            fn=lambda: AuditApi().failure_rate(hours=24),
            on_success=self._update_failure_rate,
            parent=self,
        )

    def _update_failure_rate(self, data: dict):
        rate = data.get("failure_rate", 0) if data else 0
        color = "#ef4444" if rate > 0.1 else ("#f59e0b" if rate > 0.05 else "#22c55e")
        self._failure_rate_label.setText(f"Failure rate (24h): {rate:.1%}")
        self._failure_rate_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------
    # Render alerts
    # ------------------------------------------------------------------

    def _render_alerts(self, alerts: list):
        while self._alerts_vbox.count() > 1:
            item = self._alerts_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        active = [a for a in alerts if a.get("status") not in ("acknowledged", "resolved")]
        if not active:
            no_lbl = QLabel("Geen actieve alerts.")
            no_lbl.setObjectName("metaLabel")
            self._alerts_vbox.insertWidget(0, no_lbl)
            return

        for i, alert in enumerate(active[:30]):
            frame = self._build_alert_widget(alert)
            self._alerts_vbox.insertWidget(i, frame)

    def _build_alert_widget(self, alert: dict) -> QWidget:
        from PyQt6.QtWidgets import QFrame
        frame = QFrame()
        frame.setObjectName("alertCard")
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        layout.setSpacing(4)
        layout.setContentsMargins(8, 6, 8, 6)

        sev = alert.get("severity", "INFO")
        color = _SEV_COLORS.get(sev, "#94a3b8")

        top = QHBoxLayout()
        sev_lbl = QLabel(f"[{sev}]")
        sev_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
        top.addWidget(sev_lbl)

        comp_lbl = QLabel(alert.get("component", "—"))
        comp_lbl.setObjectName("metaValue")
        top.addWidget(comp_lbl)
        top.addStretch()

        time_lbl = QLabel(str(alert.get("created_at", ""))[:16])
        time_lbl.setObjectName("metaLabel")
        top.addWidget(time_lbl)
        layout.addLayout(top)

        msg_lbl = QLabel(alert.get("message", "—"))
        msg_lbl.setWordWrap(True)
        layout.addWidget(msg_lbl)

        btn_row = QHBoxLayout()
        alert_id = alert.get("id", "")

        ack_btn = QPushButton("ACK")
        ack_btn.setMaximumWidth(60)
        ack_btn.clicked.connect(lambda _, aid=alert_id: self._ack_alert(aid))
        btn_row.addWidget(ack_btn)

        res_btn = QPushButton("RESOLVE")
        res_btn.setMaximumWidth(80)
        res_btn.clicked.connect(lambda _, aid=alert_id: self._resolve_alert(aid))
        btn_row.addWidget(res_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        return frame

    def _ack_alert(self, alert_id: str):
        bus().alert_acknowledge.emit(alert_id)
        from api.health_api import AlertsApi
        run_api(
            fn=lambda: AlertsApi().acknowledge(alert_id),
            on_success=lambda _: self._load_data(),
            parent=self,
        )

    def _resolve_alert(self, alert_id: str):
        bus().alert_resolve.emit(alert_id)
        from api.health_api import AlertsApi
        run_api(
            fn=lambda: AlertsApi().resolve(alert_id),
            on_success=lambda _: self._load_data(),
            parent=self,
        )

    # ------------------------------------------------------------------
    # Render audit
    # ------------------------------------------------------------------

    def _render_audit(self, entries: list):
        self._audit_table.setRowCount(0)
        if not entries:
            return

        OUTCOME_COLORS = {
            "success": "#22c55e",
            "failure": "#ef4444",
            "skipped": "#94a3b8",
        }

        for row, entry in enumerate(entries[:50]):
            self._audit_table.insertRow(row)
            outcome = entry.get("outcome", "—")
            color = OUTCOME_COLORS.get(outcome, "#94a3b8")

            duration = entry.get("duration_ms")
            dur_text = f"{duration:.0f}ms" if duration is not None else "—"

            cells = [
                str(entry.get("timestamp", ""))[:16],
                entry.get("job_type", "—"),
                entry.get("app_id", "—"),
                outcome,
                dur_text,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setForeground(QColor(color))
                self._audit_table.setItem(row, col, item)

    # ------------------------------------------------------------------
    # Render DLQ
    # ------------------------------------------------------------------

    def _render_dead_letters(self, entries: list):
        self._dlq_table.setRowCount(0)
        if not entries:
            return

        for row, entry in enumerate(entries[:50]):
            self._dlq_table.insertRow(row)
            dl_id = entry.get("id", "—")

            self._dlq_table.setItem(row, 0, QTableWidgetItem(dl_id[:14]))
            reason_item = QTableWidgetItem(entry.get("reason", "—"))
            reason_item.setForeground(QColor("#f59e0b"))
            self._dlq_table.setItem(row, 1, reason_item)

            resolve_btn = QPushButton("Resolve")
            resolve_btn.setMaximumWidth(80)
            resolve_btn.clicked.connect(lambda _, did=dl_id: self._resolve_dlq(did))
            self._dlq_table.setCellWidget(row, 2, resolve_btn)

    def _resolve_dlq(self, dl_id: str):
        reply = QMessageBox.question(
            self,
            "Dead Letter Resolve",
            f"Dead letter {dl_id[:14]} als opgelost markeren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from api.health_api import DeadLetterApi
        run_api(
            fn=lambda: DeadLetterApi().resolve(dl_id, resolution="resolved via desktop app"),
            on_success=lambda _: self._store.refresh_dead_letters(),
            parent=self,
        )
