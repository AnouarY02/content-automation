"""Health & Observability API endpoints wrapper."""

from api.client import ApiResponse, BackendClient


class HealthApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def snapshot(self, force: bool = False) -> ApiResponse:
        return self._c.get("/api/health/", params={"force": str(force).lower()})

    def liveness(self) -> ApiResponse:
        return self._c.get("/api/health/live")

    def component(self, name: str) -> ApiResponse:
        return self._c.get(f"/api/health/{name}")

    def trigger_job(self, job_id: str) -> ApiResponse:
        return self._c.post(f"/api/health/scheduler/trigger/{job_id}")


class AuditApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def recent(self, app_id: str | None = None, limit: int = 50,
               job_type: str | None = None, outcome: str | None = None) -> ApiResponse:
        params = {"limit": limit}
        if app_id:
            params["app_id"] = app_id
        if job_type:
            params["job_type"] = job_type
        if outcome:
            params["outcome"] = outcome
        return self._c.get("/api/health/audit/recent", params=params)

    def failures(self, app_id: str | None = None, limit: int = 20) -> ApiResponse:
        params = {"limit": limit}
        if app_id:
            params["app_id"] = app_id
        return self._c.get("/api/health/audit/failures", params=params)

    def failure_rate(self, app_id: str | None = None, hours: int = 24) -> ApiResponse:
        params = {"hours": hours}
        if app_id:
            params["app_id"] = app_id
        return self._c.get("/api/health/audit/failure-rate", params=params)


class AlertsApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def active(self, app_id: str | None = None) -> ApiResponse:
        params = {}
        if app_id:
            params["app_id"] = app_id
        return self._c.get("/api/health/alerts", params=params)

    def acknowledge(self, alert_id: str) -> ApiResponse:
        return self._c.post(f"/api/health/alerts/{alert_id}/acknowledge")

    def resolve(self, alert_id: str) -> ApiResponse:
        return self._c.post(f"/api/health/alerts/{alert_id}/resolve")


class DeadLetterApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def list(self, app_id: str | None = None) -> ApiResponse:
        params = {}
        if app_id:
            params["app_id"] = app_id
        return self._c.get("/api/health/dead-letter", params=params)

    def resolve(self, dl_id: str, resolution: str, app_id: str | None = None) -> ApiResponse:
        params = {"resolution": resolution}
        if app_id:
            params["app_id"] = app_id
        return self._c.post(f"/api/health/dead-letter/{dl_id}/resolve", params)
