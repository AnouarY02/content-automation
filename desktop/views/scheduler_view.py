"""
Scheduler View — geplande jobs overzicht en handmatig triggeren.

LAYOUT:
  ┌────────────────────────────────────────────────────────┐
  │  Scheduler  [Status: ●]  [↻ Ververs]                  │
  ├───────────────────────────────────────────────────────┤
  │ Actieve Jobs                                           │
  │  Job | Laatste run | Volgende run | Status | [▶ Run]  │
  │  ...                                                   │
  ├───────────────────────────────────────────────────────┤
  │ Recente uitvoeringen                                   │
  │  Tijd | Job | Duur | Uitkomst                          │
  └───────────────────────────────────────────────────────┘
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api


class SchedulerView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._setup_ui()
        bus().refresh_requested.connect(self._load_data)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Scheduler")
        title.setObjectName("viewTitle")
        header.addWidget(title)

        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet("color: #94a3b8; font-size: 16px;")
        header.addWidget(self._status_dot)

        self._status_label = QLabel("Status onbekend")
        self._status_label.setObjectName("metaValue")
        header.addWidget(self._status_label)

        header.addStretch()

        refresh_btn = QPushButton("↻  Ververs")
        refresh_btn.clicked.connect(self._load_data)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Jobs tabel
        jobs_group = QGroupBox("Geplande Jobs")
        jobs_vbox = QVBoxLayout(jobs_group)

        self._jobs_table = QTableWidget(0, 5)
        self._jobs_table.setObjectName("dataTable")
        self._jobs_table.setHorizontalHeaderLabels(
            ["Job naam", "Laatste run", "Volgende run", "Status", ""]
        )
        self._jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._jobs_table.setMaximumHeight(220)
        jobs_vbox.addWidget(self._jobs_table)
        root.addWidget(jobs_group)

        # Recente uitvoeringen
        history_group = QGroupBox("Recente Uitvoeringen")
        history_vbox = QVBoxLayout(history_group)

        self._history_table = QTableWidget(0, 4)
        self._history_table.setObjectName("dataTable")
        self._history_table.setHorizontalHeaderLabels(["Tijd", "Job", "Duur", "Uitkomst"])
        self._history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        history_vbox.addWidget(self._history_table)
        root.addWidget(history_group)

    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._load_data()

    def _load_data(self):
        from api.health_api import HealthApi
        run_api(
            fn=lambda: HealthApi().component("scheduler"),
            on_success=self._render_scheduler,
            parent=self,
        )
        # Scheduler jobs via audit trail (job_type filters)
        from api.health_api import AuditApi
        run_api(
            fn=lambda: AuditApi().recent(limit=30, job_type="scheduler"),
            on_success=self._render_history,
            parent=self,
        )

    def _render_scheduler(self, data: dict):
        if not data:
            return
        status = data.get("status", "unknown")
        color = {"healthy": "#22c55e", "degraded": "#f59e0b", "unhealthy": "#ef4444"}.get(
            status, "#94a3b8"
        )
        self._status_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        self._status_label.setText(f"Scheduler: {status}")

        jobs = data.get("jobs", [])
        self._jobs_table.setRowCount(0)
        for row, job in enumerate(jobs):
            self._jobs_table.insertRow(row)
            job_status = job.get("status", "—")
            js_color = "#22c55e" if job_status == "active" else "#f59e0b"

            cells = [
                job.get("name", "—"),
                str(job.get("last_run", "—"))[:19],
                str(job.get("next_run", "—"))[:19],
                job_status,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setForeground(QColor(js_color))
                self._jobs_table.setItem(row, col, item)

            trigger_btn = QPushButton("▶ Run")
            trigger_btn.setMaximumWidth(70)
            job_id = job.get("id", job.get("name", ""))
            trigger_btn.clicked.connect(
                lambda _, jid=job_id: self._trigger_job(jid)
            )
            self._jobs_table.setCellWidget(row, 4, trigger_btn)

    def _render_history(self, entries: list):
        self._history_table.setRowCount(0)
        if not entries:
            return

        OUTCOME_COLORS = {
            "success": "#22c55e",
            "failure": "#ef4444",
            "skipped": "#94a3b8",
        }
        for row, entry in enumerate(entries[:30]):
            self._history_table.insertRow(row)
            outcome = entry.get("outcome", "—")
            color = OUTCOME_COLORS.get(outcome, "#94a3b8")
            duration = entry.get("duration_ms")
            dur_text = f"{duration:.0f}ms" if duration is not None else "—"

            cells = [
                str(entry.get("timestamp", ""))[:16],
                entry.get("job_type", "—"),
                dur_text,
                outcome,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 3:
                    item.setForeground(QColor(color))
                self._history_table.setItem(row, col, item)

    def _trigger_job(self, job_id: str):
        from api.health_api import HealthApi
        run_api(
            fn=lambda: HealthApi().trigger_job(job_id),
            on_success=lambda _: self._store.notify("success", f"Job {job_id} gestart"),
            on_error=lambda e: self._store.notify("error", f"Trigger mislukt: {e}"),
            parent=self,
        )
