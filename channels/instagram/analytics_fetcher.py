"""
Instagram Analytics Fetcher

Haalt performance-metrics op via Meta Graph API Instagram Insights.
Slaat ruwe data op in: data/analytics/raw/{app_id}/{post_id}_raw.json

Meta Graph API referenties:
  - Media insights: GET /{media-id}/insights
  - Account insights: GET /{ig-user-id}/insights

TIMING STRATEGIE:
  - 24u na publicatie: Eerste meting (vroege engagement + bereik)
  - 48u na publicatie: Tweede meting (engagement consolidatie)
  - 7d na publicatie:  Derde meting (long-tail bereik)
"""

import json
import os
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger

from analytics.models import RawTikTokMetrics, Platform

ROOT = Path(__file__).parent.parent.parent
RAW_DIR = ROOT / "data" / "analytics" / "raw"
_GRAPH_BASE = "https://graph.facebook.com/v21.0"

# Instagram Insights metrics per post
_POST_METRICS = [
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saved",
    "video_views",
    "total_interactions",
]


class InstagramAnalyticsFetcher:
    """
    Haalt Instagram post-metrics op via Meta Graph API Insights.

    Vereist:
      - data/tokens/instagram.json met access_token + ig_user_id
      - instagram_basic scope
      - instagram_manage_insights scope (voor insights endpoint)
    """

    def __init__(self):
        self.access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        self.ig_user_id = os.getenv("INSTAGRAM_USER_ID", "")

        # Token bestand heeft prioriteit
        token_file = ROOT / "data" / "tokens" / "instagram.json"
        if token_file.exists():
            try:
                stored = json.loads(token_file.read_text(encoding="utf-8"))
                self.access_token = stored.get("access_token") or self.access_token
                self.ig_user_id = stored.get("ig_user_id") or self.ig_user_id
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
        Haal metrics op voor één Instagram post en sla ruwe data op.

        Args:
            post_id: Instagram media ID (van publisher.publish())
            campaign_id: Interne campagne ID
            app_id: App ID (voor namespacing)
            published_at: Tijdstip van publicatie
            experiment_id: Optioneel A/B experiment ID
            experiment_variant: A | B | C

        Returns:
            RawTikTokMetrics object (gedeeld model, platform=INSTAGRAM)
        """
        hours_since = (datetime.utcnow() - published_at).total_seconds() / 3600

        logger.info(
            f"[Instagram Fetcher] Ophalen metrics voor post {post_id} "
            f"({hours_since:.0f}u na publicatie)"
        )

        raw_data = self._fetch_from_api(post_id)
        if raw_data is None:
            logger.warning(f"[Instagram Fetcher] API niet bereikbaar, gebruik mock data voor {post_id}")
            raw_data = self._mock_metrics(post_id, hours_since)

        # Normaliseer naar RawTikTokMetrics (gedeeld model)
        views = raw_data.get("impressions", raw_data.get("video_views", 0))
        metrics = RawTikTokMetrics(
            post_id=post_id,
            campaign_id=campaign_id,
            app_id=app_id,
            platform=Platform.INSTAGRAM,
            views=views,
            likes=raw_data.get("likes", 0),
            comments=raw_data.get("comments", 0),
            shares=raw_data.get("shares", 0),
            saves=raw_data.get("saved", 0),
            reach=raw_data.get("reach", 0),
            impressions=raw_data.get("impressions", 0),
            hours_since_publish=hours_since,
            published_at=published_at,
            experiment_id=experiment_id,
            experiment_variant=experiment_variant,
        )

        self._save_raw(metrics, raw_data)
        er = metrics.likes / max(metrics.views, 1)
        logger.success(
            f"[Instagram Fetcher] Metrics opgehaald: {metrics.views} impressies, ER={er:.3f}"
        )
        return metrics

    def _fetch_from_api(self, post_id: str) -> dict | None:
        """
        Haal metrics op via Meta Graph API Insights.
        Geeft None terug als API niet beschikbaar is.
        """
        if not self.access_token:
            return None

        metrics_param = ",".join(_POST_METRICS)
        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(
                    f"{_GRAPH_BASE}/{post_id}/insights",
                    params={
                        "metric": metrics_param,
                        "access_token": self.access_token,
                    },
                )
                response.raise_for_status()
                data = response.json()

            # Verwerk insights respons naar flat dict
            result = {}
            for item in data.get("data", []):
                name = item.get("name")
                values = item.get("values", [{}])
                val = values[0].get("value", 0) if values else 0
                result[name] = val

            return result if result else None

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[Instagram Fetcher] HTTP fout {e.response.status_code}: "
                f"{e.response.text[:200]}"
            )
            return None
        except Exception as e:
            logger.error(f"[Instagram Fetcher] API fout: {e}")
            return None

    def _save_raw(self, metrics: RawTikTokMetrics, raw_api_response: dict) -> None:
        """Sla ruwe API response + gestructureerde metrics op."""
        app_dir = RAW_DIR / metrics.app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        path = app_dir / f"ig_{metrics.post_id}_{int(metrics.hours_since_publish)}h_raw.json"
        data = {
            "metrics": metrics.model_dump(mode="json"),
            "raw_api_response": raw_api_response,
            "saved_at": datetime.utcnow().isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        logger.debug(f"[Instagram Fetcher] Ruwe data opgeslagen: {path}")

    def _mock_metrics(self, post_id: str, hours_since: float) -> dict:
        """
        Mock data voor development zonder echte API-toegang.
        Simuleert realistisch Instagram groeipatroon.
        """
        import random

        viral_factor = random.choices([0.3, 0.7, 1.0, 2.0, 5.0], weights=[15, 35, 35, 12, 3])[0]
        base_impressions = int(500 * viral_factor)
        time_factor = min(1.0, hours_since / 48)
        impressions = int(base_impressions * (0.5 + 0.5 * time_factor))
        reach = int(impressions * random.uniform(0.75, 0.92))

        return {
            "impressions": impressions,
            "reach": reach,
            "likes": int(impressions * random.uniform(0.03, 0.10)),
            "comments": int(impressions * random.uniform(0.003, 0.015)),
            "shares": int(impressions * random.uniform(0.005, 0.015)),
            "saved": int(impressions * random.uniform(0.008, 0.025)),
            "video_views": int(impressions * random.uniform(0.60, 0.90)),
            "total_interactions": int(impressions * random.uniform(0.05, 0.15)),
        }
