"""
Maturity View — Intern Volwassen Scorecard weergave.

LAYOUT:
  ┌──────────────────────────────────────────────────────────────────┐
  │  Maturity Scorecard  [App: ...]  [↻ Ververs]  [⚡ Recompute]     │
  ├──────────────────────────────────────────────────────────────────┤
  │  Score: 67.4 / 100   Status: ● VALIDATED   Updated: 2026-03-10  │
  ├─────────────────────────────┬────────────────────────────────────┤
  │ Metrics (score vs target)   │ History                            │
  │  replication   60 / 60      │  2026-03-10  67.4  VALIDATED       │
  │  prediction    72 / 65      │  2026-03-09  61.2  VALIDATED       │
  │  delta         58 / 55      │  ...                               │
  │  adoption      75 / 80      │                                    │
  │  stability     96 / 95      │                                    │
  ├─────────────────────────────┤                                    │
  │ Dimensie Replicatie         │                                    │
  │  dim | #exp | winner | cons │                                    │
  └─────────────────────────────┴────────────────────────────────────┘
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api

# Status kleuren
_STATUS_COLORS = {
    "intern_volwassen": "#22c55e",
    "validated":        "#f59e0b",
    "early":            "#94a3b8",
}
_STATUS_LABELS = {
    "intern_volwassen": "INTERN VOLWASSEN",
    "validated":        "VALIDATED",
    "early":            "EARLY",
}

# Score drempelkleuren (score vs target: groen als boven, rood als eronder)
def _score_color(score: float, target: float) -> str:
    if score >= target:
        return "#22c55e"
    if score >= target * 0.8:
        return "#f59e0b"
    return "#ef4444"


class MaturityView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store    = AppStore.instance()
        self._loading  = False
        self._setup_ui()
        self._store.active_app_changed.connect(self._on_app_changed)
        bus().refresh_requested.connect(self._load_data)

    # ── UI opbouw ──────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # ── Header ──────────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("Maturity Scorecard")
        title.setObjectName("viewTitle")
        header.addWidget(title)
        header.addStretch()

        self._refresh_btn = QPushButton("↻  Ververs")
        self._refresh_btn.setToolTip("Laad bestaande scorecard opnieuw")
        self._refresh_btn.clicked.connect(self._load_data)
        header.addWidget(self._refresh_btn)

        self._compute_btn = QPushButton("⚡  Recompute")
        self._compute_btn.setToolTip("Forceer herberekening van alle metrics")
        self._compute_btn.setObjectName("primaryButton")
        self._compute_btn.clicked.connect(self._recompute)
        header.addWidget(self._compute_btn)

        root.addLayout(header)

        # ── Score-banner ─────────────────────────────────────────────────
        banner = QHBoxLayout()
        banner.setSpacing(24)

        score_col = QVBoxLayout()
        score_lbl = QLabel("Score")
        score_lbl.setObjectName("metaLabel")
        self._score_value = QLabel("—")
        self._score_value.setObjectName("subTitle")
        self._score_value.setStyleSheet("font-size: 28px; font-weight: 700;")
        score_col.addWidget(score_lbl)
        score_col.addWidget(self._score_value)
        banner.addLayout(score_col)

        status_col = QVBoxLayout()
        status_lbl = QLabel("Status")
        status_lbl.setObjectName("metaLabel")
        self._status_badge = QLabel("—")
        self._status_badge.setObjectName("metaValue")
        self._status_badge.setStyleSheet(
            "font-size: 13px; font-weight: 600; padding: 4px 10px; "
            "border-radius: 4px; background: #1e2430;"
        )
        status_col.addWidget(status_lbl)
        status_col.addWidget(self._status_badge)
        banner.addLayout(status_col)

        updated_col = QVBoxLayout()
        updated_lbl = QLabel("Laatste berekening")
        updated_lbl.setObjectName("metaLabel")
        self._updated_label = QLabel("—")
        self._updated_label.setObjectName("metaValue")
        updated_col.addWidget(updated_lbl)
        updated_col.addWidget(self._updated_label)
        banner.addLayout(updated_col)

        self._state_label = QLabel("")
        self._state_label.setObjectName("metaLabel")
        banner.addStretch()
        banner.addWidget(self._state_label)

        root.addLayout(banner)

        # ── Splitter: links (metrics + dims) | rechts (history) ──────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Links ─────────────────────────────────────────────────────────
        left = QWidget()
        left_vbox = QVBoxLayout(left)
        left_vbox.setContentsMargins(0, 0, 8, 0)
        left_vbox.setSpacing(10)

        # Metrics tabel
        metrics_group = QGroupBox("Metrics — score vs target")
        metrics_vbox = QVBoxLayout(metrics_group)
        self._metrics_table = QTableWidget(0, 5)
        self._metrics_table.setObjectName("dataTable")
        self._metrics_table.setHorizontalHeaderLabels(
            ["Metric", "Score", "Target", "Δ", "n"]
        )
        self._metrics_table.horizontalHeader().setStretchLastSection(False)
        self._metrics_table.setColumnWidth(0, 160)
        self._metrics_table.setColumnWidth(1, 55)
        self._metrics_table.setColumnWidth(2, 55)
        self._metrics_table.setColumnWidth(3, 50)
        self._metrics_table.setColumnWidth(4, 40)
        self._metrics_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._metrics_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._metrics_table.setFixedHeight(180)
        metrics_vbox.addWidget(self._metrics_table)

        # Metric notes (voor geselecteerde rij)
        self._metric_notes = QLabel("")
        self._metric_notes.setObjectName("metaValue")
        self._metric_notes.setWordWrap(True)
        self._metric_notes.setStyleSheet("color: #94a3b8; font-size: 11px; padding-top: 4px;")
        metrics_vbox.addWidget(self._metric_notes)

        self._metrics_table.selectionModel().selectionChanged.connect(self._on_metric_selected)
        left_vbox.addWidget(metrics_group)

        # Dimensie replicatie tabel
        dims_group = QGroupBox("Dimensie Replicatie")
        dims_vbox = QVBoxLayout(dims_group)
        self._dims_table = QTableWidget(0, 6)
        self._dims_table.setObjectName("dataTable")
        self._dims_table.setHorizontalHeaderLabels(
            ["Dimensie", "#Exp", "Winner", "Cons.", "Conf.", "OK"]
        )
        self._dims_table.horizontalHeader().setStretchLastSection(False)
        self._dims_table.setColumnWidth(0, 130)
        self._dims_table.setColumnWidth(1, 40)
        self._dims_table.setColumnWidth(2, 110)
        self._dims_table.setColumnWidth(3, 50)
        self._dims_table.setColumnWidth(4, 50)
        self._dims_table.setColumnWidth(5, 30)
        self._dims_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        dims_vbox.addWidget(self._dims_table)
        left_vbox.addWidget(dims_group)

        splitter.addWidget(left)

        # ── Rechts: history ───────────────────────────────────────────────
        right = QWidget()
        right_vbox = QVBoxLayout(right)
        right_vbox.setContentsMargins(8, 0, 0, 0)

        history_group = QGroupBox("Geschiedenis")
        history_vbox = QVBoxLayout(history_group)
        self._history_table = QTableWidget(0, 3)
        self._history_table.setObjectName("dataTable")
        self._history_table.setHorizontalHeaderLabels(["Datum", "Score", "Status"])
        self._history_table.horizontalHeader().setStretchLastSection(True)
        self._history_table.setColumnWidth(0, 140)
        self._history_table.setColumnWidth(1, 55)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        history_vbox.addWidget(self._history_table)
        right_vbox.addWidget(history_group)

        splitter.addWidget(right)
        splitter.setSizes([560, 320])
        root.addWidget(splitter)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._load_data()

    def _on_app_changed(self, _app_id: str):
        self._clear_all()
        if self.isVisible():
            self._load_data()

    # ── Data laden ─────────────────────────────────────────────────────

    def _load_data(self):
        app_id = self._store.active_app_id
        if not app_id:
            self._set_state("Geen app geselecteerd.")
            return
        self._set_state("Laden…")
        self._set_buttons_enabled(False)
        from api.maturity import MaturityApi
        api = MaturityApi()
        run_api(
            fn=lambda: api.get_latest(app_id),
            on_success=self._render_scorecard,
            on_error=self._on_error,
            parent=self,
        )
        run_api(
            fn=lambda: api.get_history(app_id, limit=30),
            on_success=self._render_history,
            parent=self,
        )

    def _recompute(self):
        app_id = self._store.active_app_id
        if not app_id:
            return
        self._set_state("Herberekening loopt…")
        self._set_buttons_enabled(False)
        from api.maturity import MaturityApi
        run_api(
            fn=lambda: MaturityApi().recompute(app_id),
            on_success=lambda _: QTimer.singleShot(300, self._load_data),
            on_error=self._on_error,
            parent=self,
        )

    # ── Renderen ───────────────────────────────────────────────────────

    def _render_scorecard(self, data: dict):
        self._set_buttons_enabled(True)
        if not data:
            self._set_state("Geen scorecard beschikbaar. Druk op Recompute.")
            return
        self._set_state("")

        # Banner
        score  = data.get("maturity_score", 0.0)
        status = data.get("status", "early")
        ts     = (data.get("computed_at") or "")[:16].replace("T", " ")

        self._score_value.setText(f"{score:.1f} / 100")
        color = _STATUS_COLORS.get(status, "#94a3b8")
        label = _STATUS_LABELS.get(status, status.upper())
        self._score_value.setStyleSheet(
            f"font-size: 28px; font-weight: 700; color: {color};"
        )
        self._status_badge.setText(f"● {label}")
        self._status_badge.setStyleSheet(
            f"font-size: 13px; font-weight: 600; padding: 4px 10px; "
            f"border-radius: 4px; background: #1e2430; color: {color};"
        )
        self._updated_label.setText(ts or "—")

        # Metrics tabel
        metrics = data.get("metrics", [])
        self._metrics_table.setRowCount(0)
        for row, m in enumerate(metrics):
            self._metrics_table.insertRow(row)
            sc = float(m.get("score", 0))
            tg = float(m.get("target", 0))
            delta = sc - tg
            mc = _score_color(sc, tg)

            name_item = QTableWidgetItem(m.get("name", "—").replace("_", " "))
            name_item.setData(Qt.ItemDataRole.UserRole, m.get("notes", ""))
            sc_item = QTableWidgetItem(f"{sc:.1f}")
            sc_item.setForeground(QColor(mc))
            tg_item = QTableWidgetItem(f"{tg:.1f}")
            dt_item = QTableWidgetItem(f"{delta:+.1f}")
            dt_item.setForeground(QColor("#22c55e" if delta >= 0 else "#ef4444"))
            ev_item = QTableWidgetItem(str(m.get("evidence_count", 0)))
            ev_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            for col, item in enumerate([name_item, sc_item, tg_item, dt_item, ev_item]):
                if col in (1, 2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._metrics_table.setItem(row, col, item)

        # Dimension details
        dims = data.get("dimension_details", [])
        self._render_dimensions(dims)

    def _render_dimensions(self, dims: list):
        self._dims_table.setRowCount(0)
        if not dims:
            return
        for row, d in enumerate(dims):
            self._dims_table.insertRow(row)
            ok          = d.get("contributes_to_replication", False)
            winner      = (d.get("winner_value") or "—")[:12]
            consistency = d.get("winner_consistency", 0.0)
            confidence  = d.get("causal_confidence_avg", 0.0)
            exp_count   = d.get("experiment_count", 0)

            items = [
                QTableWidgetItem(d.get("dimension", "—")),
                QTableWidgetItem(str(exp_count)),
                QTableWidgetItem(winner),
                QTableWidgetItem(f"{consistency:.0%}" if consistency else "—"),
                QTableWidgetItem(f"{confidence:.0%}"  if confidence  else "—"),
                QTableWidgetItem("✓" if ok else "✗"),
            ]
            ok_color = "#22c55e" if ok else "#ef4444"
            items[5].setForeground(QColor(ok_color))
            items[5].setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            for col, item in enumerate(items):
                self._dims_table.setItem(row, col, item)

    def _render_history(self, history: list):
        self._history_table.setRowCount(0)
        if not isinstance(history, list):
            return
        for row, snap in enumerate(history[:50]):
            self._history_table.insertRow(row)
            ts     = (snap.get("saved_at") or "")[:16].replace("T", " ")
            score  = snap.get("maturity_score", 0.0)
            status = snap.get("status", "early")
            color  = _STATUS_COLORS.get(status, "#94a3b8")
            label  = _STATUS_LABELS.get(status, status.upper())

            ts_item  = QTableWidgetItem(ts or "—")
            sc_item  = QTableWidgetItem(f"{score:.1f}")
            sc_item.setForeground(QColor(color))
            st_item  = QTableWidgetItem(label)
            st_item.setForeground(QColor(color))

            for col, item in enumerate([ts_item, sc_item, st_item]):
                self._history_table.setItem(row, col, item)

    # ── State helpers ──────────────────────────────────────────────────

    def _on_error(self, msg: str):
        self._set_state(f"Fout: {msg}")
        self._set_buttons_enabled(True)

    def _set_state(self, msg: str):
        self._state_label.setText(msg)

    def _set_buttons_enabled(self, enabled: bool):
        self._refresh_btn.setEnabled(enabled)
        self._compute_btn.setEnabled(enabled)

    def _clear_all(self):
        self._score_value.setText("—")
        self._status_badge.setText("—")
        self._updated_label.setText("—")
        self._state_label.setText("")
        self._metrics_table.setRowCount(0)
        self._dims_table.setRowCount(0)
        self._history_table.setRowCount(0)
        self._metric_notes.setText("")

    def _on_metric_selected(self):
        rows = self._metrics_table.selectionModel().selectedRows()
        if not rows:
            self._metric_notes.setText("")
            return
        item = self._metrics_table.item(rows[0].row(), 0)
        notes = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self._metric_notes.setText(notes or "")
