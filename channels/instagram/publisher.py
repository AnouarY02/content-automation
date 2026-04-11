"""
Instagram Publisher — publiceert goedgekeurde campagnes via Meta Graph API.

VEILIGHEIDSVEREISTE: Roep deze klasse ALLEEN aan vanuit approval_service.py,
nooit direct. De approval_service verifieert goedkeuring voordat publicatie plaatsvindt.

Token opslag: data/tokens/instagram.json (nooit naar .env schrijven).

Meta Graph API flow:
  1. POST /{ig-user-id}/media → creation_id (container aanmaken)
  2. POST /{ig-user-id}/media_publish → post_id (publiceren)

Vereist in bundle.idea of bundle.caption:
  - "image_url": publiek bereikbare URL van de afbeelding (verplicht voor foto-posts)
  - "video_url": URL van de video (voor Reels)

Vereist in data/tokens/instagram.json:
  - access_token: Page/User access token met instagram_basic + instagram_content_publish scope
  - ig_user_id: Instagram Business Account ID
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_dir, get_runtime_data_dir

_TOKEN_FILE = ensure_dir(get_runtime_data_dir("tokens")) / "instagram.json"
_GRAPH_BASE = "https://graph.facebook.com/v21.0"


def _load_token() -> dict:
    """Laad tokens uit data/tokens/instagram.json."""
    if _TOKEN_FILE.exists():
        try:
            return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[Instagram] Token bestand kan niet geladen worden: {exc}")
    return {}


def _save_token(data: dict) -> None:
    """Sla tokens atomisch op. Schrijft NOOIT naar .env."""
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(_TOKEN_FILE, data)


class InstagramPublisher:
    """
    Publiceert afbeeldingen en Reels op Instagram via Meta Graph API v21.

    Configuratie (in data/tokens/instagram.json OF als env vars als fallback):
      - access_token    Instagram User Access Token (instagram_content_publish scope)
      - ig_user_id      Instagram Business Account ID

    Content-type logica:
      - bundle.idea/caption bevat "video_url"  → Reel
      - bundle.idea/caption bevat "image_url"  → Foto-post
      - Geen media URL                         → ValueError (Instagram vereist media)
    """

    def __init__(self):
        stored = _load_token()
        self.access_token = (
            stored.get("access_token")
            or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
        )
        self.ig_user_id = (
            stored.get("ig_user_id")
            or os.getenv("INSTAGRAM_USER_ID", "")
        )

    def publish(self, bundle) -> str:
        """
        Publiceer een campagne-bundel op Instagram.

        Args:
            bundle: CampaignBundle (moet status=APPROVED hebben)

        Returns:
            Instagram post ID (media_id)
        """
        if not self.access_token:
            raise ValueError(
                "Instagram access_token niet ingesteld. "
                "Voeg toe aan data/tokens/instagram.json of stel INSTAGRAM_ACCESS_TOKEN in."
            )
        if not self.ig_user_id:
            raise ValueError(
                "Instagram ig_user_id niet ingesteld. "
                "Voeg toe aan data/tokens/instagram.json of stel INSTAGRAM_USER_ID in."
            )

        caption_text = self._build_caption(bundle)
        media_url, media_type = self._resolve_media(bundle)

        logger.info(
            f"[Instagram] Publiceer campagne {bundle.id} "
            f"(type={media_type}, app_id={bundle.app_id})"
        )

        # Stap 1: Container aanmaken
        creation_id = self._create_container(caption_text, media_url, media_type)

        # Stap 2: Wacht tot container klaar is (vereist voor video)
        if media_type == "REELS":
            self._wait_for_container(creation_id)

        # Stap 3: Publiceren
        post_id = self._publish_container(creation_id)

        logger.success(f"[Instagram] Gepubliceerd! Post ID: {post_id}")
        return post_id

    def _build_caption(self, bundle) -> str:
        """Bouw caption op uit bundle — caption + hashtags."""
        caption_data = bundle.caption or {}

        # Ondersteuning voor zowel dict als string
        if isinstance(caption_data, str):
            return caption_data[:2200]

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
        full = f"{caption_text}\n\n{' '.join(all_tags)}" if all_tags else caption_text
        return full[:2200]  # Instagram caption limiet

    def _resolve_media(self, bundle) -> tuple[str, str]:
        """
        Bepaal media URL en type.

        Zoekt in: bundle.idea, bundle.caption, bundle.video_path, bundle.thumbnail_path
        Geeft (url, media_type) terug — media_type is "IMAGE" of "REELS"
        """
        # Zoek video URL (Reels) — expliciete type checks voor veilige .get() aanroepen
        video_url = None
        if isinstance(bundle.idea, dict):
            video_url = bundle.idea.get("video_url")
        if not video_url and isinstance(bundle.caption, dict):
            video_url = bundle.caption.get("video_url")
        if not video_url and isinstance(bundle.video_path, str) and bundle.video_path.startswith("http"):
            video_url = bundle.video_path

        if video_url:
            return video_url, "REELS"

        # Zoek afbeelding URL
        image_url = None
        if isinstance(bundle.idea, dict):
            image_url = bundle.idea.get("image_url")
        if not image_url and isinstance(bundle.caption, dict):
            image_url = bundle.caption.get("image_url")
        if not image_url and bundle.thumbnail_path and bundle.thumbnail_path.startswith("http"):
            image_url = bundle.thumbnail_path

        if image_url:
            return image_url, "IMAGE"

        raise ValueError(
            f"Instagram vereist een media URL. "
            f"Voeg 'image_url' of 'video_url' toe aan bundle.idea of bundle.caption. "
            f"Bundle ID: {bundle.id}"
        )

    def _create_container(self, caption: str, media_url: str, media_type: str) -> str:
        """
        Stap 1: Maak een media container aan.
        Geeft creation_id terug.
        """
        params: dict = {
            "caption": caption,
            "access_token": self.access_token,
        }

        if media_type == "REELS":
            params["media_type"] = "REELS"
            params["video_url"] = media_url
            params["share_to_feed"] = "true"
        else:
            params["media_type"] = "IMAGE"
            params["image_url"] = media_url

        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        f"{_GRAPH_BASE}/{self.ig_user_id}/media",
                        params=params,
                    )
                if response.status_code == 200:
                    break
                err = response.json().get("error", {})
                last_err = f"[Instagram] Container aanmaken mislukt ({response.status_code}): {err.get('message', response.text[:300])}"
                logger.warning(f"{last_err} (poging {attempt + 1}/3)")
            except httpx.TransportError as e:
                last_err = f"[Instagram] Netwerkfout bij container aanmaken: {e}"
                logger.warning(f"{last_err} (poging {attempt + 1}/3)")
            import time; time.sleep(2 ** attempt)
        else:
            raise RuntimeError(last_err or "[Instagram] Container aanmaken mislukt na 3 pogingen")

        data = response.json()
        creation_id = data.get("id")
        if not creation_id:
            raise RuntimeError(f"[Instagram] Geen creation_id in respons: {data}")

        logger.info(f"[Instagram] Container aangemaakt: {creation_id}")
        return creation_id

    def _wait_for_container(self, creation_id: str, max_polls: int = 15) -> None:
        """
        Wacht tot video-container klaar is voor publicatie.
        Meta verwerkt Reels asynchroon — status moet FINISHED zijn.
        """
        import time

        for attempt in range(max_polls):
            with httpx.Client(timeout=15) as client:
                response = client.get(
                    f"{_GRAPH_BASE}/{creation_id}",
                    params={
                        "fields": "status_code",
                        "access_token": self.access_token,
                    },
                )

            data = response.json()
            status = data.get("status_code", "")

            if status == "FINISHED":
                logger.info(f"[Instagram] Container klaar na {attempt + 1} polls")
                return
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"[Instagram] Container verwerking mislukt: status={status}")

            logger.debug(f"[Instagram] Container status: {status} (poging {attempt + 1}/{max_polls})")
            time.sleep(5)

        raise RuntimeError(
            f"[Instagram] Container {creation_id} niet klaar na {max_polls * 5}s. "
            "Probeer opnieuw of check Meta Developer Console."
        )

    def _publish_container(self, creation_id: str) -> str:
        """
        Stap 2: Publiceer de container.
        Geeft post_id (media_id) terug.
        """
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{_GRAPH_BASE}/{self.ig_user_id}/media_publish",
                params={
                    "creation_id": creation_id,
                    "access_token": self.access_token,
                },
            )

        if response.status_code != 200:
            err = response.json().get("error", {})
            raise RuntimeError(
                f"[Instagram] Publicatie mislukt ({response.status_code}): "
                f"{err.get('message', response.text[:300])}"
            )

        data = response.json()
        post_id = data.get("id")
        if not post_id:
            raise RuntimeError(f"[Instagram] Geen post_id in publicatie-respons: {data}")

        return post_id
