"""
Approval View — human-in-the-loop campagne review werkruimte.

LAYOUT:
  ┌───────────────────────────────────────────────────────────────┐
  │  Approval  [Campaign ID]  [Correlation ID]                    │
  ├─────────────────────────┬─────────────────────────────────────┤
  │ Script (scene-by-scene) │ Caption + Hashtags                  │
  │  Scene 1: ...           │  #hashtag1 #hashtag2 ...            │
  │  Scene 2: ...           ├─────────────────────────────────────┤
  │  ...                    │ Video preview                       │
  ├─────────────────────────┤  [Openen in Bestandsbeheer]         │
  │ Campaign Info           ├─────────────────────────────────────┤
  │  App: ...               │ Beslissing                          │
  │  Kosten: $...           │  [✓ Goedkeuren]  [✗ Afwijzen]       │
  │  Status: ...            │  Reden: [____________]              │
  └─────────────────────────┴─────────────────────────────────────┘
"""

import json
import os
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.store import AppStore
from workers.api_worker import run_api


class ApprovalView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._current_campaign: dict | None = None
        self._setup_ui()

        bus().view_campaign.connect(self._load_campaign_by_id)
        self._store.pending_updated.connect(self._on_pending_updated)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        header = QHBoxLayout()
        title = QLabel("Approval")
        title.setObjectName("viewTitle")
        header.addWidget(title)

        self._campaign_id_label = QLabel("Geen campagne geselecteerd")
        self._campaign_id_label.setObjectName("subTitle")
        header.addWidget(self._campaign_id_label)

        header.addStretch()

        corr_lbl = QLabel("Correlation ID:")
        corr_lbl.setObjectName("metaLabel")
        header.addWidget(corr_lbl)

        self._corr_id_label = QLabel("—")
        self._corr_id_label.setObjectName("metaValue")
        self._corr_id_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        header.addWidget(self._corr_id_label)

        root.addLayout(header)

        # Pending campagnes balk
        pending_bar = QHBoxLayout()
        self._pending_label = QLabel("Geen campagnes wachten op approval.")
        self._pending_label.setObjectName("infoLabel")
        pending_bar.addWidget(self._pending_label)
        pending_bar.addStretch()
        self._prev_btn = QPushButton("◀ Vorige")
        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn = QPushButton("Volgende ▶")
        self._next_btn.clicked.connect(self._go_next)
        pending_bar.addWidget(self._prev_btn)
        pending_bar.addWidget(self._next_btn)
        root.addLayout(pending_bar)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Linkerpaneel: script + info
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)

        script_group = QGroupBox("Script (scene-by-scene)")
        script_layout = QVBoxLayout(script_group)
        self._script_area = QScrollArea()
        self._script_area.setWidgetResizable(True)
        self._script_container = QWidget()
        self._script_vbox = QVBoxLayout(self._script_container)
        self._script_vbox.setSpacing(8)
        self._script_vbox.addStretch()
        self._script_area.setWidget(self._script_container)
        script_layout.addWidget(self._script_area)
        left_layout.addWidget(script_group, stretch=3)

        info_group = QGroupBox("Campagne Info")
        info_layout = QVBoxLayout(info_group)
        self._info_text = QTextEdit()
        self._info_text.setReadOnly(True)
        self._info_text.setObjectName("detailText")
        self._info_text.setMaximumHeight(130)
        info_layout.addWidget(self._info_text)
        left_layout.addWidget(info_group, stretch=1)

        splitter.addWidget(left_widget)

        # Rechterpaneel: caption + video + beslissing
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        caption_group = QGroupBox("Caption & Hashtags")
        caption_layout = QVBoxLayout(caption_group)
        self._caption_text = QTextEdit()
        self._caption_text.setReadOnly(True)
        self._caption_text.setObjectName("detailText")
        self._caption_text.setMaximumHeight(120)
        caption_layout.addWidget(self._caption_text)

        self._hashtags_label = QLabel()
        self._hashtags_label.setObjectName("hashtagsLabel")
        self._hashtags_label.setWordWrap(True)
        caption_layout.addWidget(self._hashtags_label)
        right_layout.addWidget(caption_group)

        video_group = QGroupBox("Video Preview")
        video_layout = QVBoxLayout(video_group)
        self._video_path_label = QLabel("Geen video beschikbaar")
        self._video_path_label.setObjectName("metaValue")
        self._video_path_label.setWordWrap(True)
        video_layout.addWidget(self._video_path_label)

        self._open_video_btn = QPushButton("Openen in Bestandsbeheer")
        self._open_video_btn.setEnabled(False)
        self._open_video_btn.clicked.connect(self._open_video)
        video_layout.addWidget(self._open_video_btn)
        right_layout.addWidget(video_group)

        # Beslissing
        decision_group = QGroupBox("Beslissing")
        decision_layout = QVBoxLayout(decision_group)

        self._reden_input = QLineEdit()
        self._reden_input.setPlaceholderText("Reden / opmerkingen (optioneel bij goedkeuring, verplicht bij afwijzing)")
        decision_layout.addWidget(QLabel("Reden:"))
        decision_layout.addWidget(self._reden_input)

        btn_row = QHBoxLayout()
        self._approve_btn = QPushButton("✓  Goedkeuren")
        self._approve_btn.setObjectName("approveButton")
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self._on_approve)
        btn_row.addWidget(self._approve_btn)

        self._reject_btn = QPushButton("✗  Afwijzen")
        self._reject_btn.setObjectName("rejectButton")
        self._reject_btn.setEnabled(False)
        self._reject_btn.clicked.connect(self._on_reject)
        btn_row.addWidget(self._reject_btn)

        decision_layout.addLayout(btn_row)
        right_layout.addWidget(decision_group)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setSizes([500, 400])
        root.addWidget(splitter)

    # ------------------------------------------------------------------
    # Data laden
    # ------------------------------------------------------------------

    def _on_pending_updated(self, pending: list):
        self._pending_list = pending
        count = len(pending)
        if count == 0:
            self._pending_label.setText("Geen campagnes wachten op approval.")
        else:
            self._pending_label.setText(f"{count} campagne(s) wachten op approval.")

        # Auto-load eerste als nog niets geselecteerd
        if pending and self._current_campaign is None:
            self._load_campaign(pending[0])

    def _load_campaign_by_id(self, campaign_id: str):
        from api.campaigns_api import CampaignsApi
        run_api(
            fn=lambda: CampaignsApi().get(campaign_id),
            on_success=self._load_campaign,
            parent=self,
        )

    def _load_campaign(self, campaign: dict):
        if not campaign:
            return
        self._current_campaign = campaign
        cid = campaign.get("id", "—")
        self._campaign_id_label.setText(f"#{cid[:16]}")
        self._corr_id_label.setText(campaign.get("correlation_id", "—"))

        # Info
        lines = [
            f"App:       {campaign.get('app_id', '—')}",
            f"Status:    {campaign.get('status', '—')}",
            f"Kosten:    ${campaign.get('total_cost_usd', 0):.4f}",
            f"Aangemaakt:{str(campaign.get('created_at', '—'))[:19]}",
        ]
        self._info_text.setPlainText("\n".join(lines))

        # Script
        self._render_script(campaign.get("script", {}))

        # Caption
        caption_data = campaign.get("caption", {})
        if isinstance(caption_data, dict):
            self._caption_text.setPlainText(caption_data.get("caption", "—"))
            tags = caption_data.get("hashtags", [])
            self._hashtags_label.setText(" ".join(f"#{t}" for t in tags))
        else:
            self._caption_text.setPlainText(str(caption_data))
            self._hashtags_label.setText("")

        # Video
        video_path = campaign.get("video_path", "")
        if video_path and os.path.exists(video_path):
            self._video_path_label.setText(video_path)
            self._open_video_btn.setEnabled(True)
        else:
            self._video_path_label.setText(video_path or "Geen video")
            self._open_video_btn.setEnabled(False)

        # Buttons
        is_pending = campaign.get("status") == "pending_approval"
        self._approve_btn.setEnabled(is_pending)
        self._reject_btn.setEnabled(is_pending)
        self._reden_input.clear()

    def _render_script(self, script: dict):
        # Verwijder oude scene widgets
        while self._script_vbox.count() > 1:  # keep stretch
            item = self._script_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not script:
            placeholder = QLabel("Geen script beschikbaar.")
            placeholder.setObjectName("metaLabel")
            self._script_vbox.insertWidget(0, placeholder)
            return

        scenes = script.get("scenes", [])
        for i, scene in enumerate(scenes):
            frame = QFrame()
            frame.setObjectName("sceneCard")
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            flayout = QVBoxLayout(frame)
            flayout.setSpacing(4)

            scene_title = QLabel(f"Scene {i + 1}: {scene.get('hook', scene.get('type', ''))}")
            scene_title.setObjectName("sceneTitle")
            flayout.addWidget(scene_title)

            if "voiceover" in scene:
                vo_lbl = QLabel(f"Voiceover: {scene['voiceover']}")
                vo_lbl.setWordWrap(True)
                vo_lbl.setObjectName("sceneText")
                flayout.addWidget(vo_lbl)

            if "visual" in scene:
                vis_lbl = QLabel(f"Visueel: {scene['visual']}")
                vis_lbl.setWordWrap(True)
                vis_lbl.setObjectName("sceneSubText")
                flayout.addWidget(vis_lbl)

            if "duration_sec" in scene:
                dur_lbl = QLabel(f"Duur: {scene['duration_sec']}s")
                dur_lbl.setObjectName("sceneSubText")
                flayout.addWidget(dur_lbl)

            self._script_vbox.insertWidget(i, frame)

        if not scenes:
            # Script als ruwe tekst tonen
            raw = QLabel(json.dumps(script, indent=2, ensure_ascii=False))
            raw.setWordWrap(True)
            raw.setObjectName("metaValue")
            self._script_vbox.insertWidget(0, raw)

    # ------------------------------------------------------------------
    # Navigatie
    # ------------------------------------------------------------------

    def _go_prev(self):
        pending = getattr(self, "_pending_list", [])
        if not pending or self._current_campaign is None:
            return
        ids = [c.get("id") for c in pending]
        cur = self._current_campaign.get("id")
        idx = ids.index(cur) if cur in ids else 0
        if idx > 0:
            self._load_campaign(pending[idx - 1])

    def _go_next(self):
        pending = getattr(self, "_pending_list", [])
        if not pending or self._current_campaign is None:
            return
        ids = [c.get("id") for c in pending]
        cur = self._current_campaign.get("id")
        idx = ids.index(cur) if cur in ids else -1
        if idx < len(pending) - 1:
            self._load_campaign(pending[idx + 1])

    # ------------------------------------------------------------------
    # Acties
    # ------------------------------------------------------------------

    def _open_video(self):
        path = self._video_path_label.text()
        if not path or not os.path.exists(path):
            return
        folder = os.path.dirname(os.path.abspath(path))
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{os.path.abspath(path)}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _on_approve(self):
        if not self._current_campaign:
            return
        campaign_id = self._current_campaign.get("id", "")
        reden = self._reden_input.text().strip() or "Goedgekeurd via desktop app"

        reply = QMessageBox.question(
            self,
            "Bevestig goedkeuring",
            f"Campagne {campaign_id[:16]} goedkeuren?\n\nDit start de publicatie op TikTok.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from api.campaigns_api import ApprovalsApi
        run_api(
            fn=lambda: ApprovalsApi().decide(campaign_id, "approved", reden),
            on_success=self._on_decision_success,
            on_error=lambda e: self._store.notify("error", f"Approval mislukt: {e}"),
            parent=self,
        )

    def _on_reject(self):
        if not self._current_campaign:
            return
        reden = self._reden_input.text().strip()
        if not reden:
            QMessageBox.warning(self, "Reden vereist", "Vul een reden in voor afwijzing.")
            return

        campaign_id = self._current_campaign.get("id", "")
        from api.campaigns_api import ApprovalsApi
        run_api(
            fn=lambda: ApprovalsApi().decide(campaign_id, "rejected", reden),
            on_success=self._on_decision_success,
            on_error=lambda e: self._store.notify("error", f"Afwijzing mislukt: {e}"),
            parent=self,
        )

    def _on_decision_success(self, result: dict):
        action = result.get("action", "beslissing")
        self._store.notify("success", f"Campagne {action}: {result.get('campaign_id', '')[:12]}")
        bus().approval_decision_made.emit(result.get("campaign_id", ""), action)
        self._current_campaign = None
        self._approve_btn.setEnabled(False)
        self._reject_btn.setEnabled(False)
        # Refresh pending list
        self._store.load_pending()
