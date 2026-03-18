"""Onderste statusbalk — backend status, actieve app, laatste refresh."""

from datetime import datetime

from PyQt6.QtWidgets import QLabel, QStatusBar

from state.store import AppStore


class AppStatusBar(QStatusBar):
    """Permanente statusbalk onderaan het venster."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("appStatusBar")
        self.setSizeGripEnabled(False)

        self._backend_label = QLabel("● Backend: checking...")
        self._backend_label.setObjectName("statusItem")
        self.addWidget(self._backend_label)

        self._app_label = QLabel("App: —")
        self._app_label.setObjectName("statusItem")
        self.addWidget(self._app_label)

        self._msg_label = QLabel("")
        self._msg_label.setObjectName("statusMsg")
        self.addWidget(self._msg_label, 1)  # stretch=1 → neemt beschikbare ruimte

        self._refresh_label = QLabel("Laatste refresh: —")
        self._refresh_label.setObjectName("statusItem")
        self.addPermanentWidget(self._refresh_label)

        # Verbind met store
        store = AppStore.instance()
        store.backend_status_changed.connect(self._on_backend)
        store.active_app_changed.connect(self._on_app)
        store.notification.connect(self._on_notification)
        store.health_updated.connect(lambda _: self._on_refresh())

    def _on_backend(self, online: bool) -> None:
        if online:
            self._backend_label.setText("● Backend: online")
            self._backend_label.setStyleSheet("color: #22c55e;")
        else:
            self._backend_label.setText("● Backend: offline")
            self._backend_label.setStyleSheet("color: #ef4444;")

    def _on_app(self, app_id: str) -> None:
        self._app_label.setText(f"App: {app_id}")

    def _on_refresh(self) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self._refresh_label.setText(f"Refresh: {now}")

    def _on_notification(self, msg_type: str, message: str) -> None:
        colors = {
            "info": "#94a3b8", "success": "#22c55e",
            "warning": "#f59e0b", "error": "#ef4444",
        }
        color = colors.get(msg_type, "#94a3b8")
        self._msg_label.setText(message[:80])
        self._msg_label.setStyleSheet(f"color: {color};")
