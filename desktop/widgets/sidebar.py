"""
Sidebar navigatiewidget — verticale knoppenbalk links in het venster.

VISUEEL ONTWERP:
  ┌────────────┐
  │ AY OS      │  ← logo / app naam
  ├────────────┤
  │ 🏠 Dashboard│  ← actieve view heeft highlight
  │ 📋 Campaigns│
  │ ✅ Approvals│  ← badge met aantal pending
  │ 📊 Analytics│
  │ 🔍 Observe │
  │ ⏱ Scheduler│
  ├────────────┤
  │ ⚙ Settings │
  └────────────┘
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from events.bus import bus


class NavButton(QPushButton):
    """Één navigatieknop in de sidebar."""

    def __init__(self, label: str, view_name: str, parent=None):
        super().__init__(label, parent)
        self._view_name = view_name
        self.setCheckable(True)
        self.setFixedHeight(44)
        self.setObjectName("navButton")
        self.clicked.connect(lambda: bus().navigate_to.emit(self._view_name))

    def set_badge(self, count: int) -> None:
        """Toon een badge-getal achter de label (bijv. '✅ Approvals  3')."""
        base = self.text().split("  ")[0]
        if count > 0:
            self.setText(f"{base}  {count}")
        else:
            self.setText(base)


class Sidebar(QWidget):
    """Navigatiesidebar — links in het hoofdvenster."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(4)

        # Logo / app naam
        logo = QLabel("AY Marketing OS")
        logo.setObjectName("sidebarLogo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setWordWrap(True)
        layout.addWidget(logo)
        layout.addSpacing(12)

        # Nav knoppen (label, view_name)
        self._buttons: dict[str, NavButton] = {}
        nav_items = [
            ("🏠  Dashboard",   "dashboard"),
            ("📋  Campaigns",   "campaigns"),
            ("✅  Approvals",   "approval"),
            ("📊  Analytics",   "analytics"),
            ("🔍  Observability","observability"),
            ("⏱  Scheduler",   "scheduler"),
            ("🎯  Maturity",    "maturity"),
        ]
        for label, name in nav_items:
            btn = NavButton(label, name, self)
            self._buttons[name] = btn
            layout.addWidget(btn)

        layout.addStretch()

        # Settings onderaan
        settings_btn = NavButton("⚙  Settings", "settings", self)
        self._buttons["settings"] = settings_btn
        layout.addWidget(settings_btn)

        # Activeer standaard de dashboard knop
        self._active = "dashboard"
        self._buttons["dashboard"].setChecked(True)

        # Luister naar navigatie-events om actieve knop bij te werken
        bus().navigate_to.connect(self._on_navigate)

    def _on_navigate(self, view_name: str) -> None:
        for name, btn in self._buttons.items():
            btn.setChecked(name == view_name)
        self._active = view_name

    def set_pending_count(self, count: int) -> None:
        if "approval" in self._buttons:
            self._buttons["approval"].set_badge(count)

    def set_alert_count(self, count: int) -> None:
        if "observability" in self._buttons:
            self._buttons["observability"].set_badge(count)
