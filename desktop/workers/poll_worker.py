"""
PollWorker — periodieke data-refresh in de achtergrond

Elke N seconden haalt dit alles op wat de UI nodig heeft:
  - Campaigns (all + pending)
  - Health snapshot
  - Alerts
  - Analytics voor actieve app
  - Backend liveness

De worker emiteert signals per data-type zodat alleen de relevante
views refreshen, niet de hele applicatie.

Thread-safety:
  _stop_event (threading.Event) — atomische stop-vlag, veilig over threads.
  _active_app_id — gewijzigd via set_active_app() vanuit de main thread;
    Python GIL garandeert atomische str-assign op CPython.
"""

import threading

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal


class PollWorker(QThread):
    """
    Achtergrond-polling thread.
    Start eenmalig bij app-startup en stopt bij afsluiten.
    """

    campaigns_ready    = pyqtSignal(list)
    pending_ready      = pyqtSignal(list)
    health_ready       = pyqtSignal(dict)
    alerts_ready       = pyqtSignal(list)
    analytics_ready    = pyqtSignal(str, dict)   # (app_id, summary)
    backend_status     = pyqtSignal(bool)
    audit_ready        = pyqtSignal(list)
    dead_letters_ready = pyqtSignal(list)

    def __init__(self, interval_sec: int = 30, parent=None):
        super().__init__(parent)
        self._interval_sec = interval_sec
        self._stop_event   = threading.Event()   # thread-safe stop flag
        self._active_app_id: str = ""

    def set_active_app(self, app_id: str) -> None:
        self._active_app_id = app_id

    def set_interval(self, sec: int) -> None:
        self._interval_sec = sec

    def stop(self) -> None:
        self._stop_event.set()
        self.quit()

    def run(self):
        """Poll loop — draait totdat stop() wordt aangeroepen."""
        while not self._stop_event.is_set():
            self._poll_once()
            # Wacht interval in kleine stukjes zodat stop() snel reageert
            for _ in range(self._interval_sec * 10):
                if self._stop_event.is_set():
                    return
                self.msleep(100)

    def _poll_once(self):
        """Voer één volledige polling cyclus uit."""
        from api.campaigns_api import CampaignsApi, ApprovalsApi, AnalyticsApi
        from api.health_api import HealthApi, AlertsApi, AuditApi, DeadLetterApi

        # Liveness check
        try:
            from api.client import BackendClient
            alive = BackendClient.instance().ping()
            self.backend_status.emit(alive)
            if not alive:
                return   # Geen zin in verdere calls als backend down is
        except Exception as exc:
            logger.debug(f"[PollWorker] Liveness check mislukt: {exc}")
            self.backend_status.emit(False)
            return

        self._fetch(CampaignsApi().list,    self.campaigns_ready,  list)
        self._fetch(ApprovalsApi().pending, self.pending_ready,    list)
        self._fetch(HealthApi().snapshot,   self.health_ready,     dict)
        self._fetch(AlertsApi().active,     self.alerts_ready,     list)
        self._fetch(AuditApi().recent,      self.audit_ready,      list)
        self._fetch(DeadLetterApi().list,   self.dead_letters_ready, list)

        if self._active_app_id:
            resp = AnalyticsApi().summary(self._active_app_id)
            if resp.ok and isinstance(resp.data, dict):
                self.analytics_ready.emit(self._active_app_id, resp.data)

    def _fetch(self, fn, signal, expected_type):
        """Haal data op en emit signal als het type klopt."""
        try:
            resp = fn()
            if resp.ok:
                data = resp.data
                if expected_type == list and isinstance(data, list):
                    signal.emit(data)
                elif expected_type == dict and isinstance(data, dict):
                    signal.emit(data)
        except Exception as exc:
            logger.debug(f"[PollWorker] Fetch mislukt ({fn.__qualname__}): {exc}")


class Workers:
    """Container die alle workers bij elkaar houdt."""
    _instance: "Workers | None" = None
    poll: PollWorker | None = None

    @classmethod
    def instance(cls) -> "Workers":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def start_polling(self, interval_sec: int = 30) -> PollWorker:
        if self.poll and self.poll.isRunning():
            self.poll.stop()
        self.poll = PollWorker(interval_sec=interval_sec)
        self.poll.start()
        return self.poll

    def stop_all(self):
        if self.poll:
            self.poll.stop()
            self.poll.wait(3000)
