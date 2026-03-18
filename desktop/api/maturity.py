"""Maturity API endpoints wrapper."""

from api.client import ApiResponse, BackendClient


class MaturityApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def get_latest(self, app_id: str, refresh: bool = False) -> ApiResponse:
        params = {"refresh": "true"} if refresh else None
        return self._c.get(f"/api/maturity/{app_id}", params=params)

    def get_history(self, app_id: str, limit: int = 20) -> ApiResponse:
        return self._c.get(
            f"/api/maturity/{app_id}/history",
            params={"limit": limit},
        )

    def get_dimensions(self, app_id: str) -> ApiResponse:
        return self._c.get(f"/api/maturity/{app_id}/dimensions")

    def recompute(self, app_id: str) -> ApiResponse:
        return self._c.post(f"/api/maturity/{app_id}/compute", {})
