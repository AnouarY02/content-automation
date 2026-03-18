"""
Event Bus — Qt signals-gebaseerde pub/sub voor UI events

GEBRUIK:
  # Abonneren
  EventBus.instance().campaign_start_requested.connect(my_handler)

  # Publiceren
  EventBus.instance().campaign_start_requested.emit("app_001")
"""

from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    """Centrale event bus voor decoupled UI communicatie."""

    _instance: "EventBus | None" = None

    # ── Navigatie ──
    navigate_to   = pyqtSignal(str)           # view naam: "dashboard", "approval", etc.
    view_campaign = pyqtSignal(str)           # campaign_id → open detail

    # ── Campagne acties ──
    campaign_start_requested  = pyqtSignal(str)         # app_id
    approval_decision_made    = pyqtSignal(str, str, str)  # campaign_id, decision, notes
    feedback_run_requested    = pyqtSignal(str)         # app_id

    # ── Alert acties ──
    alert_acknowledge = pyqtSignal(str)       # alert_id
    alert_resolve     = pyqtSignal(str)       # alert_id

    # ── Data refresh ──
    refresh_requested    = pyqtSignal()
    refresh_for_app      = pyqtSignal(str)    # app_id

    # ── Settings ──
    backend_url_changed  = pyqtSignal(str)
    active_app_changed   = pyqtSignal(str)

    @classmethod
    def instance(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def bus() -> EventBus:
    """Shorthand accessor."""
    return EventBus.instance()
