"""
VisionStory Provider — talking head video generatie via VisionStory.ai API.
Documentatie: https://openapi.visionstory.ai

Gebruik: AI avatar praat rechtstreeks naar camera met voiceover tekst.
TTS via VisionStory (ElevenLabs voices) → 9:16 avatar video → TikTok post-processing.

Post-processing pipeline:
  VisionStory 480p 9:16 mp4
    → FFmpeg: upscale naar 1080×1920
    → Word-by-word CapCut-stijl captions (Montserrat Bold)
    → Achtergrondmuziek (−14 dB, fade in/out)
    → Eindresultaat: TikTok-ready 1080×1920 mp4

Credits: ~2 credits per 30 sec video (€10/maand = 130+ credits)
"""

import base64
import os
import random
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
from loguru import logger

ROOT = Path(__file__).parent.parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"

BASE_URL = "https://openapi.visionstory.ai"

# TikTok output dimensies
_OUT_W = 1080
_OUT_H = 1920

# Standaard avatar — eerste publieke avatar die 9:16 ondersteunt
# Stel VISIONSTORY_AVATAR_ID in .env in om een andere avatar te kiezen
_DEFAULT_AVATAR_ID = "4013268338192629600"

# Standaard stem — Alice (British Female, multilingual ElevenLabs)
# Ondersteunt Nederlands via ElevenLabs multilingual model
_DEFAULT_VOICE_ID = "Alice"


# ── Helpers ────────────────────────────────────────────────────────

def _get_font_path(extra_bold: bool = False) -> str:
    name = "Montserrat-ExtraBold.ttf" if extra_bold else "Montserrat-Bold.ttf"
    p = FONT_DIR / name
    if p.exists():
        return str(p).replace("\\", "/").replace(":", "\\:")
    return ""


def _escape_drawtext(text: str) -> str:
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\u2019")
        .replace(":", "\\:")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(";", "\\;")
        .replace("%", "%%")
        .replace("\n", "\\n")
    )


