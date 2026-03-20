"""
TikTok Publisher — publiceert goedgekeurde campagnes via TikTok Content Posting API.

VEILIGHEIDSVEREISTE: Roep deze klasse ALLEEN aan vanuit approval_service.py,
nooit direct. De approval_service verifieert goedkeuring voordat publicatie plaatsvindt.

Token opslag: data/tokens/tiktok.json (nooit naar .env schrijven).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from loguru import logger

from utils.file_io import atomic_write_json
from utils.runtime_paths import ensure_dir, get_runtime_data_dir

_TOKEN_FILE = ensure_dir(get_runtime_data_dir("tokens")) / "tiktok.json"


def _resolve_video_for_publish(raw_path: str) -> Path:
    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        cache_dir = ensure_dir(get_runtime_data_dir("publishing"))
        target = cache_dir / f"publish_{uuid4().hex}.mp4"
        with httpx.Client(timeout=120) as client:
            response = client.get(raw_path)
            response.raise_for_status()
            target.write_bytes(response.content)
        return target
    return Path(raw_path)


def _load_token_from_store() -> tuple[str, str]:
    """Laad tokens uit data/tokens/tiktok.json. Retourneert (access_token, refresh_token)."""
    if _TOKEN_FILE.exists():
        try:
            data = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
            return data.get("access_token", ""), data.get("refresh_token", "")
        except Exception as exc:
            logger.warning(f"[TikTok] Token bestand kan niet geladen worden: {exc}")
    return "", ""


def _save_tokens_to_store(access_token: str, refresh_token: str) -> None:
    """Sla tokens atomisch op in data/tokens/tiktok.json. Schrijft NOOIT naar .env."""
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_TOKEN_FILE, {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def _refresh_access_token() -> str | None:
    """Vernieuw het access token via het refresh token. Slaat op in data/tokens/tiktok.json."""
    stored_access, stored_refresh = _load_token_from_store()
    refresh_token = stored_refresh or os.getenv("TIKTOK_REFRESH_TOKEN", "")
    client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")
    if not refresh_token or not client_key:
        return None
    try:
        r = httpx.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        new_token = data.get("access_token", "")
        new_refresh = data.get("refresh_token", refresh_token)
        if not new_token:
            logger.warning("[TikTok] Token refresh: geen access_token in respons")
            return None
        # Atomisch opslaan — nooit naar .env schrijven
        _save_tokens_to_store(new_token, new_refresh)
        # Werk ook process-environment bij zodat andere modules de nieuwe waarden zien
        os.environ["TIKTOK_ACCESS_TOKEN"] = new_token
        os.environ["TIKTOK_REFRESH_TOKEN"] = new_refresh
        logger.success("[TikTok] Access token vernieuwd en opgeslagen in data/tokens/tiktok.json")
        return new_token
    except Exception as e:
        logger.error(f"[TikTok] Token refresh mislukt: {e}")
        return None


class TikTokPublisher:
    """Publiceert video's op TikTok via de Content Posting API v2.

    Post-modus wordt bepaald door TIKTOK_POST_MODE in .env:
      DIRECT_POST  → Video gaat direct live (vereist video.publish scope + goedgekeurde app)
      INBOX        → Video gaat naar creator inbox voor handmatige publicatie (default)

    Zet TIKTOK_POST_MODE=DIRECT_POST zodra TikTok 'Content Posting API' heeft goedgekeurd.
    """

    UPLOAD_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    UPLOAD_COMPLETE_URL = "https://open.tiktokapis.com/v2/post/publish/video/complete/"
    STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

    def __init__(self):
        # Token bestand heeft prioriteit boven .env (veiliger, atomisch bijgewerkt)
        stored_token, _ = _load_token_from_store()
        self.access_token = stored_token or os.getenv("TIKTOK_ACCESS_TOKEN")
        raw_mode = os.getenv("TIKTOK_POST_MODE", "INBOX").strip().upper()
        self.post_mode = raw_mode if raw_mode in ("DIRECT_POST", "INBOX") else "INBOX"

    def publish(self, bundle) -> str:
        """
        Publiceer een campagne-bundel op TikTok.

        Args:
            bundle: CampaignBundle (moet status=APPROVED hebben)

        Returns:
            TikTok post ID
        """
        if not self.access_token:
            raise ValueError("TIKTOK_ACCESS_TOKEN niet ingesteld in .env")

        video_path = _resolve_video_for_publish(bundle.video_path) if bundle.video_path else None
        if not video_path or not video_path.exists():
            raise FileNotFoundError(f"Video bestand niet gevonden: {bundle.video_path}")

        caption_data = bundle.caption or {}
        caption_options = caption_data.get("caption_options", [{}])
        recommended = caption_data.get("recommended_version", "A")
        chosen_caption = next(
            (c for c in caption_options if c.get("version") == recommended),
            caption_options[0] if caption_options else {}
        )

        caption_text = chosen_caption.get("caption", "")
        hashtags = caption_data.get("hashtags", {})
        all_tags = (
            hashtags.get("primary", []) +
            hashtags.get("secondary", []) +
            hashtags.get("niche", [])
        )
        full_caption = f"{caption_text}\n\n{' '.join(all_tags)}"

        if self.post_mode == "DIRECT_POST":
            logger.info(f"[TikTok] Start upload voor campagne {bundle.id} (DIRECT_POST — gaat direct live)")
        else:
            logger.info(f"[TikTok] Start upload voor campagne {bundle.id} (INBOX — verschijnt in creator inbox)")

        try:
            # Stap 1: Initieer upload
            publish_id, upload_url = self._init_upload(video_path, full_caption)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                # Token verlopen — probeer te vernieuwen
                logger.warning("[TikTok] 401 ontvangen, token vernieuwen...")
                new_token = _refresh_access_token()
                if not new_token:
                    raise ValueError("TikTok token verlopen en refresh mislukt. Draai: python scripts/get_tiktok_token.py")
                self.access_token = new_token
                try:
                    publish_id, upload_url = self._init_upload(video_path, full_caption)
                except httpx.HTTPStatusError as e2:
                    if e2.response.status_code == 401:
                        raise ValueError(
                            "TikTok upload mislukt met 401 na token refresh. "
                            "Waarschijnlijke oorzaak: de TikTok Developer App heeft GEEN 'video.upload' scope. "
                            "Oplos: ga naar developers.tiktok.com → jouw app → Products → voeg 'Content Posting API' toe → "
                            "sla op → draai opnieuw: python scripts/get_tiktok_token.py"
                        ) from e2
                    raise
            else:
                raise

        # Stap 2: Upload video
        self._upload_video(video_path, upload_url)

        logger.success(f"[TikTok] Gepubliceerd! Publish ID: {publish_id}")
        return publish_id

    def _init_upload(self, video_path: Path, caption: str) -> tuple[str, str]:
        file_size = video_path.stat().st_size
        post_info: dict = {
            "title": caption[:2200],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        }
        if self.post_mode == "DIRECT_POST":
            post_info["post_mode"] = "DIRECT_POST"

        with httpx.Client(timeout=30) as client:
            response = client.post(
                self.UPLOAD_INIT_URL,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                json={
                    "post_info": post_info,
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": file_size,
                        "chunk_size": file_size,
                        "total_chunk_count": 1,
                    },
                },
            )
            if response.status_code == 400 and self.post_mode == "DIRECT_POST":
                err = response.json().get("error", {})
                if "scope" in str(err).lower() or "permission" in str(err).lower():
                    logger.warning(
                        "[TikTok] DIRECT_POST mislukt (scope niet goedgekeurd). "
                        "Voeg 'Content Posting API' product toe in TikTok Developer Console. "
                        "Valt terug op INBOX modus."
                    )
                    post_info.pop("post_mode", None)
                    response = client.post(
                        self.UPLOAD_INIT_URL,
                        headers={
                            "Authorization": f"Bearer {self.access_token}",
                            "Content-Type": "application/json; charset=UTF-8",
                        },
                        json={"post_info": post_info, "source_info": {
                            "source": "FILE_UPLOAD",
                            "video_size": file_size,
                            "chunk_size": file_size,
                            "total_chunk_count": 1,
                        }},
                    )
            response.raise_for_status()
            data = response.json()["data"]
            return data["publish_id"], data["upload_url"]

    def _upload_video(self, video_path: Path, upload_url: str) -> None:
        video_data = video_path.read_bytes()
        file_size = len(video_data)
        with httpx.Client(timeout=120) as client:
            response = client.put(
                upload_url,
                content=video_data,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    "Content-Length": str(file_size),
                },
            )
            response.raise_for_status()
        logger.info(f"[TikTok] Video geüpload ({file_size / 1024 / 1024:.1f} MB)")
