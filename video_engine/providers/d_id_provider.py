"""
D-ID Provider — talking head video generatie.
Documentatie: https://docs.d-id.com

Gebruik: een UGC creator-persona (bijv. Nour) praat rechtstreeks naar de camera.
TTS via ElevenLabs (multilingual) → D-ID animatie → TikTok 9:16 post-processing.

Post-processing pipeline:
  D-ID 512×512 mp4
    → FFmpeg: 1080×1920 met blurred achtergrond + talking head gecentreerd
    → Word-by-word CapCut-stijl captions (Montserrat Bold)
    → Achtergrondmuziek (−14 dB, fade in/out)
    → Eindresultaat: TikTok-ready 1080×1920 mp4
"""

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

# Standaard presenter — jong, casual (past bij Nour persona)
# Stel DID_PRESENTER_URL in .env in om een eigen foto te gebruiken.
_DEFAULT_PRESENTER = "https://clips-presenters.d-id.com/v2/ella/p9l_fpg2_k/q15Yu1RvRA/image.png"

# ElevenLabs Aria voice (warm, young, multilingual — werkt goed in NL)
_ARIA_VOICE_ID = "9BWtsMINqrJLrRacOk9x"

# Dutch Microsoft TTS fallback
_NL_MICROSOFT_VOICE = "nl-NL-ColetteNeural"

# TikTok output dimensies
_OUT_W = 1080
_OUT_H = 1920
_FACE_SIZE = 900   # Geanimeerd hoofd geschaald naar 900×900


# ── Helpers ───────────────────────────────────────────────────────

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


# ── DIDProvider ───────────────────────────────────────────────────

