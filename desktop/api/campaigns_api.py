"""Campaign API endpoints wrapper."""

from api.client import ApiResponse, BackendClient


class CampaignsApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def list(self, status: str | None = None) -> ApiResponse:
        params = {"status": status} if status else None
        return self._c.get("/api/campaigns/", params=params)

    def get(self, campaign_id: str) -> ApiResponse:
        return self._c.get(f"/api/campaigns/{campaign_id}")

    def list_pending(self) -> ApiResponse:
        return self._c.get("/api/campaigns/pending")

    def start(self, app_id: str, platform: str = "tiktok", idea_index: int = 0) -> ApiResponse:
        return self._c.post("/api/campaigns/start", {
            "app_id": app_id, "platform": platform, "idea_index": idea_index
        })


class ApprovalsApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def pending(self) -> ApiResponse:
        return self._c.get("/api/approvals/pending")

    def decide(self, campaign_id: str, decision: str, notes: str = "",
                scheduled_for: str | None = None) -> ApiResponse:
        body = {"campaign_id": campaign_id, "decision": decision, "notes": notes}
        if scheduled_for:
            body["scheduled_for"] = scheduled_for
        return self._c.post("/api/approvals/decide", body)


class AppsApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def list(self) -> ApiResponse:
        return self._c.get("/api/apps/")

    def brand_memory(self, app_id: str) -> ApiResponse:
        return self._c.get(f"/api/apps/{app_id}/brand-memory")


class AnalyticsApi:
    def __init__(self):
        self._c = BackendClient.instance()

    def summary(self, app_id: str) -> ApiResponse:
        return self._c.get(f"/api/analytics/{app_id}/summary")

    def posts(self, app_id: str, limit: int = 20) -> ApiResponse:
        return self._c.get(f"/api/analytics/{app_id}/posts", params={"limit": limit})
