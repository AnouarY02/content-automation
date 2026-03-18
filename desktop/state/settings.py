"""Persistente app-instellingen via QSettings (Windows registry / ini bestand)."""

from PyQt6.QtCore import QSettings


class Settings:
    """Lees/schrijf persistente desktop-instellingen."""

    _ORG = "AY-automatisering"
    _APP = "MarketingOS"

    def __init__(self):
        self._s = QSettings(self._ORG, self._APP)

    @property
    def backend_url(self) -> str:
        return self._s.value("backend/url", "http://localhost:8000")

    @backend_url.setter
    def backend_url(self, url: str) -> None:
        self._s.setValue("backend/url", url)

    @property
    def active_app_id(self) -> str:
        return self._s.value("app/active_id", "")

    @active_app_id.setter
    def active_app_id(self, app_id: str) -> None:
        self._s.setValue("app/active_id", app_id)

    @property
    def poll_interval_sec(self) -> int:
        return int(self._s.value("polling/interval_sec", 30))

    @poll_interval_sec.setter
    def poll_interval_sec(self, sec: int) -> None:
        self._s.setValue("polling/interval_sec", sec)

    @property
    def window_geometry(self) -> bytes | None:
        return self._s.value("window/geometry")

    @window_geometry.setter
    def window_geometry(self, geo: bytes) -> None:
        self._s.setValue("window/geometry", geo)

    def sync(self) -> None:
        self._s.sync()
