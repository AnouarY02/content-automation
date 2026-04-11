"""
YouTube Publisher — publiceert goedgekeurde campagnes via YouTube Data API v3.

VEILIGHEIDSVEREISTE: Roep deze klasse ALLEEN aan vanuit approval_service.py.

OAuth2 setup: Eenmalig via google-auth-oauthlib flow.
Token opslag: data/tokens/youtube.json

YouTube Data API v3 endpoint:
  POST https://www.googleapis.com/upload/youtube/v3/videos
  POST https://www.googleapis.com/youtube/v3/videos (metadata)

Vereist in data/tokens/youtube.json:
  - access_token:    OAuth2 access token
  - refresh_token:   OAuth2 refresh token
  - client_id:       Google OAuth2 Client ID
  - client_secret:   Google OAuth2 Client Secret
  - channel_id:      YouTube Channel ID (UC...)

Scopes: https://www.googleapis.com/auth/youtube.upload
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from loguru import logger

from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_dir, get_runtime_data_dir

_TOKEN_FILE = ensure_dir(get_runtime_data_dir("tokens")) / "youtube.json"
_YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _load_token() -> dict:
    if _TOKEN_FILE.exists():
        try:
            return json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[YouTube] Token bestand kan niet geladen worden: {exc}")
    return {}


def _save_token(data: dict) -> None:
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_json(_TOKEN_FILE, data)


class YouTubePublisher:
    """
    Publiceert videos op YouTube via YouTube Data API v3.

    Ondersteunt:
    - Video upload via URL (downloadt eerst, dan uploadt)
    - Automatisch token refreshen via OAuth2 refresh_token
    - Privacy: public / unlisted / private (standaard public)

    Configuratie in data/tokens/youtube.json:
      - access_token, refresh_token, client_id, client_secret, channel_id
    """

    def __init__(self):
        stored = _load_token()
        self.access_token = stored.get("access_token") or os.getenv("YOUTUBE_ACCESS_TOKEN", "")
        self.refresh_token = stored.get("refresh_token") or os.getenv("YOUTUBE_REFRESH_TOKEN", "")
        self.client_id = stored.get("client_id") or os.getenv("YOUTUBE_CLIENT_ID", "")
        self.client_secret = stored.get("client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET", "")
        self.channel_id = stored.get("channel_id") or os.getenv("YOUTUBE_CHANNEL_ID", "")
        self._stored = stored

    def publish(self, bundle) -> str:
        """
        Publiceer een campagne-bundel op YouTube.

        Args:
            bundle: CampaignBundle (moet status=APPROVED hebben)

        Returns:
            YouTube video ID (bijv. "dQw4w9WgXcQ")
        """
        if not self.access_token and not self.refresh_token:
            raise ValueError(
                "YouTube access_token of refresh_token niet ingesteld. "
                "Voer het OAuth2 setup-proces uit en sla tokens op in data/tokens/youtube.json."
            )

        # Ververs token als nodig
        self._ensure_valid_token()

        title = self._build_title(bundle)
        description = self._build_description(bundle)
        tags = self._extract_tags(bundle)
        video_url = self._resolve_video_url(bundle)

        logger.info(f"[YouTube] Publiceer campagne {bundle.id} — '{title}'")

        # Download video naar tijdelijk bestand
        video_bytes = self._download_video(video_url)

        # Upload naar YouTube
        video_id = self._upload_video(
            video_bytes=video_bytes,
            title=title,
            description=description,
            tags=tags,
            privacy_status="public",
        )

        logger.success(f"[YouTube] Gepubliceerd! Video ID: {video_id}")
        return video_id

    def _build_title(self, bundle) -> str:
        """Bouw YouTube-titel op (max 100 tekens)."""
        if isinstance(bundle.idea, dict):
            title = bundle.idea.get("title") or bundle.idea.get("hook", "")
        else:
            title = ""
        return title[:100] if title else "GLP Coach — Nieuwe video"

    def _build_description(self, bundle) -> str:
        """Bouw beschrijving op uit caption + hashtags (max 5000 tekens)."""
        caption_data = bundle.caption or {}

        if isinstance(caption_data, str):
            return caption_data[:5000]

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
        tags_str = " ".join(all_tags)
        full = f"{caption_text}\n\n{tags_str}" if tags_str else caption_text
        return full[:5000]

    def _extract_tags(self, bundle) -> list[str]:
        """Extraheer hashtags als YouTube tags (max 500 tekens totaal)."""
        caption_data = bundle.caption or {}
        if not isinstance(caption_data, dict):
            return []
        hashtags = caption_data.get("hashtags", {})
        all_tags = (
            hashtags.get("primary", []) +
            hashtags.get("secondary", []) +
            hashtags.get("niche", [])
        )
        # Verwijder # prefix voor YouTube tags
        return [t.lstrip("#") for t in all_tags[:15]]

    def _resolve_video_url(self, bundle) -> str:
        """Zoek video URL in bundle."""
        if isinstance(bundle.idea, dict):
            url = bundle.idea.get("video_url")
            if url:
                return url
        if isinstance(bundle.caption, dict):
            url = bundle.caption.get("video_url")
            if url:
                return url
        if isinstance(bundle.video_path, str) and bundle.video_path.startswith("http"):
            return bundle.video_path
        raise ValueError(
            f"[YouTube] Geen video URL gevonden in bundle {bundle.id}. "
            "YouTube vereist een video — voeg 'video_url' toe aan bundle.idea of bundle.video_path."
        )

    def _download_video(self, video_url: str) -> bytes:
        """Download video van URL naar bytes."""
        logger.info(f"[YouTube] Video downloaden van {video_url[:80]}...")
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=120, follow_redirects=True) as client:
                    response = client.get(video_url)
                if response.status_code == 200:
                    video_bytes = response.content
                    if len(video_bytes) < 10_000:
                        raise RuntimeError(f"[YouTube] Video te klein ({len(video_bytes)} bytes)")
                    logger.info(f"[YouTube] Video gedownload ({len(video_bytes) / 1_000_000:.1f} MB)")
                    return video_bytes
                last_err = f"[YouTube] Download mislukt: HTTP {response.status_code}"
            except httpx.TransportError as e:
                last_err = f"[YouTube] Netwerkfout bij downloaden: {e}"
            logger.warning(f"{last_err} (poging {attempt + 1}/3)")
            time.sleep(2 ** attempt)
        raise RuntimeError(last_err or "[YouTube] Video downloaden mislukt na 3 pogingen")

    def _upload_video(
        self,
        video_bytes: bytes,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str = "public",
    ) -> str:
        """Upload video naar YouTube via resumable upload API."""
        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": "22",  # People & Blogs (meest generiek)
                "defaultLanguage": "nl",
                "defaultAudioLanguage": "nl",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video_bytes)),
        }

        # Stap 1: Initieer resumable upload
        with httpx.Client(timeout=30) as client:
            init_response = client.post(
                f"{_YOUTUBE_UPLOAD}?uploadType=resumable&part=snippet,status",
                headers=headers,
                json=metadata,
            )

        if init_response.status_code not in (200, 201):
            err = init_response.text[:300]
            raise RuntimeError(f"[YouTube] Upload initialisatie mislukt ({init_response.status_code}): {err}")

        upload_url = init_response.headers.get("Location")
        if not upload_url:
            raise RuntimeError("[YouTube] Geen upload URL ontvangen van YouTube API")

        logger.info(f"[YouTube] Resumable upload gestart")

        # Stap 2: Upload video bytes
        with httpx.Client(timeout=300) as client:
            upload_response = client.put(
                upload_url,
                content=video_bytes,
                headers={"Content-Type": "video/mp4"},
            )

        if upload_response.status_code not in (200, 201):
            err = upload_response.text[:300]
            raise RuntimeError(f"[YouTube] Video upload mislukt ({upload_response.status_code}): {err}")

        data = upload_response.json()
        video_id = data.get("id")
        if not video_id:
            raise RuntimeError(f"[YouTube] Geen video ID in upload respons: {data}")

        return video_id

    def _ensure_valid_token(self) -> None:
        """Ververs access token als refresh_token beschikbaar is."""
        if not self.refresh_token or not self.client_id or not self.client_secret:
            return  # Gebruik access_token direct, geen refresh mogelijk

        try:
            with httpx.Client(timeout=15) as client:
                response = client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": self.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data["access_token"]
                updated = {**self._stored, "access_token": self.access_token}
                _save_token(updated)
                logger.debug("[YouTube] Access token verversen geslaagd")
        except Exception as e:
            logger.warning(f"[YouTube] Token verversen mislukt (gebruik bestaande): {e}")