class DIDProvider:
    """Genereert talking head video's via D-ID API + TikTok post-processing."""

    BASE_URL = "https://api.d-id.com"
    COST_PER_MINUTE = 0.30

    def __init__(self):
        self.api_key = os.getenv("DID_API_KEY")
        self.total_cost_usd = 0.0

    # ── Public ──────────────────────────────────────────────────

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        if not self.api_key:
            raise ValueError("DID_API_KEY niet ingesteld in .env")

        video_id = str(uuid.uuid4())[:8]
        raw_path = output_dir / f"did_raw_{video_id}.mp4"
        final_path = output_dir / f"did_{video_id}.mp4"

        voiceover_text = script.get("full_voiceover_text", "")
        if not voiceover_text:
            scenes = script.get("scenes", [])
            voiceover_text = " ".join(
                s.get("voiceover", "") for s in scenes if s.get("voiceover")
            )

        duration_sec = script.get("total_duration_sec", 45)
        logger.info(f"[D-ID] Genereer talking head ({duration_sec}s, {len(voiceover_text)} tekens)...")

        # Stap 1: D-ID generatie
        talk_id = self._create_talk(voiceover_text, memory)
        video_url = self._poll_talk(talk_id)
        self._download_video(video_url, raw_path)

        # Stap 2: TikTok post-processing (9:16, captions, muziek)
        work_dir = output_dir / f"did_work_{video_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._postprocess_for_tiktok(raw_path, final_path, voiceover_text, script, work_dir)
        except Exception as e:
            logger.warning(f"[D-ID] Post-processing mislukt ({e}), gebruik ruwe video")
            shutil.copy(str(raw_path), str(final_path))
        finally:
            raw_path.unlink(missing_ok=True)
            shutil.rmtree(work_dir, ignore_errors=True)

        self.total_cost_usd += (duration_sec / 60) * self.COST_PER_MINUTE
        logger.success(f"[D-ID] Video klaar: {final_path} | kosten=${self.total_cost_usd:.3f}")
        return final_path

    # ── D-ID API ────────────────────────────────────────────────

    def _build_tts_provider(self, memory: dict) -> dict:
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", _ARIA_VOICE_ID)

        if elevenlabs_key:
            logger.debug(f"[D-ID] TTS: ElevenLabs (voice={voice_id})")
            return {
                "type": "elevenlabs",
                "voice_id": voice_id,
                "voice_config": {
                    "model_id": "eleven_multilingual_v2",
                    "stability": 0.45,
                    "similarity_boost": 0.80,
                    "style": 0.15,
                    "use_speaker_boost": True,
                },
            }

        logger.debug(f"[D-ID] TTS: Microsoft Dutch ({_NL_MICROSOFT_VOICE})")
        return {"type": "microsoft", "voice_id": _NL_MICROSOFT_VOICE}

    def _create_talk(self, script_text: str, memory: dict) -> str:
        presenter_url = os.getenv("DID_PRESENTER_URL", _DEFAULT_PRESENTER)
        tts_provider = self._build_tts_provider(memory)

        with httpx.Client(timeout=60) as client:
            response = client.post(
                f"{self.BASE_URL}/talks",
                headers={
                    "Authorization": f"Basic {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "script": {
                        "type": "text",
                        "input": script_text,
                        "provider": tts_provider,
                    },
                    "config": {
                        "result_format": "mp4",
                        "fluent": True,
                        "pad_audio": 0.0,
                    },
                    "source_url": presenter_url,
                },
            )

        if response.status_code != 201:
            logger.error(f"[D-ID] API fout {response.status_code}: {response.text[:300]}")
            response.raise_for_status()

        talk_id = response.json()["id"]
        logger.debug(f"[D-ID] Talk aangemaakt: {talk_id}")
        return talk_id

    def _poll_talk(self, talk_id: str, max_wait_sec: int = 300) -> str:
        start = time.time()
        with httpx.Client(timeout=30) as client:
            while time.time() - start < max_wait_sec:
                response = client.get(
                    f"{self.BASE_URL}/talks/{talk_id}",
                    headers={"Authorization": f"Basic {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                status = data.get("status")

                if status == "done":
                    url = data.get("result_url") or data.get("video_url")
                    if not url:
                        raise RuntimeError(f"[D-ID] Talk klaar maar geen result_url: {data}")
                    return url

                if status == "error":
                    raise RuntimeError(f"[D-ID] Talk mislukt: {data.get('error', {})}")

                elapsed = int(time.time() - start)
                logger.debug(f"[D-ID] Status: {status} ({elapsed}s)...")
                time.sleep(5)

        raise TimeoutError(f"D-ID talk {talk_id} niet klaar binnen {max_wait_sec}s")

    def _download_video(self, url: str, output_path: Path) -> None:
        logger.info("[D-ID] Download video...")
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
        logger.debug(f"[D-ID] Ruwe video: {output_path} ({output_path.stat().st_size // 1024} KB)")

    # ── Post-processing ─────────────────────────────────────────

    def _postprocess_for_tiktok(
        self,
        raw_path: Path,
        output_path: Path,
        voiceover: str,
        script: dict,
        work_dir: Path,
    ) -> None:
        """
        Transformeer 512×512 D-ID output naar TikTok-ready 1080×1920.

        Stappen:
        1. Blurred achtergrond + talking head gecentreerd bovenaan
        2. CapCut-stijl word-by-word captions
        3. Achtergrondmuziek
        """
        framed_path = work_dir / "framed.mp4"
        captioned_path = work_dir / "captioned.mp4"

        # Stap 1: 9:16 framing
        logger.info("[D-ID] Post-processing: 9:16 framing...")
        self._frame_to_portrait(raw_path, framed_path)

        # Stap 2: Captions toevoegen
        logger.info("[D-ID] Post-processing: captions toevoegen...")
        actual_dur = _get_duration(framed_path)
        if actual_dur < 1:
            actual_dur = script.get("total_duration_sec", 45)

        caption_filters = self._build_caption_filters(voiceover, actual_dur)
        if caption_filters:
            self._apply_caption_filters(framed_path, captioned_path, caption_filters)
        else:
            shutil.copy(str(framed_path), str(captioned_path))

        # Stap 3: Achtergrondmuziek
        logger.info("[D-ID] Post-processing: muziek toevoegen...")
        self._add_background_music(captioned_path, output_path, script)

        if not output_path.exists() or output_path.stat().st_size < 10000:
            # Fallback: gebruik gecaptionede versie zonder muziek
            shutil.copy(str(captioned_path), str(output_path))

        logger.info(f"[D-ID] Post-processing klaar: {output_path.stat().st_size // 1024} KB")

    def _frame_to_portrait(self, input_path: Path, output_path: Path) -> None:
        """
        Zet 512×512 om naar 1080×1920 met blurred achtergrond.

        Layout:
        - Achtergrond: input geschaald naar 1080×1920, gblur=30, verduisterd
        - Voorgrond: input geschaald naar 900×900, gecentreerd horizontaal, y=80
        """
        face_x = (_OUT_W - _FACE_SIZE) // 2  # = 90

        fc = (
            f"[0:v]scale={_FACE_SIZE}:{_FACE_SIZE}[face];"
            f"[0:v]scale={_OUT_W}:{_OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={_OUT_W}:{_OUT_H},"
            f"gblur=sigma=30,"
            f"eq=brightness=-0.25:saturation=0.6[bg];"
            f"[bg][face]overlay={face_x}:80[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-filter_complex", fc,
            "-map", "[out]",
            "-map", "0:a",
            "-s", f"{_OUT_W}x{_OUT_H}",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg framing mislukt: {result.stderr[-300:]}")

    def _build_caption_filters(self, voiceover: str, duration: float) -> list[str]:
        """CapCut-stijl word-by-word captions, gepositioneerd in de ondertitelbalk."""
        if not voiceover or not voiceover.strip():
            return []

        words = voiceover.split()
        if not words:
            return []

        # Groepeer in chunks van 2-3 woorden
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
        """Pas drawtext caption filters toe via FFmpeg."""
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
            logger.warning(f"[D-ID] Muziek mix mislukt: {result.stderr[-200:]}")
            shutil.copy(str(input_path), str(output_path))

    def _pick_music_track(self, script: dict, tracks: list[Path]) -> Path:
        """Kies muziektrack op basis van script mood."""
        vo = (script.get("full_voiceover_text", "") or "").lower()
        track_map = {t.stem: t for t in tracks}

        if any(w in vo for w in ["serieus", "probleem", "stress", "moe", "eerlijk"]):
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
