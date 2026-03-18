"""
TikTok Analytics Fetcher

Haalt performance-metrics op via de TikTok Research API en Content Posting API.
Slaat ruwe data op in: data/analytics/raw/{app_id}/{post_id}_raw.json

TikTok API referenties:
  - Video query: POST https://open.tiktokapis.com/v2/research/video/query/
  - Video info:  GET  https://open.tiktokapis.com/v2/video/list/
  - Business:    GET  https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/

TIMING STRATEGIE:
  - 24u na publicatie: Eerste meting (hook performance + vroege virality)
  - 48u na publicatie: Tweede meting (engagement consolidatie)
  - 7d na publicatie:  Derde meting (long-tail reach)
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from analytics.models import RawTikTokMetrics, Platform

ROOT = Path(__file__).parent.parent.parent
RAW_DIR = ROOT / "data" / "analytics" / "raw"


class TikTokAnalyticsFetcher:
    """
    Haalt TikTok metrics op voor een gepubliceerde post.

    Ondersteunt twee API-paden:
    1. TikTok Content Posting API (voor eigen account posts)
    2. TikTok Research API (voor uitgebreide data — vereist aparte toegang)
    """

    CONTENT_API_BASE = "https://open.tiktokapis.com/v2"
    RESEARCH_API_BASE = "https://open.tiktokapis.com/v2/research"

    # Metrics die we opvragen via de video query
    REQUESTED_FIELDS = [
        "id", "view_count", "like_count", "comment_count",
        "share_count", "collect_count",  # collect = saves
        "video_duration", "create_time",
        "average_time_watched", "total_time_watched",
        "reach", "impressions",
    ]

    def __init__(self):
        self.access_token = os.getenv("TIKTOK_ACCESS_TOKEN")
        self.client_key = os.getenv("TIKTOK_CLIENT_KEY")

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
        Haal metrics op voor één post en sla ruwe data op.

        Args:
            post_id: TikTok video ID
            campaign_id: Interne campagne ID
            app_id: App ID (voor namespacing)
            published_at: Tijdstip van publicatie
            experiment_id: Optioneel A/B experiment ID
            experiment_variant: A | B | C

        Returns:
            RawTikTokMetrics object
        """
        hours_since = (datetime.utcnow() - published_at).total_seconds() / 3600

        logger.info(f"[TikTok Fetcher] Ophalen metrics voor post {post_id} ({hours_since:.0f}u na publicatie)")

        # Probeer echte API; val terug op gesimuleerde data voor development
        raw_data = self._fetch_from_api(post_id)
        if raw_data is None:
            logger.warning(f"[TikTok Fetcher] API niet bereikbaar, gebruik mock data voor {post_id}")
            raw_data = self._mock_metrics(post_id, hours_since)

        metrics = RawTikTokMetrics(
            post_id=post_id,
            campaign_id=campaign_id,
            app_id=app_id,
            platform=Platform.TIKTOK,
            views=raw_data.get("view_count", 0),
            likes=raw_data.get("like_count", 0),
            comments=raw_data.get("comment_count", 0),
            shares=raw_data.get("share_count", 0),
            saves=raw_data.get("collect_count", 0),
            watch_time_total_sec=raw_data.get("total_time_watched", 0.0),
            avg_watch_time_sec=raw_data.get("average_time_watched", 0.0),
            video_duration_sec=raw_data.get("video_duration", 45),
            reach=raw_data.get("reach", 0),
            impressions=raw_data.get("impressions", 0),
            hours_since_publish=hours_since,
            published_at=published_at,
            experiment_id=experiment_id,
            experiment_variant=experiment_variant,
        )

        # Sla ruwe data op
        self._save_raw(metrics, raw_data)
        logger.success(f"[TikTok Fetcher] Metrics opgehaald: {metrics.views} views, ER={metrics.likes/max(metrics.views,1):.3f}")
        return metrics

    def fetch_multiple(
        self,
        posts: list[dict],
        app_id: str,
    ) -> list[RawTikTokMetrics]:
        """
        Haal metrics op voor meerdere posts tegelijk.

        Args:
            posts: Lijst van dicts met keys: post_id, campaign_id, published_at
            app_id: App ID

        Returns:
            Lijst van RawTikTokMetrics
        """
        results = []
        for post in posts:
            try:
                metrics = self.fetch(
                    post_id=post["post_id"],
                    campaign_id=post["campaign_id"],
                    app_id=app_id,
                    published_at=post["published_at"],
                    experiment_id=post.get("experiment_id"),
                    experiment_variant=post.get("experiment_variant"),
                )
                results.append(metrics)
            except Exception as e:
                logger.error(f"[TikTok Fetcher] Fout bij post {post.get('post_id')}: {e}")
        return results

    def _fetch_from_api(self, post_id: str) -> dict | None:
        """
        Haal data op via TikTok Content API v2.
        Geeft None terug als API niet beschikbaar is.
        """
        if not self.access_token:
            return None

        fields = ",".join(self.REQUESTED_FIELDS)
        try:
            with httpx.Client(timeout=15) as client:
                response = client.get(
                    f"{self.CONTENT_API_BASE}/video/list/",
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json",
                    },
                    params={"fields": fields},
                )
                response.raise_for_status()
                data = response.json()

                # Zoek de specifieke video in de response
                videos = data.get("data", {}).get("videos", [])
                for video in videos:
                    if str(video.get("id")) == str(post_id):
                        return video

                logger.warning(f"[TikTok Fetcher] Post {post_id} niet gevonden in API response")
                return None

        except httpx.HTTPStatusError as e:
            logger.error(f"[TikTok Fetcher] HTTP fout {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"[TikTok Fetcher] API fout: {e}")
            return None

    def _save_raw(self, metrics: RawTikTokMetrics, raw_api_response: dict) -> None:
        """Sla ruwe API response + gestructureerde metrics op."""
        app_dir = RAW_DIR / metrics.app_id
        app_dir.mkdir(parents=True, exist_ok=True)

        path = app_dir / f"{metrics.post_id}_{int(metrics.hours_since_publish)}h_raw.json"
        data = {
            "metrics": metrics.model_dump(mode="json"),
            "raw_api_response": raw_api_response,
            "saved_at": datetime.utcnow().isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        logger.debug(f"[TikTok Fetcher] Ruwe data opgeslagen: {path}")

    def _mock_metrics(self, post_id: str, hours_since: float) -> dict:
        """
        Mock data voor development/testing zonder echte API-toegang.
        Simuleert realistisch TikTok groeipatroon.
        """
        import random
        # Virality randomizer — meeste posts doen het gemiddeld, soms een uitschieters
        viral_factor = random.choices([0.2, 0.6, 1.0, 2.5, 8.0], weights=[15, 35, 35, 12, 3])[0]
        base_views = int(800 * viral_factor)

        # Groei over tijd
        time_factor = min(1.0, hours_since / 48)
        views = int(base_views * (0.5 + 0.5 * time_factor))

        avg_watch_pct = random.uniform(0.25, 0.75)
        duration = 45

        return {
            "id": post_id,
            "view_count": views,
            "like_count": int(views * random.uniform(0.02, 0.08)),
            "comment_count": int(views * random.uniform(0.002, 0.015)),
            "share_count": int(views * random.uniform(0.003, 0.012)),
            "collect_count": int(views * random.uniform(0.005, 0.020)),
            "average_time_watched": duration * avg_watch_pct,
            "total_time_watched": views * duration * avg_watch_pct,
            "video_duration": duration,
            "reach": int(views * random.uniform(0.85, 0.98)),
            "impressions": int(views * random.uniform(1.05, 1.20)),
        }
