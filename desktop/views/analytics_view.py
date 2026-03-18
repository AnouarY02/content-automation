"""
Analytics View — performance scores, trends en learnings.

LAYOUT:
  ┌─────────────────────────────────────────────────────┐
  │  Analytics  [App: ...]  [▶ Ververs]                 │
  ├────────────────────┬────────────────────────────────┤
  │ Score per campagne │ High-confidence learnings       │
  │  ID | Score | Trend│  ⊕ Hook: ...                   │
  │  ...               │  ⊕ ...                         │
  │                    ├────────────────────────────────┤
  │                    │ Experiment tags                 │
  │                    │  #hook_test #cta_test ...       │
  └────────────────────┴────────────────────────────────┘
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api


class AnalyticsView(QWidget):
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
        title = QLabel("Analytics")
        title.setObjectName("viewTitle")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("↻  Ververs")
        refresh_btn.clicked.connect(self._load_data)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Links: score tabel
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)

        scores_group = QGroupBox("Performance Scores")
        scores_vbox = QVBoxLayout(scores_group)

        self._scores_table = QTableWidget(0, 4)
        self._scores_table.setObjectName("dataTable")
        self._scores_table.setHorizontalHeaderLabels(["Campaign ID", "Score", "Views", "Status"])
        self._scores_table.horizontalHeader().setStretchLastSection(True)
        self._scores_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._scores_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        scores_vbox.addWidget(self._scores_table)
        left_layout.addWidget(scores_group)

        # Benchmark
        bench_group = QGroupBox("Benchmark")
        bench_vbox = QVBoxLayout(bench_group)
        self._benchmark_label = QLabel("Geen data beschikbaar.")
        self._benchmark_label.setWordWrap(True)
        self._benchmark_label.setObjectName("metaValue")
        bench_vbox.addWidget(self._benchmark_label)
        left_layout.addWidget(bench_group)

        splitter.addWidget(left)

        # Rechts: learnings + tags
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        learnings_group = QGroupBox("High-Confidence Learnings")
        learnings_vbox = QVBoxLayout(learnings_group)

        self._learnings_scroll = QScrollArea()
        self._learnings_scroll.setWidgetResizable(True)
        self._learnings_container = QWidget()
        self._learnings_vbox = QVBoxLayout(self._learnings_container)
        self._learnings_vbox.setSpacing(6)
        self._learnings_vbox.addStretch()
        self._learnings_scroll.setWidget(self._learnings_container)
        learnings_vbox.addWidget(self._learnings_scroll)
        right_layout.addWidget(learnings_group, stretch=2)

        tags_group = QGroupBox("Experiment Tags (recente campagnes)")
        tags_vbox = QVBoxLayout(tags_group)
        self._tags_label = QLabel("—")
        self._tags_label.setWordWrap(True)
        self._tags_label.setObjectName("hashtagsLabel")
        tags_vbox.addWidget(self._tags_label)
        right_layout.addWidget(tags_group, stretch=1)

        # Detail van geselecteerd campagne
        detail_group = QGroupBox("Geselecteerd Post Detail")
        detail_vbox = QVBoxLayout(detail_group)
        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setObjectName("detailText")
        self._detail_text.setPlaceholderText("Selecteer een campagne voor details...")
        self._detail_text.setMaximumHeight(160)
        detail_vbox.addWidget(self._detail_text)
        right_layout.addWidget(detail_group)

        splitter.addWidget(right)
        splitter.setSizes([520, 380])
        root.addWidget(splitter)

        self._scores_table.selectionModel().selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._load_data()

    def _load_data(self):
        app_id = self._store.active_app_id
        if not app_id:
            return
        from api.campaigns_api import AnalyticsApi
        run_api(
            fn=lambda: AnalyticsApi().summary(app_id),
            on_success=self._render_summary,
            parent=self,
        )
        run_api(
            fn=lambda: AnalyticsApi().posts(app_id),
            on_success=self._render_posts,
            parent=self,
        )

    def _render_summary(self, data: dict):
        if not data:
            return
        benchmark = data.get("benchmark", {})
        learnings = data.get("learnings", [])
        tags = data.get("experiment_tags", [])

        # Benchmark
        if benchmark:
            lines = [
                f"Gem. score:  {benchmark.get('avg_score', 0):.1f}",
                f"Beste:       {benchmark.get('best_score', 0):.1f}",
                f"Slechtste:   {benchmark.get('worst_score', 0):.1f}",
                f"Sample size: {benchmark.get('sample_size', 0)}",
            ]
            self._benchmark_label.setText("\n".join(lines))

        # Learnings
        while self._learnings_vbox.count() > 1:
            item = self._learnings_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, learning in enumerate(learnings[:20]):
            direction = learning.get("direction", "neutral")
            icon = "⊕" if direction == "positive" else ("⊖" if direction == "negative" else "○")
            text = f"{icon} [{learning.get('confidence', 0):.0%}] {learning.get('insight', '—')}"
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            color = "#22c55e" if direction == "positive" else (
                "#ef4444" if direction == "negative" else "#94a3b8"
            )
            lbl.setStyleSheet(f"color: {color}; padding: 2px 0;")
            self._learnings_vbox.insertWidget(i, lbl)

        if not learnings:
            no_lbl = QLabel("Geen learnings beschikbaar.")
            no_lbl.setObjectName("metaLabel")
            self._learnings_vbox.insertWidget(0, no_lbl)

        # Tags
        self._tags_label.setText(" ".join(f"#{t}" for t in tags) if tags else "—")

    def _render_posts(self, posts: list):
        self._scores_table.setRowCount(0)
        if not posts:
            return

        SCORE_COLORS = [
            (80, "#22c55e"),   # goed
            (60, "#f59e0b"),   # matig
            (0,  "#ef4444"),   # slecht
        ]

        for row, post in enumerate(posts[:100]):
            self._scores_table.insertRow(row)
            score = post.get("score", 0)
            color = "#94a3b8"
            for threshold, c in SCORE_COLORS:
                if score >= threshold:
                    color = c
                    break

            cells = [
                post.get("campaign_id", "—")[:14],
                f"{score:.1f}",
                str(post.get("views", 0)),
                post.get("status", "—"),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, post)
                if col == 1:
                    item.setForeground(QColor(color))
                self._scores_table.setItem(row, col, item)

    def _on_selection(self):
        rows = self._scores_table.selectionModel().selectedRows()
        if not rows:
            self._detail_text.clear()
            return
        item = self._scores_table.item(rows[0].row(), 0)
        if not item:
            return
        post = item.data(Qt.ItemDataRole.UserRole)
        if not post:
            return

        metrics = post.get("metrics", {})
        lines = [
            f"Campaign:       {post.get('campaign_id', '—')}",
            f"Score:          {post.get('score', 0):.2f} / 100",
            f"Views:          {metrics.get('play_count', 0):,}",
            f"Likes:          {metrics.get('like_count', 0):,}",
            f"Comments:       {metrics.get('comment_count', 0):,}",
            f"Shares:         {metrics.get('share_count', 0):,}",
            f"Engagement:     {metrics.get('engagement_rate', 0):.2%}",
            f"Completion:     {metrics.get('completion_rate', 0):.2%}",
            f"Gem. kijktijd:  {metrics.get('avg_watch_time_pct', 0):.1%}",
        ]
        self._detail_text.setPlainText("\n".join(lines))
