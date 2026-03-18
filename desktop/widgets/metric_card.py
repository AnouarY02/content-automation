"""Herbruikbare dashboard metric-kaart widget."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout


class MetricCard(QFrame):
    """
    Kleine kaart die één metric toont.

    ┌──────────────────┐
    │  Pending           │
    │       3            │  ← groot getal
    │  Approvals         │  ← subtitel
    └──────────────────┘
    """

    def __init__(self, title: str, subtitle: str = "", color: str = "#6C63FF", parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setMinimumSize(140, 100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        self._title_label = QLabel(title)
        self._title_label.setObjectName("metricTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self._value_label = QLabel("—")
        self._value_label.setObjectName("metricValue")
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value_label.setStyleSheet(f"color: {color};")

        self._sub_label = QLabel(subtitle)
        self._sub_label.setObjectName("metricSubtitle")
        self._sub_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self._title_label)
        layout.addStretch()
        layout.addWidget(self._value_label)
        layout.addStretch()
        layout.addWidget(self._sub_label)

    def set_value(self, value: str | int | float) -> None:
        self._value_label.setText(str(value))

    def set_color(self, color: str) -> None:
        self._value_label.setStyleSheet(f"color: {color};")


class HealthBadge(QLabel):
    """Kleine gekleurde badge voor health status."""

    COLORS = {
        "healthy":   ("#22c55e", "HEALTHY"),
        "degraded":  ("#f59e0b", "DEGRADED"),
        "unhealthy": ("#ef4444", "UNHEALTHY"),
        "unknown":   ("#6b7280", "UNKNOWN"),
    }

    def __init__(self, parent=None):
        super().__init__("● UNKNOWN", parent)
        self.setObjectName("healthBadge")
        self._set_status("unknown")

    def set_status(self, status: str) -> None:
        self._set_status(status.lower())

    def _set_status(self, status: str) -> None:
        color, label = self.COLORS.get(status, ("#6b7280", status.upper()))
        self.setText(f"● {label}")
        self.setStyleSheet(f"color: {color}; font-weight: bold;")
