"""
Settings View — backend URL, actieve app, polling interval.
"""

from PyQt6.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from events.bus import bus
from state.settings import Settings
from state.store import AppStore
from workers.api_worker import run_api


class SettingsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = AppStore.instance()
        self._settings = Settings()
        self._setup_ui()
        self._load_values()
        self._store.apps_updated.connect(self._populate_app_combo)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        title = QLabel("Instellingen")
        title.setObjectName("viewTitle")
        root.addWidget(title)

        # Backend configuratie
        backend_group = QGroupBox("Backend Verbinding")
        backend_form = QFormLayout(backend_group)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("http://localhost:8000")
        backend_form.addRow("Backend URL:", self._url_input)

        url_btn_row = QHBoxLayout()
        test_btn = QPushButton("Verbinding testen")
        test_btn.clicked.connect(self._test_connection)
        url_btn_row.addWidget(test_btn)
        self._conn_status = QLabel()
        self._conn_status.setObjectName("metaValue")
        url_btn_row.addWidget(self._conn_status)
        url_btn_row.addStretch()
        backend_form.addRow("", url_btn_row)

        self._poll_spin = QSpinBox()
        self._poll_spin.setRange(5, 300)
        self._poll_spin.setSuffix(" sec")
        backend_form.addRow("Poll interval:", self._poll_spin)

        root.addWidget(backend_group)

        # App selectie
        app_group = QGroupBox("Actieve App")
        app_form = QFormLayout(app_group)

        self._app_combo = QComboBox()
        self._app_combo.setMinimumWidth(300)
        app_form.addRow("App:", self._app_combo)

        refresh_apps_btn = QPushButton("Apps vernieuwen")
        refresh_apps_btn.clicked.connect(self._refresh_apps)
        app_form.addRow("", refresh_apps_btn)

        root.addWidget(app_group)

        # Opslaan
        save_row = QHBoxLayout()
        save_btn = QPushButton("Opslaan")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        save_row.addStretch()
        save_row.addWidget(save_btn)
        root.addLayout(save_row)

        root.addStretch()

    def _load_values(self):
        self._url_input.setText(self._settings.backend_url)
        self._poll_spin.setValue(self._settings.poll_interval_sec)

    def _populate_app_combo(self, apps: list):
        self._app_combo.clear()
        self._app_combo.addItem("— Selecteer app —", "")
        for app in apps:
            label = f"{app.get('name', app.get('id', '—'))} ({app.get('id', '')[:8]})"
            self._app_combo.addItem(label, app.get("id", ""))
        # Herstel selectie
        active = self._settings.active_app_id
        if active:
            for i in range(self._app_combo.count()):
                if self._app_combo.itemData(i) == active:
                    self._app_combo.setCurrentIndex(i)
                    break

    def _test_connection(self):
        url = self._url_input.text().strip()
        if not url:
            return
        from api.client import BackendClient
        client = BackendClient.instance()
        # Tijdelijk de URL overschrijven voor de test
        orig = client._base_url
        client._base_url = url
        run_api(
            fn=lambda: client.ping(),
            on_success=lambda ok: self._set_conn_status(ok),
            on_error=lambda _: self._set_conn_status(False),
            parent=self,
        )
        client._base_url = orig

    def _set_conn_status(self, ok: bool):
        if ok:
            self._conn_status.setText("✓ Verbonden")
            self._conn_status.setStyleSheet("color: #22c55e;")
        else:
            self._conn_status.setText("✗ Geen verbinding")
            self._conn_status.setStyleSheet("color: #ef4444;")

    def _refresh_apps(self):
        from api.campaigns_api import AppsApi
        run_api(
            fn=lambda: AppsApi().list(),
            on_success=self._store.set_apps,
            parent=self,
        )

    def _save(self):
        url = self._url_input.text().strip()
        if url:
            old_url = self._settings.backend_url
            self._settings.backend_url = url
            if url != old_url:
                bus().backend_url_changed.emit(url)

        self._settings.poll_interval_sec = self._poll_spin.value()

        app_id = self._app_combo.currentData() or ""
        self._settings.active_app_id = app_id
        self._store.set_active_app(app_id)

        self._store.notify("success", "Instellingen opgeslagen")
