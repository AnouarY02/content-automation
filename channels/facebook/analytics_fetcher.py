"""
Facebook Analytics Fetcher

Haalt performance-metrics op via Meta Graph API voor Facebook Page posts.
Slaat ruwe data op in: data/analytics/raw/{app_id}/{post_id}_raw.json

Meta Graph API referenties:
  - Post insights: GET /{post-id}/insights
  - Video insights: GET /{video-id}/video_insights
  - Post stats: GET /{post-id}?fields=reactions.summary(true),comments.summary(true),shares

TIMING STRATEGIE:
  - 24u na publicatie: Eerste meting (vroege engagement + bereik)
  - 48u na publicatie: Tweede meting (engagement consolidatie)
  - 7d na publicatie:  Derde meting (long-tail bereik)

METRICS PER POST TYPE:
  - Tekst/foto post: impressions, reach, reactions, comments, shares, post_clicks
  - Video post: video_views, video_avg_time_watched, video_view_time, + bovenstaande
"""

import json
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from analytics.models import RawTikTokMetrics, Platform

ROOT = Path(__file__).parent.parent.parent
RAW_DIR = ROOT / "data" / "analytics" / "raw"
_GRAPH_BASE = "https://graph.facebook.com/v21.0"

# Insights metrics voor een reguliere post (tekst/foto)
_POST_INSIGHT_METRICS = [
    "post_impressions",
    "post_impressions_unique",  # = reach
    "post_reactions_by_type_total",
    "post_clicks",
    "post_engaged_users",
]

# Extra video metrics
_VIDEO_INSIGHT_METRICS = [
    "post_video_views",
    "post_video_avg_time_watched",
    "post_video_view_time",
    "post_video_complete_views_organic",
]


class FacebookAnalyticsFetcher:
    """
    Haalt Facebook Page post metrics op via Meta Graph API.

    Vereist:
      - data/tokens/facebook.json met access_token + page_id (page access token)
      - pages_read_engagement scope
      - pages_show_list scope
    """

    def __init__(self):
        self.access_token = ""
        self.page_id = ""

        token_file = ROOT / "data" / "tokens" / "facebook.json"
        if token_file.exists():
            try:
                stored = json.loads(token_file.read_text(encoding="utf-8"))
                self.access_token = stored.get("access_token") or stored.get("page_token", "")
                self.page_id = stored.get("page_id", "")
            except Exception:
                pass

    def fetch(
        self,
        post_id: str,
        campaign_id: str,
        app_id: str,
        published_at: datetime,
        experiment_id: str | None = None,
        experiment_variant: str | None = None,
    ) -> RawTikTokMetrics:
        """
        Haal metrics op voor één Facebook post.

        Returns:
            RawTikTokMetrics model (gedeeld data model, platform=FACEBOOK)
        """
        hours_since = (datetime.utcnow() - published_at).total_seconds() / 3600

        try:
            metrics = self._fetch_post_metrics(post_id)
        except Exception as exc:
            logger.warning(f"[Facebook Analytics] Graph API fout voor {post_id}: {exc} — gebruik nullen")
            metrics = {}

        # Bouw het gedeelde RawTikTokMetrics model (platform-agnostisch)
        raw = RawTikTokMetrics(
            post_id=post_id,
            campaign_id=campaign_id,
            app_id=app_id,
            platform=Platform.FACEBOOK,

            views=metrics.get("video_views", metrics.get("impressions_unique", 0)),
            likes=metrics.get("reactions", 0),
            comments=metrics.get("comments", 0),
            shares=metrics.get("shares", 0),
            saves=0,  # Facebook heeft geen saves
            profile_visits=metrics.get("post_clicks", 0),

            watch_time_total_sec=metrics.get("video_view_time_sec", 0.0),
            avg_watch_time_sec=metrics.get("video_avg_time_watched_sec", 0.0),
            video_duration_sec=metrics.get("video_duration_sec", 45),

            reach=metrics.get("impressions_unique", 0),
            impressions=metrics.get("impressions", 0),

            fetched_at=datetime.utcnow(),
            hours_since_publish=hours_since,
            published_at=published_at,

            experiment_id=experiment_id,
            experiment_variant=experiment_variant,
        )

        # Sla ruwe data op
        self._save_raw(app_id, post_id, raw, metrics, hours_since)

        return raw

    def _fetch_post_metrics(self, post_id: str) -> dict:
        """Haal alle metrics op voor één post via Graph API."""
        result = {}

        with httpx.Client(timeout=30) as client:
            # 1. Basis post stats (reactions, comments, shares)
            basic_resp = client.get(
                f"{_GRAPH_BASE}/{post_id}",
                params={
                    "fields": "reactions.summary(true),comments.summary(true),shares",
                    "access_token": self.access_token,
                },
            )
            if basic_resp.status_code == 200:
                data = basic_resp.json()
                result["reactions"] = data.get("reactions", {}).get("summary", {}).get("total_count", 0)
                result["comments"] = data.get("comments", {}).get("summary", {}).get("total_count", 0)
                result["shares"] = data.get("shares", {}).get("count", 0)

            # 2. Post insights (impressions, reach, clicks)
            insight_resp = client.get(
                f"{_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": ",".join(_POST_INSIGHT_METRICS),
                    "access_token": self.access_token,
                },
            )
            if insight_resp.status_code == 200:
                for item in insight_resp.json().get("data", []):
                    name = item.get("name", "")
                    value = item.get("values", [{}])[-1].get("value", 0)
                    if name == "post_impressions":
                        result["impressions"] = value if isinstance(value, int) else 0
                    elif name == "post_impressions_unique":
                        result["impressions_unique"] = value if isinstance(value, int) else 0
                    elif name == "post_clicks":
                        result["post_clicks"] = value if isinstance(value, int) else 0

            # 3. Video metrics (optioneel — faalt gracefully als het geen video is)
            video_resp = client.get(
                f"{_GRAPH_BASE}/{post_id}/insights",
                params={
                    "metric": ",".join(_VIDEO_INSIGHT_METRICS),
                    "access_token": self.access_token,
                },
            )
            if video_resp.status_code == 200:
                for item in video_resp.json().get("data", []):
                    name = item.get("name", "")
                    value = item.get("values", [{}])[-1].get("value", 0)
                    if name == "post_video_views":
                        result["video_views"] = value if isinstance(value, int) else 0
                    elif name == "post_video_avg_time_watched":
                        result["video_avg_time_watched_sec"] = float(value) if value else 0.0
                    elif name == "post_video_view_time":
                        result["video_view_time_sec"] = float(value) if value else 0.0

        return result

    def _save_raw(self, app_id: str, post_id: str, raw: RawTikTokMetrics, api_response: dict, hours: float) -> None:
        """Sla ruwe data op als JSON voor auditing."""
        raw_dir = RAW_DIR / app_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        h = int(hours)
        path = raw_dir / f"{post_id}_{h}h_raw.json"
        try:
            path.write_text(
                json.dumps({
                    "post_id": post_id,
                    "platform": "facebook",
                    "hours_since_publish": hours,
                    "fetched_at": datetime.utcnow().isoformat(),
                    "raw_metrics": raw.model_dump(mode="json"),
                    "api_response": api_response,
                }, indent=2),
                encoding="utf-8",
            )
            logger.debug(f"[Facebook Analytics] Ruwe data opgeslagen: {path.name}")
        except Exception as exc:
            logger.warning(f"[Facebook Analytics] Opslaan mislukt: {exc}")
