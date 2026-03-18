"""
Campaigns View — volledige lijst met filter en detailpaneel.

LAYOUT:
  ┌─────────────────────────────────────────────────────┐
  │  Campaigns  [Alle ▼]  [🔍 Filter]   [▶ Nieuw]      │
  ├──────────────────────┬──────────────────────────────┤
  │ Tabel                │ Detail paneel                │
  │  ID | Status | Idee  │  Campaign ID:                │
  │  ...                 │  App: ...                    │
  │                      │  Status: ...                 │
  │                      │  Idee: ...                   │
  │                      │  [Naar Approval →]           │
  └──────────────────────┴──────────────────────────────┘
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api


class CampaignsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._all_campaigns: list[dict] = []
        self._setup_ui()
        self._store.campaigns_updated.connect(self._refresh)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Campaigns")
        title.setObjectName("viewTitle")
        header.addWidget(title)

        self._filter_combo = QComboBox()
        self._filter_combo.addItems(["Alle", "pending_approval", "published", "generating", "failed", "draft"])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        header.addWidget(QLabel("Status:"))
        header.addWidget(self._filter_combo)
        header.addStretch()

        new_btn = QPushButton("▶  Nieuwe campagne")
        new_btn.setObjectName("primaryButton")
        new_btn.clicked.connect(self._on_new_campaign)
        header.addWidget(new_btn)
        root.addLayout(header)

        # Splitter: tabel | detail
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Tabel
        self._table = QTableWidget(0, 5)
        self._table.setObjectName("dataTable")
        self._table.setHorizontalHeaderLabels(["ID", "Status", "App", "Idee", "Aangemaakt"])
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        splitter.addWidget(self._table)

        # Detail paneel
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(16, 0, 0, 0)

        detail_title = QLabel("Detail")
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setObjectName("detailText")
        self._detail_text.setPlaceholderText("Selecteer een campagne...")
        detail_layout.addWidget(self._detail_text)

        self._to_approval_btn = QPushButton("Naar Approval →")
        self._to_approval_btn.setObjectName("primaryButton")
        self._to_approval_btn.setEnabled(False)
        self._to_approval_btn.clicked.connect(self._go_to_approval)
        detail_layout.addWidget(self._to_approval_btn)

        splitter.addWidget(detail_widget)
        splitter.setSizes([550, 350])
        root.addWidget(splitter)

    def _refresh(self, campaigns: list):
        self._all_campaigns = campaigns
        self._apply_filter(self._filter_combo.currentText())

    def _apply_filter(self, status: str):
        filtered = self._all_campaigns
        if status != "Alle":
            filtered = [c for c in filtered if c.get("status") == status]

        # Filter op actieve app
        app_id = self._store.active_app_id
        if app_id:
            filtered = [c for c in filtered if c.get("app_id") == app_id]

        self._table.setRowCount(0)
        STATUS_COLORS = {
            "published": "#22c55e", "pending_approval": "#f59e0b",
            "failed": "#ef4444", "generating": "#6C63FF",
        }
        for row, c in enumerate(filtered[:100]):
            self._table.insertRow(row)
            status = c.get("status", "")
            color = STATUS_COLORS.get(status, "#94a3b8")
            for col, text in enumerate([
                c.get("id", "")[:12],
                status,
                c.get("app_id", ""),
                c.get("idea_title", "—"),
                str(c.get("created_at", ""))[:16],
            ]):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, c)
                if col == 1:
                    from PyQt6.QtGui import QColor
                    item.setForeground(QColor(color))
                self._table.setItem(row, col, item)

    def _on_selection(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._detail_text.clear()
            self._to_approval_btn.setEnabled(False)
            return
        item = self._table.item(rows[0].row(), 0)
        if not item:
            return
        campaign = item.data(Qt.ItemDataRole.UserRole)
        if not campaign:
            return

        lines = [
            f"Campaign ID:  {campaign.get('id', '—')}",
            f"App:          {campaign.get('app_id', '—')}",
            f"Status:       {campaign.get('status', '—')}",
            f"Idee:         {campaign.get('idea_title', '—')}",
            f"Kosten:       ${campaign.get('total_cost_usd', 0):.4f}",
            f"Video:        {campaign.get('video_path', 'Geen')}",
            f"Aangemaakt:   {str(campaign.get('created_at', '—'))[:19]}",
            f"Gepubliceerd: {str(campaign.get('published_at', '—'))[:19]}",
        ]
        self._detail_text.setPlainText("\n".join(lines))

        is_pending = campaign.get("status") == "pending_approval"
        self._to_approval_btn.setEnabled(is_pending)
        self._selected_campaign = campaign

    def _go_to_approval(self):
        if hasattr(self, "_selected_campaign"):
            bus().view_campaign.emit(self._selected_campaign.get("id", ""))
            bus().navigate_to.emit("approval")

    def _on_new_campaign(self):
        app_id = self._store.active_app_id
        if not app_id:
            return
        from api.campaigns_api import CampaignsApi
        run_api(
            fn=lambda: CampaignsApi().start(app_id),
            on_success=lambda _: self._store.notify("success", "Pipeline gestart"),
            parent=self,
        )
