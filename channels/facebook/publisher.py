"""
Facebook Publisher — publiceert goedgekeurde campagnes op een Facebook Page.

VEILIGHEIDSVEREISTE: Roep deze klasse ALLEEN aan vanuit approval_service.py,
nooit direct. De approval_service verifieert goedkeuring voordat publicatie plaatsvindt.

Token opslag: data/tokens/facebook.json (nooit naar .env schrijven).

Meta Graph API endpoints:
  - Tekst post:  POST /{page-id}/feed
  - Foto post:   POST /{page-id}/photos
  - Video post:  POST /{page-id}/videos

Vereist in data/tokens/facebook.json:
  - access_token: Page Access Token met pages_manage_posts scope
  - page_id:      Facebook Page ID
"""

import json
import os
from datetime import datetime, timezone

import httpx
from loguru import logger

from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_dir, get_runtime_data_dir

_TOKEN_FILE = ensure_dir(get_runtime_data_dir("tokens")) / "facebook.json"
_GRAPH_BASE = "https://graph.facebook.com/v21.0"


def _load_token() -> dict:
    """Laad tokens uit data/tokens/facebook.json."""
    if _TOKEN_FILE.exists():
        try:
            return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[Facebook] Token bestand kan niet geladen worden: {exc}")
    return {}


def _save_token(data: dict) -> None:
    """Sla tokens atomisch op. Schrijft NOOIT naar .env."""
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(_TOKEN_FILE, data)


class FacebookPublisher:
    """
    Publiceert content op een Facebook Page via Meta Graph API v21.

    Configuratie (in data/tokens/facebook.json OF als env vars als fallback):
      - access_token    Page Access Token (pages_manage_posts scope)
      - page_id         Facebook Page ID

    Content-type logica:
      - bundle bevat "video_url"  → Video post
      - bundle bevat "image_url"  → Foto post
      - Geen media URL            → Tekst post (feed)
    """

    def __init__(self):
        stored = _load_token()
        self.access_token = (
            stored.get("access_token")
            or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        )
        self.page_id = (
            stored.get("page_id")
            or os.getenv("FACEBOOK_PAGE_ID", "")
        )

    def publish(self, bundle) -> str:
        """
        Publiceer een campagne-bundel op de Facebook Page.

        Args:
            bundle: CampaignBundle (moet status=APPROVED hebben)

        Returns:
            Facebook post ID
        """
        if not self.access_token:
            raise ValueError(
                "Facebook access_token niet ingesteld. "
                "Voeg toe aan data/tokens/facebook.json of stel FACEBOOK_PAGE_ACCESS_TOKEN in."
            )
        if not self.page_id:
            raise ValueError(
                "Facebook page_id niet ingesteld. "
                "Voeg toe aan data/tokens/facebook.json of stel FACEBOOK_PAGE_ID in."
            )

        message = self._build_message(bundle)
        media_url, media_type = self._resolve_media(bundle)

        logger.info(
            f"[Facebook] Publiceer campagne {bundle.id} "
            f"(type={media_type or 'tekst'}, app_id={bundle.app_id})"
        )

        if media_type == "video":
            post_id = self._post_video(message, media_url)
        elif media_type == "photo":
            post_id = self._post_photo(message, media_url)
        else:
            post_id = self._post_text(message)

        logger.success(f"[Facebook] Gepubliceerd! Post ID: {post_id}")
        return post_id

    def _build_message(self, bundle) -> str:
        """Bouw bericht op uit bundle — caption + hashtags."""
        caption_data = bundle.caption or {}

        if isinstance(caption_data, str):
            return caption_data[:63206]  # Facebook tekst limiet

        caption_options = caption_data.get("caption_options", [{}])
        recommended = caption_data.get("recommended_version", "A")
        chosen = next(
            (c for c in caption_options if c.get("version") == recommended),
            caption_options[0] if caption_options else {}
        )

        caption_text = chosen.get("caption", "")
        hashtags = caption_data.get("hashtags", {})
        all_tags = (
            hashtags.get("primary", []) +
            hashtags.get("secondary", []) +
            hashtags.get("niche", [])
        )
        # Trunceer caption_text EERST, dan hashtags toevoegen (zodat totaal limiet niet overschreden wordt)
        max_caption = 63206 - len(" ".join(all_tags)) - 2 if all_tags else 63206
        full = f"{caption_text[:max_caption]}\n\n{' '.join(all_tags)}" if all_tags else caption_text[:63206]
        return full

    def _resolve_media(self, bundle) -> tuple[str | None, str | None]:
        """
        Bepaal media URL en type (optioneel — Facebook ondersteunt tekst-only posts).
        Geeft (url, type) terug — type is "photo", "video" of None.
        """
        # Zoek video URL
        video_url = None
        if isinstance(bundle.idea, dict):
            video_url = bundle.idea.get("video_url")
        if not video_url and isinstance(bundle.caption, dict):
            video_url = bundle.caption.get("video_url")
        if not video_url and bundle.video_path and bundle.video_path.startswith("http"):
            video_url = bundle.video_path

        if video_url:
            return video_url, "video"

        # Zoek afbeelding URL
        image_url = None
        if isinstance(bundle.idea, dict):
            image_url = bundle.idea.get("image_url")
        if not image_url and isinstance(bundle.caption, dict):
            image_url = bundle.caption.get("image_url")
        if not image_url and bundle.thumbnail_path and bundle.thumbnail_path.startswith("http"):
            image_url = bundle.thumbnail_path

        if image_url:
            return image_url, "photo"

        # Geen media — tekst post
        return None, None

    def _post_text(self, message: str) -> str:
        """Publiceer een tekst-post op de Page."""
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{_GRAPH_BASE}/{self.page_id}/feed",
                params={
                    "message": message,
                    "access_token": self.access_token,
                },
            )

        return self._extract_post_id(response, "tekst post")

    def _post_photo(self, message: str, image_url: str) -> str:
        """Publiceer een foto-post op de Page."""
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{_GRAPH_BASE}/{self.page_id}/photos",
                params={
                    "url": image_url,
                    "message": message,
                    "access_token": self.access_token,
                },
            )

        return self._extract_post_id(response, "foto post")

    def _post_video(self, message: str, video_url: str) -> str:
        """
        Publiceer een video op de Page.
        Meta verwerkt video asynchroon — we retourneren het video ID direct.
        """
        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{_GRAPH_BASE}/{self.page_id}/videos",
                params={
                    "file_url": video_url,
                    "description": message,
                    "access_token": self.access_token,
                },
            )

        return self._extract_post_id(response, "video post")

    def _extract_post_id(self, response: httpx.Response, post_type: str) -> str:
        """Extraheer post ID uit respons, gooit RuntimeError bij fout."""
        if response.status_code != 200:
            err = response.json().get("error", {})
            raise RuntimeError(
                f"[Facebook] {post_type} mislukt ({response.status_code}): "
                f"{err.get('message', response.text[:300])}"
            )

        data = response.json()
        post_id = data.get("id") or data.get("post_id")
        if not post_id:
            raise RuntimeError(f"[Facebook] Geen post_id in respons: {data}")

        return post_id