def _get_duration(path: Path) -> float:
    """Haal videoduur op via ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── VisionStoryProvider ────────────────────────────────────────────

class VisionStoryProvider:
    """Genereert talking head video's via VisionStory.ai API + TikTok post-processing."""

    COST_PER_VIDEO_USD = 0.08  # ~2 credits ≈ €10/130 credits ≈ $0.08

    def __init__(self):
        self.api_key = os.getenv("VISIONSTORY_API_KEY") or os.getenv("DID_API_KEY", "")
        # Accepteer sk-vs- keys — DID_API_KEY kan een VisionStory key bevatten
        if self.api_key and not self.api_key.startswith("sk-vs-"):
            # Als het een colon-encoded D-ID key is, niet bruikbaar voor VisionStory
            if ":" in self.api_key and not self.api_key.startswith("sk-vs-"):
                self.api_key = ""
        self.total_cost_usd = 0.0

    @property
    def _headers(self) -> dict:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
        }

    # ── Public ────────────────────────────────────────────────────

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        if not self.api_key:
            raise ValueError("Geen VisionStory API key — stel VISIONSTORY_API_KEY of DID_API_KEY (sk-vs-...) in .env in")

        video_id = str(uuid.uuid4())[:8]
        raw_path = output_dir / f"vs_raw_{video_id}.mp4"
        final_path = output_dir / f"vs_{video_id}.mp4"

        voiceover_text = self._extract_voiceover(script)
        duration_sec = script.get("total_duration_sec", 45)

        logger.info(f"[VisionStory] Genereer talking head ({duration_sec}s, {len(voiceover_text)} tekens)...")

        # Stap 1: VisionStory video genereren
        vs_video_id = self._create_video(voiceover_text, memory)
        video_url = self._poll_video(vs_video_id, max_wait_sec=900)
        self._download_video(video_url, raw_path)

        # Stap 2: TikTok post-processing (upscale, captions, muziek)
        work_dir = output_dir / f"vs_work_{video_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._postprocess_for_tiktok(raw_path, final_path, voiceover_text, script, work_dir)
        except Exception as e:
            logger.warning(f"[VisionStory] Post-processing mislukt ({e}), gebruik ruwe video")
            shutil.copy(str(raw_path), str(final_path))
        finally:
            raw_path.unlink(missing_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)

        self.total_cost_usd += self.COST_PER_VIDEO_USD
        logger.success(f"[VisionStory] Video klaar: {final_path} | kosten=${self.total_cost_usd:.3f}")
        return final_path

    # ── VisionStory API ────────────────────────────────────────────

    def _extract_voiceover(self, script: dict) -> str:
        text = script.get("full_voiceover_text", "")
        if not text:
            scenes = script.get("scenes", [])
            text = " ".join(s.get("voiceover", "") for s in scenes if s.get("voiceover"))
        return text.strip()

    def _generate_elevenlabs_audio(self, text: str) -> bytes | None:
        """Genereer audio met ElevenLabs — Nadia stem voor GLP Coach."""
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key or len(api_key) < 10:
            return None

        # Gebruik VISIONSTORY_ELEVENLABS_VOICE_ID als expliciet ingesteld,
        # anders de standaard ELEVENLABS_VOICE_ID (vrouwelijk, past bij default avatar)
        # NIET de clone-stem — die is mannelijk en past niet bij de vrouwelijke avatar
        voice_id = (
            os.getenv("VISIONSTORY_ELEVENLABS_VOICE_ID", "").strip()
            or os.getenv("ELEVENLABS_VOICE_ID", "9BWtsMINqrJLrRacOk9x").strip()
        )

        logger.info(f"[VisionStory] ElevenLabs TTS genereren (voice={voice_id[:8]}...)...")
        try:
            tts_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_192"
            resp = httpx.post(
                tts_url,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.65,
                        "similarity_boost": 0.85,
                        "style": 0.10,
                        "use_speaker_boost": True,
                    },
                    "apply_text_normalization": "auto",
                },
                timeout=60,
            )
            resp.raise_for_status()
            if len(resp.content) < 2000:
                logger.warning(f"[VisionStory] ElevenLabs response te klein ({len(resp.content)} bytes)")
                return None
            logger.info(f"[VisionStory] ElevenLabs audio klaar: {len(resp.content) // 1024} KB")
            return resp.content
        except Exception as e:
            logger.warning(f"[VisionStory] ElevenLabs TTS mislukt: {e}")
            return None

    def _create_video(self, text: str, memory: dict) -> str:
        avatar_id = os.getenv("VISIONSTORY_AVATAR_ID", _DEFAULT_AVATAR_ID)
        voice_id = os.getenv("VISIONSTORY_VOICE_ID", _DEFAULT_VOICE_ID)

        # Bescherm tegen te lange teksten (API limiet ~2000 tekens)
        if len(text) > 1800:
            text = text[:1800].rsplit(" ", 1)[0] + "..."

        # Probeer ElevenLabs audio te gebruiken voor betere stem + lipsync
        el_audio = self._generate_elevenlabs_audio(text)

        if el_audio:
            audio_b64 = base64.b64encode(el_audio).decode("utf-8")
            payload = {
                "avatar_id": avatar_id,
                "audio_script": {
                    "inline_data": {
                        "mime_type": "audio/mpeg",
                        "data": audio_b64,
                    },
                },
                "aspect_ratio": "9:16",
                "emotion": "marketing",
            }
            logger.info(f"[VisionStory] Maak video met avatar={avatar_id}, ElevenLabs audio")
        else:
            payload = {
                "avatar_id": avatar_id,
                "text_script": {
                    "text": text,
                    "voice_id": voice_id,
                },
                "aspect_ratio": "9:16",
                "emotion": "marketing",
            }
            logger.info(f"[VisionStory] Maak video met avatar={avatar_id}, voice={voice_id} (VisionStory TTS)")

        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{BASE_URL}/api/v1/video",
                headers=self._headers,
                json=payload,
            )

        if response.status_code != 200:
            logger.error(f"[VisionStory] API fout {response.status_code}: {response.text[:300]}")
            response.raise_for_status()

        data = response.json()
        if data.get("error"):
            raise RuntimeError(f"[VisionStory] API error: {data['error']}")

        vs_id = data["data"]["video_id"]
        logger.debug(f"[VisionStory] Video aangemaakt: {vs_id}")
        return vs_id

    def _poll_video(self, vs_video_id: str, max_wait_sec: int = 300) -> str:
        start = time.time()
        with httpx.Client(timeout=30) as client:
            while time.time() - start < max_wait_sec:
                response = client.get(
                    f"{BASE_URL}/api/v1/video",
                    headers=self._headers,
                    params={"video_id": vs_video_id},
                )
                response.raise_for_status()
                data = response.json().get("data", {})
                status = data.get("status")

                # VisionStory gebruikt "created" als klaar-status (niet "done")
                if status in ("done", "created"):
                    url = data.get("video_url")
                    if not url:
                        raise RuntimeError(f"[VisionStory] Video klaar maar geen video_url: {data}")
                    logger.info(f"[VisionStory] Video klaar (status={status}): {url[:80]}...")
                    return url

                if status in ("failed", "error"):
                    raise RuntimeError(f"[VisionStory] Video generatie mislukt: {data}")

                elapsed = int(time.time() - start)
                logger.info(f"[VisionStory] Status: {status} ({elapsed}s)...")
                time.sleep(8)

        raise TimeoutError(f"VisionStory video {vs_video_id} niet klaar binnen {max_wait_sec}s")

    def _download_video(self, url: str, output_path: Path) -> None:
        logger.info("[VisionStory] Download video...")
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
        size_kb = output_path.stat().st_size // 1024
        logger.debug(f"[VisionStory] Ruwe video: {output_path} ({size_kb} KB)")

    # ── Post-processing ────────────────────────────────────────────

    def _postprocess_for_tiktok(
        self,
        raw_path: Path,
        output_path: Path,
        voiceover: str,
        script: dict,
        work_dir: Path,
    ) -> None:
        """
        Transformeer VisionStory 480p output naar TikTok-ready 1080×1920.

        Stappen:
        1. Upscale naar 1080×1920 (behoud 9:16 aspect ratio)
        2. CapCut-stijl word-by-word captions
        3. Achtergrondmuziek
        """
        upscaled_path = work_dir / "upscaled.mp4"
        captioned_path = work_dir / "captioned.mp4"

        # Stap 1: Upscale naar 1080×1920
        logger.info("[VisionStory] Post-processing: upscale naar 1080×1920...")
        self._upscale_to_portrait(raw_path, upscaled_path)

        # Stap 2: Captions toevoegen
        logger.info("[VisionStory] Post-processing: captions toevoegen...")
        actual_dur = _get_duration(upscaled_path)
        if actual_dur < 1:
            actual_dur = script.get("total_duration_sec", 45)

        caption_filters = self._build_caption_filters(voiceover, actual_dur)
        if caption_filters:
            self._apply_caption_filters(upscaled_path, captioned_path, caption_filters)
        else:
            shutil.copy(str(upscaled_path), str(captioned_path))

        # Stap 3: Achtergrondmuziek
        logger.info("[VisionStory] Post-processing: muziek toevoegen...")
        self._add_background_music(captioned_path, output_path, script)

        if not output_path.exists() or output_path.stat().st_size < 10000:
            shutil.copy(str(captioned_path), str(output_path))

        logger.info(f"[VisionStory] Post-processing klaar: {output_path.stat().st_size // 1024} KB")

    def _upscale_to_portrait(self, input_path: Path, output_path: Path) -> None:
        """Upscale VisionStory 480p 9:16 video naar 1080×1920."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", f"scale={_OUT_W}:{_OUT_H}:flags=lanczos",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg upscale mislukt: {result.stderr[-300:]}")

    def _build_caption_filters(self, voiceover: str, duration: float) -> list[str]:
        """CapCut-stijl word-by-word captions."""
        if not voiceover or not voiceover.strip():
            return []

        words = voiceover.split()
        if not words:
            return []

        chunks = []
        i = 0
        while i < len(words):
            chunk_size = 3 if len(words[i]) <= 4 else 2
            chunks.append(" ".join(words[i:i + chunk_size]))
            i += chunk_size

        total_chars = sum(len(c) for c in chunks)
        if total_chars == 0:
            return []

        font_path = _get_font_path(extra_bold=True)
        font_spec = f"fontfile='{font_path}':" if font_path else ""

        start_t = 0.3
        end_t = duration - 0.3
        available = end_t - start_t

        filters = []
        t = start_t

        for chunk in chunks:
            chunk_dur = max(0.3, available * (len(chunk) / total_chars))
            chunk_end = min(t + chunk_dur, end_t)
            safe = _escape_drawtext(chunk)

            filters.append(
                f"drawtext=text='{safe}':"
                f"{font_spec}"
                f"fontsize=58:fontcolor=white:"
                f"borderw=6:bordercolor=black@0.95:"
                f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
                f"x=(w-text_w)/2:y=h*0.78:"
                f"enable='between(t,{t:.2f},{chunk_end:.2f})'"
            )
            t = chunk_end

        return filters

    def _apply_caption_filters(
        self, input_path: Path, output_path: Path, filters: list[str]
    ) -> None:
        vf = ",".join(filters)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg captions mislukt: {result.stderr[-300:]}")

    def _add_background_music(
        self, input_path: Path, output_path: Path, script: dict
    ) -> None:
        """Voeg subtiele achtergrondmuziek toe (volume −14 dB)."""
        if not MUSIC_DIR.exists():
            shutil.copy(str(input_path), str(output_path))
            return

        tracks = [t for t in MUSIC_DIR.glob("*.mp3") if t.stat().st_size > 1000]
        if not tracks:
            shutil.copy(str(input_path), str(output_path))
            return

        track = self._pick_music_track(script, tracks)
        video_dur = _get_duration(input_path)
        if not video_dur or video_dur < 3:
            shutil.copy(str(input_path), str(output_path))
            return

        fade_out_start = max(0, video_dur - 2.5)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-stream_loop", "-1",
            "-i", str(track),
            "-filter_complex", (
                f"[1:a]atrim=0:{video_dur},"
                f"volume=0.08,"
                f"afade=t=in:d=1.5,"
                f"afade=t=out:st={fade_out_start:.1f}:d=2.5[music];"
                f"[0:a][music]amix=inputs=2:duration=first:"
                f"dropout_transition=2[aout]"
            ),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.warning(f"[VisionStory] Muziek mix mislukt: {result.stderr[-200:]}")
            shutil.copy(str(input_path), str(output_path))

    def _pick_music_track(self, script: dict, tracks: list[Path]) -> Path:
        vo = (script.get("full_voiceover_text", "") or "").lower()
        track_map = {t.stem: t for t in tracks}

        if any(w in vo for w in ["afvallen", "gewicht", "gezond", "ozempic", "glp", "snack", "dieet", "lichaam", "kilo"]):
            preferred = ["emotional_piano", "upbeat_positive", "warm_corporate"]
        elif any(w in vo for w in ["serieus", "probleem", "stress", "moe", "eerlijk"]):
            preferred = ["cinematic_dark", "ambient_soft"]
        elif any(w in vo for w in ["chill", "rustig", "simpel", "makkelijk"]):
            preferred = ["chill_lofi", "ambient_soft"]
        elif any(w in vo for w in ["snel", "tip", "hack", "wist je", "bizar"]):
            preferred = ["upbeat_positive", "energetic_bright"]
        else:
            preferred = ["warm_corporate", "chill_lofi", "upbeat_positive"]

        for name in preferred:
            if name in track_map:
                return track_map[name]
        return random.choice(tracks)

    # ── Utility ────────────────────────────────────────────────────

    @staticmethod
    def list_avatars() -> list[dict]:
        """Haal beschikbare avatars op (voor configuratie)."""
        api_key = os.getenv("VISIONSTORY_API_KEY") or os.getenv("DID_API_KEY", "")
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{BASE_URL}/api/v1/avatars",
                headers={"X-API-Key": api_key},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("data", {}).get("public_avatars", [])

    @staticmethod
    def get_credits() -> int:
        """Haal resterende credits op."""
        api_key = os.getenv("VISIONSTORY_API_KEY") or os.getenv("DID_API_KEY", "")
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{BASE_URL}/api/v1/billing/credits",
                headers={"X-API-Key": api_key},
            )
            response.raise_for_status()
            return response.json().get("data", {}).get("remaining", 0)
