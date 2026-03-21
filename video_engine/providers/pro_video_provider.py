"""
Pro Video Provider v8 — agency-level ad campaign video pipeline.

Pipeline output per video:
- 1080x1920 master @ 60fps (H.264 high, yuv420p)
- Platform exports: TikTok/Reels/Shorts (juiste codec/bitrate/fps per platform)
- Aspect ratio exports: 9:16, 1:1, 4:5, 16:9 (smart crop / blurred bg)
- SRT + VTT ondertitels (Whisper word-level timing)
- Thumbnails (JPEG, per platform)
- Metadata JSON (per platform)
- Retention tracking (VideoRecord voor feedback loop)

v8 quality upgrade:
- Poppins Bold font (TikTok-native, ronder dan Montserrat)
- Room reverb op voice (multi-tap delay, subtiele kamer-ambience)
- Sidechain ducking (muziek gaat automatisch zachter tijdens spraak)
- Pro CTA overlay (gradient badge, pulse glow, gestaffeld appearance)
- Proportionele borderw (schaalt mee met fontsize, geen vaste 10px)
- Sterkere compressor + limiter op voice chain
- Hogere muziek basis-volume (0.22 ipv 0.15) — ducking regelt de balans

v7: beat-sync, A/B variants, SFX library, retention feedback
v6: safe-zone captions, scene-type kleuren, variabele transities, D-ID
v5: scene-type color grading, gradient, vignette, grain, Ken Burns
v4: achtergrondmuziek, word-by-word captions, ElevenLabs TTS
v1-3: 60fps, Whisper sync, de-esser, stereo

Kosten: ~$0.01-0.05 per video (TTS + optioneel D-ID hook + optioneel AI beelden)
"""

import hashlib
import json as _json
import os
import random
import re
import subprocess
import time
import uuid
from pathlib import Path
from shutil import which
from typing import Callable

from loguru import logger
from utils.runtime_paths import (
    ensure_dir,
    ensure_writable_dir,
    get_app_screenshots_dir,
    get_generated_assets_dir,
    get_runtime_data_dir,
    is_vercel_runtime,
)

try:
    from video_engine.retention_optimizer import RetentionOptimizer, VideoRecord
except ImportError:
    RetentionOptimizer = None
    VideoRecord = None

ROOT = Path(__file__).parent.parent.parent
ASSETS_DIR = ensure_dir(get_generated_assets_dir())
FONT_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"
SFX_DIR = ROOT / "assets" / "sfx"
APP_ASSETS_DIR = ensure_dir(get_app_screenshots_dir())
LUT_DIR = ROOT / "assets" / "luts"
CACHE_DIR = ensure_writable_dir(ROOT / "data" / "pexels_cache", get_runtime_data_dir("pexels_cache"))
PIXABAY_CACHE_DIR = ensure_writable_dir(ROOT / "data" / "pixabay_cache", get_runtime_data_dir("pixabay_cache"))

# Stock cache — 24h geldig
_CACHE_TTL = 86400


def _resolve_ffmpeg_bin() -> str:
    custom = os.getenv("FFMPEG_BINARY", "").strip()
    if custom:
        return custom

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        resolved = get_ffmpeg_exe()
        if resolved:
            return resolved
    except Exception:
        pass

    return which("ffmpeg") or "ffmpeg"


def _resolve_ffprobe_bin(ffmpeg_bin: str) -> str | None:
    custom = os.getenv("FFPROBE_BINARY", "").strip()
    if custom:
        return custom

    ffmpeg_path = Path(ffmpeg_bin)
    sibling_names = ("ffprobe.exe", "ffprobe") if ffmpeg_path.suffix.lower() == ".exe" else ("ffprobe", "ffprobe.exe")
    for name in sibling_names:
        candidate = ffmpeg_path.with_name(name)
        if candidate.exists():
            return str(candidate)

    return which("ffprobe")


def _ensure_binary_on_path(binary: str | None) -> None:
    if not binary:
        return

    bin_dir = str(Path(binary).parent)
    current = os.environ.get("PATH", "")
    if not current:
        os.environ["PATH"] = bin_dir
        return

    path_entries = current.split(os.pathsep)
    if bin_dir not in path_entries:
        os.environ["PATH"] = os.pathsep.join([bin_dir, current])


def _ensure_command_wrapper(command_name: str, target_binary: str | None) -> str | None:
    if not target_binary:
        return None

    target_path = Path(target_binary)
    if target_path.stem.lower() == command_name.lower():
        _ensure_binary_on_path(str(target_path))
        return str(target_path)

    wrapper_dir = ensure_dir(get_runtime_data_dir("bin"))
    if os.name == "nt":
        wrapper = wrapper_dir / f"{command_name}.cmd"
        wrapper.write_text(f'@echo off\r\n"{target_binary}" %*\r\n', encoding="utf-8")
    else:
        wrapper = wrapper_dir / command_name
        wrapper.write_text(f'#!/bin/sh\nexec "{target_binary}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)

    _ensure_binary_on_path(str(wrapper))
    return str(wrapper)


FFMPEG_BIN = _resolve_ffmpeg_bin()
FFPROBE_BIN = _resolve_ffprobe_bin(FFMPEG_BIN)
FFMPEG_BIN = _ensure_command_wrapper("ffmpeg", FFMPEG_BIN) or FFMPEG_BIN
FFPROBE_BIN = _ensure_command_wrapper("ffprobe", FFPROBE_BIN) or FFPROBE_BIN
_ensure_binary_on_path(FFMPEG_BIN)
_ensure_binary_on_path(FFPROBE_BIN)


def _probe_dimensions(path: Path) -> tuple[int, int] | None:
    if FFPROBE_BIN:
        probe = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "stream=width,height",
             "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        try:
            width, height = map(int, probe.stdout.strip().split("x"))
            return width, height
        except (ValueError, AttributeError):
            pass

    probe = subprocess.run(
        [FFMPEG_BIN, "-i", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    match = re.search(r"(\d{2,5})x(\d{2,5})", (probe.stderr or "") + "\n" + (probe.stdout or ""))
    if match:
        return int(match.group(1)), int(match.group(2))
    return None


def _probe_duration_seconds(path: Path) -> float | None:
    if FFPROBE_BIN:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        try:
            return float(result.stdout.strip())
        except (ValueError, AttributeError):
            pass

    result = subprocess.run(
        [FFMPEG_BIN, "-i", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (result.stderr or "") + "\n" + (result.stdout or ""))
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _probe_satavg(image_path: Path) -> float | None:
    if not FFPROBE_BIN:
        return None

    probe = subprocess.run(
        [FFPROBE_BIN, "-v", "quiet",
         "-show_entries", "frame_tags=lavfi.signalstats.SATAVG",
         "-f", "lavfi",
         "-i", f"movie='{str(image_path).replace(chr(92), '/')}',signalstats"],
        capture_output=True, text=True, timeout=10,
    )
    try:
        for line in probe.stdout.splitlines():
            if "SATAVG" in line:
                return float(line.split("=")[-1])
    except Exception:
        return None
    return None


STOCK_INTERMEDIATE_FPS = 30
STOCK_INTERMEDIATE_PRESET = "ultrafast"
STOCK_INTERMEDIATE_CRF = "23"

# Globale FFmpeg thread-limiet — voorkomt dat elk FFmpeg-proces alle cores claimt
_FFMPEG_THREADS = ["2"]


def _visual_fetch_workers() -> int:
    if is_vercel_runtime():
        return 2
    return 2  # Was 4 — verlaagd om memory-gebruik te beperken (Railway OOM)


def _clip_render_workers() -> int:
    """Aantal parallelle FFmpeg clip-renders. Laag houden voor geheugen."""
    if is_vercel_runtime():
        return 1
    return 1  # Was 4 — 1 sequentieel voorkomt OOM op Railway Trial


def _stock_cache_get(query: str, provider: str = "pexels") -> dict | None:
    """Haal gecached stock resultaat op (indien < 24h oud)."""
    cache = PIXABAY_CACHE_DIR if provider == "pixabay" else CACHE_DIR
    key = hashlib.md5(query.lower().strip().encode()).hexdigest()
    path = cache / f"{key}.json"
    if path.exists():
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("_ts", 0) < _CACHE_TTL:
                return data.get("result")
        except Exception:
            pass
    return None


def _stock_cache_set(query: str, result: dict, provider: str = "pexels") -> None:
    """Sla stock resultaat op in cache."""
    cache = PIXABAY_CACHE_DIR if provider == "pixabay" else CACHE_DIR
    key = hashlib.md5(query.lower().strip().encode()).hexdigest()
    path = cache / f"{key}.json"
    try:
        path.write_text(
            _json.dumps({"_ts": time.time(), "query": query, "result": result}),
            encoding="utf-8",
        )
    except Exception:
        pass


# Legacy aliases
_pexels_cache_get = lambda q: _stock_cache_get(q, "pexels")
_pexels_cache_set = lambda q, r: _stock_cache_set(q, r, "pexels")


# ── Font helper ──────────────────────────────────────────────────

# ── Curated Video Library — handmatig geselecteerde Pexels video's ────────
# Elke video is visueel gecontroleerd op kwaliteit en relevantie.
# Per scene-type een lijst van Pexels video IDs die ALTIJD passen.
CURATED_VIDEOS = {
    "health": {
        "hook": [
            12908966,  # Jonge vrouw gefrustreerd aan bureau met telefoon
            8873327,   # Vrouw gestrest achter laptop, close-up
            7591937,   # Vrouw met headset aan computer, professioneel
            8873041,   # Vrouw geconcentreerd achter laptop, kantoor
            8467631,   # Vrouw moe aan bureau, werk
        ],
        "problem": [
            7710697,   # Vrouw met krullend haar sorteert papieren aan bureau
            7273680,   # Vrouw staand, moe en gefrustreerd
            8558445,   # Vrouw hoofd in handen, gestrest
            7149377,   # Vrouw moe achter computer
            9080632,   # Vrouw gestrest met papierwerk
        ],
        "solution": [
            7971669,   # Vrouw blond lachend met telefoon, professioneel
            7967110,   # Vrouw op telefoon in kantoor met planten
            6598883,   # Vrouw lachend op telefoon in bed, intiem
            5496954,   # Vrouw lachend met smartphone
            7011568,   # Vrouw bellend in keuken met koffie
        ],
        "cta": [
            7578155,   # Jonge vrouw lachend, telefoon op statief — TikTok vibe
            5871183,   # Jonge vrouw blond selfie, close-up lachend
            6598883,   # Vrouw lachend op telefoon in bed, intiem
            8279339,   # Vrouw tappend op telefoon, indoor
            6962433,   # Vrouw tappend op telefoon, lachend
        ],
    },
}


def _get_font_path(extra_bold: bool = False) -> str:
    """Get escaped font path for FFmpeg drawtext on Windows.

    Font priority: Poppins (TikTok-native) → Montserrat (fallback).
    Poppins heeft rondere lettervormen die beter passen bij social media content.
    """
    # Poppins = TikTok-native, ronder, moderner dan Montserrat
    primary = "Poppins-ExtraBold.ttf" if extra_bold else "Poppins-Bold.ttf"
    fallback = "Montserrat-ExtraBold.ttf" if extra_bold else "Montserrat-Bold.ttf"
    for name in (primary, fallback):
        path = FONT_DIR / name
        if path.exists():
            return str(path).replace("\\", "/").replace(":", "\\:")
    # Fallback: DejaVu (geïnstalleerd via Dockerfile op Railway/Linux)
    for sys_font in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(sys_font).exists():
            return sys_font
    return ""


# ── Curated Pexels search terms per niche + scene type ───────────

NICHE_SEARCH_TERMS = {
    "health": {
        "hook": [
            "young woman frustrated office computer",
            "young woman tired desk paperwork",
            "young woman exhausted work office",
            "woman stressed typing laptop office",
            "woman overwhelmed desk documents",
        ],
        "problem": [
            "young woman stressed paperwork desk",
            "woman frustrated typing computer office",
            "woman head in hands desk stressed",
            "woman tired late night computer work",
            "woman overwhelmed documents office desk",
        ],
        "solution": [
            "young woman smiling using phone office",
            "woman happy looking phone relieved",
            "woman smiling tablet organized desk",
            "woman relieved phone coffee break",
        ],
        "cta": [
            "young woman tapping phone excited smile",
            "woman using phone happy smiling",
            "woman showing phone screen satisfied",
        ],
    },
    "tech": {
        "hook": [
            "young person frustrated laptop",
            "person staring at phone confused",
            "messy desk multiple screens",
        ],
        "problem": [
            "person overwhelmed notifications phone",
            "student struggling computer late night",
            "person multitasking stressed",
        ],
        "solution": [
            "person using app phone smiling",
            "person organized clean desk productive",
            "young person happy laptop cafe",
        ],
        "cta": [
            "person downloading app phone",
            "hand tapping smartphone screen",
        ],
    },
    "finance": {
        "hook": [
            "young person worried looking at phone",
            "person checking bank account phone",
            "person stressed bills kitchen table",
        ],
        "problem": [
            "person confused money expenses",
            "person stressed budgeting receipts",
            "young adult worried finances laptop",
        ],
        "solution": [
            "person happy finance app phone",
            "person relieved checking savings phone",
            "person organized budgeting smiling",
        ],
        "cta": [
            "person excited phone download",
            "hand tapping phone financial app",
        ],
    },
    "education": {
        "hook": [
            "student tired studying late night",
            "young person bored scrolling phone",
            "student overwhelmed textbooks desk",
        ],
        "problem": [
            "student stressed exam study",
            "person confused reading laptop",
            "messy study desk disorganized notes",
        ],
        "solution": [
            "student happy using study app tablet",
            "person organized studying productive",
            "student smiling phone learning",
        ],
        "cta": [
            "student downloading app phone",
            "person excited tapping phone screen",
        ],
    },
    "food": {
        "hook": [
            "person cooking kitchen home",
            "person scrolling food delivery phone",
            "person staring empty fridge",
        ],
        "problem": [
            "person confused meal planning",
            "messy kitchen overwhelmed cooking",
            "person tired ordering fast food",
        ],
        "solution": [
            "person happy using food app phone",
            "person cooking organized kitchen",
            "person enjoying healthy meal proud",
        ],
        "cta": [
            "person excited downloading food app",
            "hand tapping phone ordering food",
        ],
    },
    "productivity": {
        "hook": [
            "person waking up tired bed morning",
            "person staring at to do list overwhelmed",
            "young person bored routine",
        ],
        "problem": [
            "person procrastinating phone couch",
            "messy desk unfinished tasks",
            "person stressed deadlines laptop",
        ],
        "solution": [
            "person organized using app phone",
            "person productive happy desk clean",
            "person checking tasks phone satisfied",
        ],
        "cta": [
            "person downloading productivity app",
            "hand tapping phone excited",
        ],
    },
    "lifestyle": {
        "hook": [
            "young person morning routine bedroom",
            "person scrolling phone bed lazy",
            "person looking out window thinking",
        ],
        "problem": [
            "person bored same routine daily",
            "person unmotivated couch phone",
            "person feeling stuck uninspired",
        ],
        "solution": [
            "person excited using new app phone",
            "person happy active lifestyle",
            "person smiling self improvement",
        ],
        "cta": [
            "person tapping phone trying new app",
            "young person excited phone screen",
        ],
    },
}

GENERIC_SEARCH_TERMS = {
    "hook": [
        "young person scrolling phone bed",
        "person looking at camera relatable",
        "person morning routine tired",
        "young adult daily life authentic",
    ],
    "problem": [
        "person frustrated looking at phone",
        "person stressed head in hands",
        "person overwhelmed daily tasks",
        "person bored unmotivated couch",
    ],
    "solution": [
        "person happy using phone app",
        "person excited phone screen",
        "person relieved smiling phone",
        "person productive organized happy",
    ],
    "cta": [
        "person tapping phone screen",
        "hand downloading app smartphone",
        "person excited trying new app",
    ],
    "body": [
        "person using smartphone casual",
        "young person daily life authentic",
        "person relaxed using technology",
    ],
}

# Stop words voor keyword extractie uit visual_description
_STOP_WORDS = frozenset({
    "the", "and", "with", "from", "that", "this", "into", "their",
    "they", "them", "have", "has", "been", "will", "would", "could",
    "should", "being", "were", "are", "for", "not", "but", "all",
    "when", "can", "her", "his", "its", "who", "which", "there",
    "then", "some", "very", "just", "about", "also", "shot", "angle",
    "camera", "close-up", "medium", "wide", "lighting", "soft",
    "natural", "background", "blurred", "slightly", "looking",
    "scene", "light", "left", "right", "color", "appears", "clean",
    "setting", "atmosphere", "modern", "bright", "warm", "cool",
})


class ProVideoProvider:
    """Maakt professionele TikTok video's met echte footage en voiceover."""

    COST_PER_IMAGE = 0.02
    COST_PER_TTS_CHAR = 0.015 / 1000
    COST_PER_ELEVENLABS_CHAR = 0.0  # Gratis bij ElevenLabs abonnement

    # ElevenLabs stemmen — 100% native Nederlandse sprekers
    ELEVENLABS_VOICES = {
        "roos": {"id": "7qdUFMklKPaaAVMsBTBt", "desc": "Fris, jong, warm vrouwelijk (NL)"},
        "emma": {"id": "OlBRrVAItyi00MuGMbna", "desc": "Rustig, helder, vrouwelijk (NL)"},
        "melanie": {"id": "SXBL9NbvTrjsJQYay2kT", "desc": "Jong, commercieel, vrouwelijk (NL)"},
        "ido": {"id": "dLPO5AsXc3FZDbTh1IKa", "desc": "Warm, vriendelijk, mannelijk (NL)"},
        "lucas": {"id": "T6sdx9oLQ9xfxeKIi6AM", "desc": "Diep, verhalend, mannelijk (NL)"},
        "arjen": {"id": "62klqbsYqbynbr66ypRt", "desc": "Rustig, betrouwbaar, mannelijk (NL)"},
    }

    # OpenAI stemmen als fallback
    OPENAI_VOICES = {
        "nova": "Warm, vrouwelijk, professioneel",
        "onyx": "Diep, mannelijk, autoritair",
        "alloy": "Neutraal, gebalanceerd",
        "echo": "Warm, conversatie",
        "fable": "Expressief, storytelling",
        "shimmer": "Zacht, vriendelijk",
    }

    # Gecombineerde lijst voor UI
    VOICES = {
        # ElevenLabs (primair — native Nederlands)
        "roos": "Fris, jong, warm vrouwelijk — native NL (ElevenLabs)",
        "emma": "Rustig, helder, vrouwelijk — native NL (ElevenLabs)",
        "melanie": "Jong, commercieel, vrouwelijk — native NL (ElevenLabs)",
        "ido": "Warm, vriendelijk, mannelijk — native NL (ElevenLabs)",
        "lucas": "Diep, verhalend, mannelijk — native NL (ElevenLabs)",
        "arjen": "Rustig, betrouwbaar, mannelijk — native NL (ElevenLabs)",
        # OpenAI fallback
        "nova": "Warm, vrouwelijk (OpenAI)",
        "onyx": "Diep, mannelijk (OpenAI)",
        "alloy": "Neutraal (OpenAI)",
        "echo": "Conversatie (OpenAI)",
        "fable": "Storytelling (OpenAI)",
        "shimmer": "Zacht (OpenAI)",
    }

    def __init__(self, voice: str = "roos", tts_speed: float = 1.0, voice_settings: dict | None = None):
        self.total_cost_usd = 0.0
        self.voice = voice if voice in self.VOICES else "roos"
        self.tts_speed = max(0.5, min(tts_speed, 2.0))
        # Custom voice settings vanuit dashboard (overschrijft defaults)
        self._custom_voice_settings = voice_settings

    @staticmethod
    def _allow_degraded_video() -> bool:
        raw = os.getenv("ALLOW_DEGRADED_VIDEO", "").strip().lower()
        if raw:
            return raw == "true"
        return os.getenv("ENVIRONMENT", "development").lower() != "production"

    def produce(self, script: dict, memory: dict, output_dir: Path, on_progress: Callable | None = None) -> Path:
        """Produceer een complete video met één doorlopende voiceover."""
        def _vprogress(msg):
            if on_progress:
                on_progress(f"  > Video: {msg}")
        vid = str(uuid.uuid4())[:8]
        work_dir = ASSETS_DIR / "work" / vid
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"pro_{vid}.mp4"

        # Bewaar memory als instance var voor LUT en andere scene-aware methods
        self._current_memory = memory

        scenes = script.get("scenes", [])
        if not scenes:
            scenes = [{"voiceover": "Content wordt gegenereerd.", "duration_sec": 5, "type": "hook"}]

        # -- Stap 1: Genereer VOLLEDIGE voiceover als één audio (elimineert scene-cuts)
        _vprogress("Voiceover genereren...")
        full_vo_text = script.get("full_voiceover_text", "")
        if not full_vo_text:
            full_vo_text = " ".join(
                s.get("voiceover", "").strip() for s in scenes if s.get("voiceover", "").strip()
            )

        full_audio, full_duration = self._generate_single_voiceover(full_vo_text, work_dir)

        if not full_duration or full_duration < 3:
            full_duration = float(sum(s.get("duration_sec", 5) for s in scenes))

        # -- Stap 1b: Whisper word-level timestamps (voor caption sync)
        _vprogress("Voiceover klaar, timestamps berekenen...")
        self._word_timestamps = None
        if full_audio and full_audio.exists():
            self._word_timestamps = self._get_word_timestamps(full_audio)

        # -- Stap 2: Verdeel duur met gewogen pacing per scene-type
        # Hook = kort/punchy, problem = laat het even bezinken,
        # solution = tijd voor uitleg, cta = snel/urgent
        _pacing_weights = {
            "hook": 0.80,
            "problem": 1.10,
            "demo": 1.15,       # Extra tijd voor app demo (kijker moet UI zien)
            "feature": 1.10,    # Features moeten rustig getoond worden
            "solution": 1.05,
            "cta": 0.85,
        }
        word_counts = [max(1, len(s.get("voiceover", "").split())) for s in scenes]
        total_words = max(1, sum(word_counts))
        raw_durations = [(wc / total_words) * full_duration for wc in word_counts]

        # Pas pacing weights toe
        scene_durations = []
        for s, raw_dur in zip(scenes, raw_durations):
            st = s.get("type", "body")
            weight = _pacing_weights.get(st, 1.0)
            scene_durations.append(max(2.5, raw_dur * weight))

        # Normaliseer terug naar total duration
        dur_sum = sum(scene_durations)
        if dur_sum > 0 and abs(dur_sum - full_duration) > 0.5:
            scale = full_duration / dur_sum
            scene_durations = [max(2.0, d * scale) for d in scene_durations]

        total_video_duration = sum(scene_durations)

        # -- Stap 2b: Beat-sync — snap scene-grenzen naar muziek beats
        # Detecteer beats in de geselecteerde muziektrack, en verschuif
        # scene-grenzen (max ±0.4s) zodat transities op een beat landen.
        music_track = self._select_music_for_mood(script)
        self._selected_music = music_track  # Cache voor _assemble_final_video
        beats = []
        if music_track and music_track.exists():
            beats = self._detect_beats(music_track)

        if beats and len(scene_durations) > 1:
            # Bereken huidige scene-grenzen (cumulatief)
            boundaries = []
            t = 0.0
            for d in scene_durations[:-1]:  # Laatste scene heeft geen transitie
                t += d
                boundaries.append(t)

            # Snap elke grens naar dichtstbijzijnde beat
            snapped = [self._snap_to_beat(b, beats, max_shift=0.4) for b in boundaries]

            # Herbereken scene duraties vanuit gesnappte grenzen
            new_durations = []
            prev = 0.0
            for sb in snapped:
                new_durations.append(max(2.0, sb - prev))
                prev = sb
            # Laatste scene: resterende tijd
            new_durations.append(max(2.0, total_video_duration - prev))

            shifted = sum(abs(a - b) for a, b in zip(boundaries, snapped))
            if shifted > 0.01:
                scene_durations = new_durations
                logger.info(f"[ProVideo] Beat-sync: {len(snapped)} grenzen gesnapt (totaal {shifted:.2f}s verschoven)")

            # Herbereken offsets
            scene_time_offsets = []
            cumulative = 0.0
            for dur in scene_durations:
                scene_time_offsets.append(cumulative)
                cumulative += dur
            total_video_duration = sum(scene_durations)

        # -- Stap 3: Per-scene visual PARALLEL (saves ~2-3 min vs sequential)
        _vprogress(f"Beeldmateriaal zoeken ({len(scenes)} scenes)...")
        import concurrent.futures
        app_url = memory.get("url", "") if memory else ""
        self._used_video_ids = set()  # Track gebruikte video's voor unieke footage

        # Bereken per-scene timestamps offset voor Whisper sync
        scene_time_offsets = []
        cumulative = 0.0
        for dur in scene_durations:
            scene_time_offsets.append(cumulative)
            cumulative += dur

        app_name = (memory.get("app_name", "") or memory.get("name", "")) if memory else ""
        scene_data = [
            {"scene": scene, "visual": None, "duration": dur, "idx": i,
             "time_offset": offset, "total_duration": total_video_duration,
             "app_name": app_name}
            for i, (scene, dur, offset) in enumerate(
                zip(scenes, scene_durations, scene_time_offsets))
        ]

        def _fetch_visual(sd):
            sd["visual"] = self._get_scene_visual(
                sd["scene"], sd["idx"], memory, work_dir, sd["duration"], app_url
            )
            return sd

        with concurrent.futures.ThreadPoolExecutor(max_workers=_visual_fetch_workers()) as ex:
            scene_data = list(ex.map(_fetch_visual, scene_data))

        # Log welke visuals gevonden zijn
        for sd in scene_data:
            v = sd.get("visual")
            v_ok = v and v.exists() if v else False
            v_size = v.stat().st_size if v_ok else 0
            logger.info(f"[ProVideo] Scene {sd['idx']} visual: {'OK' if v_ok else 'GEEN'} ({v_size} bytes) path={v}")
            _vprogress(f"  > Scene {sd['idx']} visual: {'OK' if v_ok else 'GEEN'} ({v_size} bytes)")

        # -- Stap 4: Visuele clips PARALLEL
        _vprogress(f"Clips renderen ({len(scene_data)} scenes)...")
        total_scenes = len(scene_data)

        def _make_clip(sd):
            try:
                result = self._create_visual_clip(sd, sd["idx"], work_dir, total_scenes)
                if result and result.exists() and result.stat().st_size > 5000:
                    _vprogress(f"  > Clip {sd['idx']}: OK ({result.stat().st_size} bytes)")
                    return result
                # Clip rendering mislukt — gebruik visual direct als noodoplossing
                visual = sd.get("visual")
                raw_clip = work_dir / f"raw_clip_{sd['idx']:02d}.mp4"
                if visual and visual.exists() and visual.stat().st_size > 5000:
                    _vprogress(f"  > Clip {sd['idx']}: effects mislukt, raw visual als fallback")
                    logger.warning(f"[ProVideo] Clip {sd['idx']} effecten mislukt, raw visual fallback")
                    # Detecteer image vs video (alleen extensie-gebaseerd)
                    _IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")
                    is_img = str(visual).lower().endswith(_IMG_EXTS)
                    inp = ["-loop", "1"] if is_img else ["-stream_loop", "-1"]
                    # Scale naar 1080x1920 portrait
                    fb_cmd = [
                        "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                        *inp, "-i", str(visual),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                        "-an", "-t", str(sd["duration"]), "-r", "30",
                        str(raw_clip),
                    ]
                    subprocess.run(fb_cmd, capture_output=True, timeout=60)
                    if raw_clip.exists() and raw_clip.stat().st_size > 5000:
                        _vprogress(f"  > Clip {sd['idx']}: raw fallback OK ({raw_clip.stat().st_size} bytes)")
                        return raw_clip
                    # Probeer origineel raw bestand (pre-processing kan corrupt zijn)
                    raw_stock = work_dir / f"stock_raw_{sd['idx']:02d}.mp4"
                    if raw_stock.exists() and raw_stock.stat().st_size > 50000:
                        _vprogress(f"  > Clip {sd['idx']}: probeer origineel stock ({raw_stock.stat().st_size} bytes)")
                        fb_raw = [
                            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                            "-stream_loop", "-1",
                            "-i", str(raw_stock),
                            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p",
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                            "-an", "-t", str(sd["duration"]), "-r", "30",
                            str(raw_clip),
                        ]
                        subprocess.run(fb_raw, capture_output=True, timeout=60)
                        if raw_clip.exists() and raw_clip.stat().st_size > 5000:
                            _vprogress(f"  > Clip {sd['idx']}: origineel stock OK ({raw_clip.stat().st_size} bytes)")
                            return raw_clip
                    # Zonder loop — misschien is input al juiste lengte
                    fb_cmd2 = [
                        "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                        "-i", str(visual),
                        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-pix_fmt", "yuv420p", "-an",
                        "-t", str(sd["duration"]), "-r", "30",
                        str(raw_clip),
                    ]
                    subprocess.run(fb_cmd2, capture_output=True, timeout=60)
                    if raw_clip.exists() and raw_clip.stat().st_size > 5000:
                        _vprogress(f"  > Clip {sd['idx']}: simple re-encode OK")
                        return raw_clip
                # Allerlaatste noodoplossing: color background video
                _vprogress(f"  > Clip {sd['idx']}: alle fallbacks mislukt, color clip")
                scene_type = sd["scene"].get("type", "body")
                _colors = {"hook": "0x1a1a2e", "problem": "0x16213e", "solution": "0x0f3460", "cta": "0x533483"}
                bg_color = _colors.get(scene_type, "0x1a1a2e")
                color_cmd = [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c={bg_color}:s=1080x1920:d={sd['duration']}:r=30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-pix_fmt", "yuv420p",
                    str(raw_clip),
                ]
                subprocess.run(color_cmd, capture_output=True, timeout=30)
                if raw_clip.exists() and raw_clip.stat().st_size > 1000:
                    _vprogress(f"  > Clip {sd['idx']}: color fallback OK")
                    return raw_clip
                _vprogress(f"  > Clip {sd['idx']}: COMPLEET MISLUKT")
                return None
            except Exception as e:
                _vprogress(f"  > Clip {sd['idx']}: CRASH ({e})")
                logger.error(f"[ProVideo] Clip {sd['idx']} CRASH: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=_clip_render_workers()) as ex:
            clip_results = list(ex.map(_make_clip, scene_data))

        # Bewaar volgorde
        clips = [c for c in clip_results if c and c.exists()]

        if not clips:
            diag = [f"scene {sd['idx']}: visual={'OK' if sd.get('visual') and sd['visual'].exists() else 'GEEN'}" for sd in scene_data]
            raise RuntimeError(f"Geen scene clips geproduceerd. Diagnostiek: {'; '.join(diag)}")

        # -- Stap 5: Concat visuals (normalisatie gebeurt in _concat_visual_clips)
        _vprogress("Video assembleren...")
        raw_video = work_dir / "raw_video.mp4"
        scene_types = [s.get("type", "body") for s in scenes]
        self._concat_visual_clips(clips, raw_video, scene_types=scene_types)

        # -- Stap 6: Combineer video + voiceover + achtergrondmuziek
        self._assemble_final_video(raw_video, full_audio, work_dir, script, output_path)

        # -- Stap 7: Genereer SRT subtitels (naast burned-in captions)
        srt_path = output_path.with_suffix(".srt")
        self._export_srt(script, scene_durations, scene_time_offsets, srt_path)

        logger.success(
            f"[ProVideo] Video klaar: {output_path} | "
            f"{len(clips)} scenes | kosten=${self.total_cost_usd:.3f}"
        )

        # -- Stap 8: Retention tracking — sla productie-metadata op
        if RetentionOptimizer and VideoRecord:
            try:
                hook_scene = next((s for s in scenes if s.get("type") == "hook"), None)
                cta_scene = next((s for s in scenes if s.get("type") == "cta"), None)
                music_name = getattr(self, "_selected_music", None)
                total_dur = self._get_media_duration(output_path) or sum(scene_durations)

                record = VideoRecord(
                    video_id=f"pro_{vid}",
                    hook_duration_sec=scene_durations[0] if scene_durations else 0,
                    total_duration_sec=total_dur,
                    scene_count=len(scenes),
                    scene_types=[s.get("type", "body") for s in scenes],
                    hook_text=(hook_scene.get("voiceover", "") if hook_scene else "")[:100],
                    cta_text=(cta_scene.get("voiceover", "") if cta_scene else "")[:100],
                    music_track=str(music_name.name) if music_name else "",
                    has_beat_sync=bool(getattr(self, "_beat_synced", False)),
                    sfx_count=len([s for s in scenes if s.get("type") in ("hook", "problem", "solution", "cta")]),
                    caption_style="triple_layer_v7",
                    niche=memory.get("niche", "") if memory else "",
                    app_name=memory.get("app_name", "") if memory else "",
                )
                optimizer = RetentionOptimizer()
                optimizer.save_record(record)
            except Exception as e:
                logger.debug(f"[ProVideo] Retention tracking skip: {e}")

        # -- Stap 9: Cleanup work directory (bespaar schijfruimte)
        try:
            import shutil
            shutil.rmtree(str(work_dir), ignore_errors=True)
            logger.info(f"[ProVideo] Work dir opgeruimd: {work_dir}")
        except Exception:
            pass

        return output_path

    def _export_srt(
        self, script: dict, durations: list[float],
        offsets: list[float], output_path: Path,
    ) -> None:
        """Exporteer SRT ondertitelbestand met per-scene voiceover tekst.

        Gebruikt Whisper word timestamps als beschikbaar, anders scene-level timing.
        Genereert ook een .vtt variant voor web gebruik.
        """
        scenes = script.get("scenes", [])
        if not scenes:
            return

        def _fmt_srt_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        def _fmt_vtt_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

        try:
            srt_lines = []
            vtt_lines = ["WEBVTT", ""]
            sub_idx = 1

            all_words = getattr(self, "_word_timestamps", None)

            for i, scene in enumerate(scenes):
                vo = scene.get("voiceover", "").strip()
                if not vo:
                    continue

                offset = offsets[i] if i < len(offsets) else 0.0
                dur = durations[i] if i < len(durations) else 5.0

                if all_words:
                    # Whisper-gebaseerd: splits voiceover in 2-woord chunks met exacte timing
                    scene_words = self._get_scene_whisper_words(vo, offset, dur)
                    if scene_words and len(scene_words) >= 2:
                        j = 0
                        while j < len(scene_words):
                            chunk = scene_words[j:j + 2]
                            text = " ".join(w["word"] for w in chunk)
                            start = offset + chunk[0]["start"]
                            end = offset + chunk[-1]["end"]

                            srt_lines.append(str(sub_idx))
                            srt_lines.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
                            srt_lines.append(text)
                            srt_lines.append("")

                            vtt_lines.append(f"{_fmt_vtt_time(start)} --> {_fmt_vtt_time(end)}")
                            vtt_lines.append(text)
                            vtt_lines.append("")

                            sub_idx += 1
                            j += 2
                        continue

                # Fallback: hele scene als één subtitle
                start = offset
                end = offset + dur
                srt_lines.append(str(sub_idx))
                srt_lines.append(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}")
                srt_lines.append(vo)
                srt_lines.append("")

                vtt_lines.append(f"{_fmt_vtt_time(start)} --> {_fmt_vtt_time(end)}")
                vtt_lines.append(vo)
                vtt_lines.append("")

                sub_idx += 1

            if srt_lines:
                output_path.write_text("\n".join(srt_lines), encoding="utf-8")
                # VTT naast SRT
                vtt_path = output_path.with_suffix(".vtt")
                vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")
                logger.info(f"[ProVideo] Subtitels geëxporteerd: {output_path.name} + .vtt")

        except Exception as e:
            logger.debug(f"[ProVideo] SRT export mislukt: {e}")

    # ── Platform-Specifieke Exports ──────────────────────────────

    PLATFORM_SPECS = {
        "tiktok": {
            "max_duration": 60,
            "resolution": "1080x1920",
            "fps": 30,
            "video_bitrate": "6M",
            "audio_bitrate": "128k",
            "codec": "libx264",
            "pix_fmt": "yuv420p",
            "profile": "high",
            "level": "4.0",
            "faststart": True,
            "max_size_mb": 287,
        },
        "reels": {
            "max_duration": 90,
            "resolution": "1080x1920",
            "fps": 30,
            "video_bitrate": "5M",
            "audio_bitrate": "128k",
            "codec": "libx264",
            "pix_fmt": "yuv420p",
            "profile": "high",
            "level": "4.0",
            "faststart": True,
            "max_size_mb": 250,
        },
        "shorts": {
            "max_duration": 60,
            "resolution": "1080x1920",
            "fps": 30,
            "video_bitrate": "8M",
            "audio_bitrate": "192k",
            "codec": "libx264",
            "pix_fmt": "yuv420p",
            "profile": "high",
            "level": "4.1",
            "faststart": True,
            "max_size_mb": 256,
        },
    }

    def export_for_platform(
        self, video_path: Path, platform: str,
        output_dir: Path | None = None,
    ) -> Path | None:
        """Re-encode video met platform-optimale settings.

        Platforms: 'tiktok', 'reels' (Instagram), 'shorts' (YouTube).

        Optimaliseert:
        - Codec/profiel (H.264 high, yuv420p voor compatibiliteit)
        - Bitrate (per platform max)
        - Faststart (moov atom vooraan voor snelle playback)
        - Max duur (trim als nodig)
        - Thumbnail (eerste frame als JPEG)
        - Metadata JSON (voor upload automation)
        """
        platform = platform.lower().strip()
        if platform not in self.PLATFORM_SPECS:
            logger.warning(f"[ProVideo] Onbekend platform: {platform}")
            return None

        if not video_path or not video_path.exists():
            return None

        specs = self.PLATFORM_SPECS[platform]
        out_dir = output_dir or video_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = video_path.stem
        out_path = out_dir / f"{stem}_{platform}.mp4"

        # FFmpeg re-encode met platform specs
        cmd = [
            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            "-i", str(video_path),
            "-c:v", specs["codec"],
            "-profile:v", specs["profile"],
            "-level:v", specs["level"],
            "-pix_fmt", specs["pix_fmt"],
            "-b:v", specs["video_bitrate"],
            "-maxrate", specs["video_bitrate"],
            "-bufsize", str(int(specs["video_bitrate"].replace("M", "")) * 2) + "M",
            "-r", str(specs["fps"]),
            "-c:a", "aac", "-b:a", specs["audio_bitrate"],
            "-ar", "44100", "-ac", "2",
        ]

        if specs.get("faststart"):
            cmd += ["-movflags", "+faststart"]

        # Trim als video te lang is voor platform
        dur = self._get_media_duration(video_path)
        if dur and dur > specs["max_duration"]:
            cmd += ["-t", str(specs["max_duration"])]
            logger.info(f"[ProVideo] Video getrimd naar {specs['max_duration']}s voor {platform}")

        cmd.append(str(out_path))

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                logger.error(f"[ProVideo] Platform export mislukt: {result.stderr[-300:]}")
                return None
        except subprocess.TimeoutExpired:
            logger.error(f"[ProVideo] Platform export timeout voor {platform}")
            return None

        if not out_path.exists():
            return None

        # Check bestandsgrootte
        size_mb = out_path.stat().st_size / (1024 * 1024)
        if size_mb > specs["max_size_mb"]:
            logger.warning(
                f"[ProVideo] {platform} export {size_mb:.1f}MB overschrijdt "
                f"limiet {specs['max_size_mb']}MB — hercompressie nodig"
            )

        # Genereer thumbnail — best-frame selectie
        # Extraheert 5 kandidaat-frames verspreid over de video,
        # selecteert het frame met de hoogste visuele kwaliteit
        # (contrast × kleurvariatie). Vermijdt zwarte/flash frames.
        thumb_path = out_dir / f"{stem}_{platform}_thumb.jpg"
        vid_dur = self._get_media_duration(out_path) or 10.0
        best_thumb = None
        best_score = -1

        # Sample 5 frames op 15%, 30%, 45%, 60%, 75% van de video
        for ti, pct in enumerate([0.15, 0.30, 0.45, 0.60, 0.75]):
            ts = vid_dur * pct
            candidate = out_dir / f"_thumb_candidate_{ti}.jpg"
            subprocess.run([
                "ffmpeg", "-y", "-ss", f"{ts:.2f}",
                "-i", str(out_path),
                "-vframes", "1", "-q:v", "2",
                str(candidate),
            ], capture_output=True, timeout=15)

            if candidate.exists() and candidate.stat().st_size > 5000:
                # Score berekenen via FFmpeg signalstats (gemiddelde saturatie)
                # Fallback: gebruik bestandsgrootte als kwaliteitsindicator
                # (meer detail = meer bytes bij zelfde JPEG kwaliteit)
                score = candidate.stat().st_size
                sat = _probe_satavg(candidate)
                if sat is not None:
                    score = score * (1 + sat / 100)

                if score > best_score:
                    best_score = score
                    best_thumb = candidate

        if best_thumb and best_thumb.exists():
            import shutil
            shutil.copy(str(best_thumb), str(thumb_path))

        # Cleanup kandidaten
        for ti in range(5):
            c = out_dir / f"_thumb_candidate_{ti}.jpg"
            if c.exists():
                c.unlink()

        # Metadata JSON voor upload automation
        meta = {
            "platform": platform,
            "source_file": str(video_path.name),
            "export_file": str(out_path.name),
            "thumbnail": str(thumb_path.name) if thumb_path.exists() else None,
            "specs": {
                "resolution": specs["resolution"],
                "fps": specs["fps"],
                "video_bitrate": specs["video_bitrate"],
                "codec": specs["codec"],
            },
            "duration_sec": self._get_media_duration(out_path),
            "size_mb": round(size_mb, 2),
        }
        meta_path = out_dir / f"{stem}_{platform}_meta.json"
        meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")

        logger.info(f"[ProVideo] {platform} export klaar: {out_path.name} ({size_mb:.1f}MB)")
        return out_path

    def export_all_platforms(
        self, video_path: Path, output_dir: Path | None = None,
    ) -> dict[str, Path | None]:
        """Export video voor alle platforms tegelijk.

        Returns: dict met platform -> output path (of None bij fout).
        """
        results = {}
        for platform in self.PLATFORM_SPECS:
            results[platform] = self.export_for_platform(
                video_path, platform, output_dir,
            )
        return results

    # ── Multi-Aspect Ratio Export ────────────────────────────────

    ASPECT_SPECS = {
        "9:16": {"w": 1080, "h": 1920, "label": "Portrait (TikTok/Reels/Shorts)"},
        "1:1": {"w": 1080, "h": 1080, "label": "Square (Instagram Feed/Facebook)"},
        "4:5": {"w": 1080, "h": 1350, "label": "Portrait Feed (Instagram Optimal)"},
        "16:9": {"w": 1920, "h": 1080, "label": "Landscape (YouTube/Ads)"},
    }

    def export_aspect_ratio(
        self, video_path: Path, aspect: str,
        output_dir: Path | None = None,
    ) -> Path | None:
        """Converteer 9:16 video naar ander aspect ratio met smart crop.

        Ondersteunde ratios: '9:16' (origineel), '1:1', '4:5', '16:9'.

        Smart crop strategie:
        - Houdt het verticale centrum (y=0.35-0.70) waar captions en subject staan
        - Voegt blurred achtergrond toe bij landscape (16:9) om zwarte balken te vermijden
        """
        aspect = aspect.strip()
        if aspect not in self.ASPECT_SPECS:
            logger.warning(f"[ProVideo] Onbekend aspect ratio: {aspect}")
            return None

        if not video_path or not video_path.exists():
            return None

        specs = self.ASPECT_SPECS[aspect]
        out_dir = output_dir or video_path.parent
        out_dir.mkdir(parents=True, exist_ok=True)

        stem = video_path.stem
        aspect_label = aspect.replace(":", "x")
        out_path = out_dir / f"{stem}_{aspect_label}.mp4"

        if aspect == "9:16":
            # Origineel — gewoon kopie met re-encode
            import shutil
            shutil.copy(str(video_path), str(out_path))
            return out_path

        target_w, target_h = specs["w"], specs["h"]

        use_filter_complex = False

        if aspect == "16:9":
            # Landscape: blurred achtergrond + origineel video gecentreerd
            # Gebruikt filter_complex vanwege split/overlay pipeline
            use_filter_complex = True
            vf = (
                f"[0:v]split[orig][blur];"
                f"[blur]scale={target_w}:{target_h},boxblur=20:5[bg];"
                f"[orig]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
            )
        else:
            # 1:1 of 4:5: smart crop uit het midden van de 9:16 video
            # Crop y-offset: iets boven centrum om captions zichtbaar te houden
            crop_y_expr = f"(ih-{target_h})*0.35"
            vf = (
                f"crop={target_w}:{target_h}:(iw-{target_w})/2:{crop_y_expr},"
                f"scale={target_w}:{target_h}"
            )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
        ]

        if use_filter_complex:
            cmd += ["-filter_complex", vf, "-map", "[vout]", "-map", "0:a"]
        else:
            cmd += ["-vf", vf]

        cmd += [
            "-c:v", "libx264", "-profile:v", "high",
            "-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M",
            "-r", "30",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(out_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode != 0:
                logger.error(f"[ProVideo] Aspect ratio export mislukt: {result.stderr[-300:]}")
                return None
        except subprocess.TimeoutExpired:
            logger.error(f"[ProVideo] Aspect ratio export timeout")
            return None

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            logger.info(f"[ProVideo] {aspect} export klaar: {out_path.name} ({size_mb:.1f}MB)")
            return out_path

        return None

    def export_all_aspects(
        self, video_path: Path, output_dir: Path | None = None,
    ) -> dict[str, Path | None]:
        """Export video in alle aspect ratios.

        Returns: dict met aspect -> output path.
        """
        results = {}
        for aspect in self.ASPECT_SPECS:
            if aspect == "9:16":
                continue  # Origineel, hoeft niet geconverteerd
            results[aspect] = self.export_aspect_ratio(
                video_path, aspect, output_dir,
            )
        return results

    def generate_post_caption(
        self, script: dict, memory: dict, platform: str = "tiktok",
    ) -> dict:
        """Genereer social media caption + hashtags voor de video post.

        Gebruikt GPT-4o-mini om een platform-specifieke caption te genereren
        met relevante hashtags, CTA tekst, en emoji's.

        Returns: {
            "caption": str,      # Volledige post tekst
            "hashtags": list,    # Lijst van hashtags
            "first_comment": str # Optioneel: hashtags als eerste comment
        }

        Kosten: ~$0.0002 per call (GPT-4o-mini)
        """
        app_name = memory.get("app_name", "")
        niche = memory.get("niche", "")
        hook_text = ""
        for s in script.get("scenes", []):
            if s.get("type") == "hook":
                hook_text = s.get("voiceover", "")
                break

        # Platform-specifieke richtlijnen
        _platform_rules = {
            "tiktok": {
                "max_chars": 2200,
                "max_hashtags": 8,
                "style": "casual, Gen-Z taal, emoji's, vraag stellen",
                "hashtag_style": "mix van breed (#fyp #viral) en niche",
            },
            "reels": {
                "max_chars": 2200,
                "max_hashtags": 15,
                "style": "motiverend, professioneel maar warm, emoji's",
                "hashtag_style": "niche-specifiek, #reels toevoegen",
            },
            "shorts": {
                "max_chars": 100,
                "max_hashtags": 3,
                "style": "kort en krachtig, directe CTA",
                "hashtag_style": "alleen #shorts + 2 niche tags",
            },
        }
        rules = _platform_rules.get(platform, _platform_rules["tiktok"])

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            # Fallback: genereer template-based caption
            return self._template_caption(app_name, niche, platform)

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)

            prompt = (
                f"Schrijf een {platform} post caption in het NEDERLANDS voor deze video.\n\n"
                f"App: {app_name}\n"
                f"Niche: {niche}\n"
                f"Hook van de video: \"{hook_text}\"\n\n"
                f"Regels:\n"
                f"- Schrijfstijl: {rules['style']}\n"
                f"- Max {rules['max_hashtags']} hashtags\n"
                f"- Hashtag stijl: {rules['hashtag_style']}\n"
                f"- Gebruik de hook om nieuwsgierigheid te wekken\n"
                f"- Eindig met een CTA (link in bio / probeer gratis)\n"
                f"- Max {rules['max_chars']} karakters totaal\n\n"
                f"Format je antwoord als JSON:\n"
                f'{{"caption": "...", "hashtags": ["#tag1", "#tag2"], "first_comment": "..."}}'
            )

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Je bent een Nederlandse social media specialist. Antwoord ALLEEN in valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.8,
                max_tokens=400,
            )

            content = response.choices[0].message.content.strip()
            # Strip markdown codeblock als aanwezig
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0]

            result = _json.loads(content)
            logger.info(f"[ProVideo] Caption gegenereerd voor {platform}: {len(result.get('hashtags', []))} hashtags")
            return result

        except Exception as e:
            logger.warning(f"[ProVideo] Caption generatie mislukt: {e}")
            return self._template_caption(app_name, niche, platform)

    def _template_caption(
        self, app_name: str, niche: str, platform: str,
    ) -> dict:
        """Fallback caption template als GPT niet beschikbaar is."""
        _niche_tags = {
            "health": ["#zorg", "#gezondheid", "#healthcare", "#admin", "#tijdbesparing"],
            "tech": ["#tech", "#saas", "#software", "#productiviteit", "#startup"],
            "finance": ["#fintech", "#geld", "#besparen", "#financien", "#slim"],
            "education": ["#onderwijs", "#leren", "#edtech", "#studeren", "#kennis"],
        }
        base_tags = ["#fyp", "#viral", "#nederland"]
        niche_tags = _niche_tags.get(niche, ["#app", "#tool"])
        tags = (base_tags + niche_tags)[:8]

        caption = (
            f"Dit verandert alles 🔥\n\n"
            f"Geen eindeloos papierwerk meer.\n"
            f"{app_name} doet het voor je ✨\n\n"
            f"Link in bio → Probeer het gratis 👆\n\n"
            + " ".join(tags)
        )
        return {
            "caption": caption,
            "hashtags": tags,
            "first_comment": " ".join(niche_tags + ["#app", "#gratis", "#tip"]),
        }

    def produce_variants(
        self, script: dict, memory: dict, output_dir: Path,
        num_hooks: int = 3, num_ctas: int = 2,
    ) -> list[Path]:
        """Genereer A/B varianten van een video met verschillende hooks en CTA's.

        Produceert num_hooks × num_ctas varianten door:
        1. Hook scene voiceover te variëren (GPT genereert alternatieven)
        2. CTA scene voiceover te variëren
        3. Andere stock footage per variant (random selection)

        Returns: lijst van video paden (max num_hooks × num_ctas)
        """
        import copy

        scenes = script.get("scenes", [])
        if not scenes:
            return [self.produce(script, memory, output_dir)]

        # Identificeer hook en CTA scenes
        hook_idx = next((i for i, s in enumerate(scenes) if s.get("type") == "hook"), None)
        cta_idx = next((i for i, s in enumerate(scenes) if s.get("type") == "cta"), None)

        if hook_idx is None or cta_idx is None:
            return [self.produce(script, memory, output_dir)]

        # Genereer hook variaties via GPT (als beschikbaar)
        hook_variants = self._generate_hook_variants(
            scenes[hook_idx].get("voiceover", ""), memory, num_hooks,
        )
        cta_variants = self._generate_cta_variants(
            scenes[cta_idx].get("voiceover", ""), memory, num_ctas,
        )

        results = []
        for hi, hook_vo in enumerate(hook_variants):
            for ci, cta_vo in enumerate(cta_variants):
                variant_script = copy.deepcopy(script)
                variant_script["scenes"][hook_idx]["voiceover"] = hook_vo
                variant_script["scenes"][cta_idx]["voiceover"] = cta_vo

                # Rebuild full voiceover text
                variant_script["full_voiceover_text"] = " ".join(
                    s.get("voiceover", "").strip()
                    for s in variant_script["scenes"]
                    if s.get("voiceover", "").strip()
                )

                variant_dir = output_dir / f"variant_{hi+1}h_{ci+1}c"
                variant_dir.mkdir(parents=True, exist_ok=True)

                try:
                    path = self.produce(variant_script, memory, variant_dir)
                    results.append(path)
                    logger.info(f"[ProVideo] Variant {hi+1}h_{ci+1}c klaar: {path}")
                except Exception as e:
                    logger.warning(f"[ProVideo] Variant {hi+1}h_{ci+1}c mislukt: {e}")

        return results if results else [self.produce(script, memory, output_dir)]

    def _generate_hook_variants(
        self, original_hook: str, memory: dict, count: int,
    ) -> list[str]:
        """Genereer hook varianten via GPT (of simpele text manipulatie als fallback)."""
        variants = [original_hook]  # Origineel altijd als eerste

        if count <= 1:
            return variants

        # Probeer GPT variaties
        try:
            import openai
            api_key = os.getenv("OPENAI_API_KEY")
            if api_key:
                client = openai.OpenAI(api_key=api_key)
                app_name = memory.get("app_name", "") if memory else ""
                niche = memory.get("niche", "") if memory else ""

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "system",
                        "content": (
                            f"Je bent een TikTok copywriter voor {app_name} ({niche}). "
                            "Schrijf korte, pakkende hook-varianten in het Nederlands. "
                            "Zelfde boodschap, andere invalshoek. Max 2 zinnen per variant."
                        ),
                    }, {
                        "role": "user",
                        "content": (
                            f"Originele hook: \"{original_hook}\"\n\n"
                            f"Schrijf {count - 1} alternatieve hooks. "
                            "Eén per regel, zonder nummering."
                        ),
                    }],
                    temperature=0.9,
                    max_tokens=500,
                )

                lines = response.choices[0].message.content.strip().split("\n")
                for line in lines:
                    line = line.strip().strip("-•").strip()
                    if line and len(line) > 10 and len(variants) < count:
                        variants.append(line)

                logger.info(f"[ProVideo] Hook varianten: {len(variants)} (GPT)")
        except Exception as e:
            logger.debug(f"[ProVideo] Hook GPT varianten mislukt: {e}")

        # Fallback als GPT niet genoeg varianten maakt
        while len(variants) < count:
            variants.append(original_hook)

        return variants[:count]

    def _generate_cta_variants(
        self, original_cta: str, memory: dict, count: int,
    ) -> list[str]:
        """Genereer CTA varianten."""
        variants = [original_cta]

        if count <= 1:
            return variants

        # Simpele CTA variaties (geen GPT nodig — CTA's zijn kort)
        _cta_templates = [
            "Probeer het nu gratis via de link in bio.",
            "Download nu gratis. Link in bio.",
            "Klik op de link in bio en start vandaag.",
            "Ga naar de link in bio. Het is gratis.",
            "Start nu gratis. Link in bio hierboven.",
        ]
        for tpl in _cta_templates:
            if tpl != original_cta and len(variants) < count:
                variants.append(tpl)

        return variants[:count]

    # ── Whisper Word Timestamps ────────────────────────────────────

    def _get_word_timestamps(self, audio_path: Path) -> list[dict] | None:
        """Haal per-woord timestamps op via OpenAI Whisper.

        Returns een lijst van {"word": "hallo", "start": 0.12, "end": 0.45}
        of None als Whisper niet beschikbaar is.
        """
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)

            logger.info("[ProVideo] Whisper word-level timestamps ophalen...")
            with open(audio_path, "rb") as f:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                    language="nl",
                )

            words = []
            if hasattr(transcript, "words") and transcript.words:
                for w in transcript.words:
                    words.append({
                        "word": w.word.strip(),
                        "start": float(w.start),
                        "end": float(w.end),
                    })
                logger.info(f"[ProVideo] Whisper: {len(words)} woorden met timestamps")
            return words if words else None

        except Exception as e:
            logger.warning(f"[ProVideo] Whisper mislukt: {e}")
            return None

    # ── Voiceover ─────────────────────────────────────────────────

    def _is_elevenlabs_voice(self) -> bool:
        """Check of de gekozen stem een ElevenLabs stem is."""
        return self.voice in self.ELEVENLABS_VOICES

    def _generate_scene_audio(
        self, scene: dict, idx: int, work_dir: Path,
    ) -> tuple[Path | None, float | None]:
        """Genereer TTS audio — ElevenLabs (warm NL) of OpenAI fallback."""
        text = scene.get("voiceover", "").strip()
        if not text:
            return None, None

        audio_path = work_dir / f"tts_{idx:02d}.mp3"

        # Probeer ElevenLabs eerst (veel beter Nederlands)
        if self._is_elevenlabs_voice():
            result = self._tts_elevenlabs(text, audio_path, idx)
            if result:
                return result

        # Fallback naar OpenAI
        return self._tts_openai(text, audio_path, idx)

    def _tts_elevenlabs(
        self, text: str, audio_path: Path, idx: int,
    ) -> tuple[Path, float] | None:
        """Genereer audio met ElevenLabs — consistente, natuurlijke stem.

        Quality guard: als de generatie te kort is voor de tekst
        (< 0.8s per 100 chars), retry met hogere stability. Max 2 pogingen.
        """
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key or len(api_key) < 10:
            logger.debug("[ProVideo] Geen ELEVENLABS_API_KEY, skip ElevenLabs")
            return None

        voice_info = self.ELEVENLABS_VOICES.get(self.voice)
        if not voice_info:
            return None

        import httpx

        voice_id = voice_info["id"]
        logger.info(f"[ProVideo] ElevenLabs TTS scene {idx} ({self.voice}): {text[:60]}...")

        # Verwachte minimale duur: ~0.8s per 100 tekens (Nederlands spreektempo)
        min_expected_dur = max(0.5, len(text) * 0.008)

        # Gebruik custom voice settings van dashboard, of defaults
        base_stability = self._custom_voice_settings.get("stability", 0.58) if self._custom_voice_settings else 0.58
        base_similarity = self._custom_voice_settings.get("similarity_boost", 0.92) if self._custom_voice_settings else 0.92
        base_style = self._custom_voice_settings.get("style", 0.45) if self._custom_voice_settings else 0.45

        # Probeer max 2 keer: eerste keer normaal, tweede met hogere stability
        for attempt in range(2):
            try:
                stability = base_stability if attempt == 0 else min(base_stability + 0.14, 0.95)
                voice_settings = {
                    "stability": stability,
                    "similarity_boost": base_similarity,
                    "style": base_style if attempt == 0 else max(base_style - 0.15, 0.10),
                    "use_speaker_boost": True,
                }

                resp = httpx.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json={
                        "text": text,
                        "model_id": "eleven_multilingual_v2",
                        "voice_settings": voice_settings,
                    },
                    timeout=30,
                )
                resp.raise_for_status()

                # Quality check: is het bestand niet te klein?
                if len(resp.content) < 2000:
                    logger.warning(f"[ProVideo] ElevenLabs scene {idx} response te klein ({len(resp.content)} bytes), retry...")
                    continue

                audio_path.write_bytes(resp.content)
                audio_path = self._enhance_voice_audio(audio_path, idx)
                duration = self._get_media_duration(audio_path)

                # Quality check: duur vs verwacht
                if duration < min_expected_dur and attempt == 0:
                    logger.warning(
                        f"[ProVideo] ElevenLabs scene {idx} te kort "
                        f"({duration:.1f}s vs verwacht ≥{min_expected_dur:.1f}s), "
                        f"retry met hogere stability..."
                    )
                    continue

                self.total_cost_usd += len(text) * self.COST_PER_ELEVENLABS_CHAR
                if attempt > 0:
                    self.total_cost_usd += len(text) * self.COST_PER_ELEVENLABS_CHAR
                logger.info(f"[ProVideo] ElevenLabs scene {idx} klaar: {duration:.1f}s (poging {attempt + 1})")
                return audio_path, duration

            except Exception as e:
                logger.warning(f"[ProVideo] ElevenLabs scene {idx} poging {attempt + 1} mislukt: {e}")
                if attempt == 0:
                    continue
                return None

        return None

    def _enhance_voice_audio(self, audio_path: Path, idx: int) -> Path:
        """Verbeter stemkwaliteit — focus op CONSISTENTIE boven effecten.

        Audio chain (v9 — consistency update):
        1. Silence removal — knipt pauzes >0.5s weg (was 0.35, nu milder
           zodat natuurlijke adempauzes behouden blijven)
        2. Highpass 80Hz — verwijder laagfrequent geroemel
        3. De-esser — band-reject 5.5-8kHz, dempt scherpe S/SJ klanken (NL)
        4. Warmth EQ — mid-boost 2.5-4kHz voor presentie op telefoonluidsprekers
        5. Body EQ — lage mids 220Hz voor warmte
        6. Air — subtiele 10kHz boost voor helderheid
        7. Room reverb — korte early reflection (12ms, zeer lage mix)
        8. Compressor — STRAKKERE settings voor gelijkmatig volume
           (threshold lager, ratio hoger = minder dynamiek-verschil tussen runs)
        9. Limiter — hard ceiling -1dB
        10. Loudnorm — broadcast-standaard -14 LUFS

        v9 WIJZIGINGEN t.o.v. v8:
        - Silence threshold verhoogd naar -42dB (was -38dB) zodat zachte
          lettergrepen niet worden weggeknipt
        - Silence duration verhoogd naar 0.5s (was 0.35s) — adempauzes
          zijn natuurlijk, te agressief knippen klinkt gehaast
        - Compressor strakker: threshold 0.04, ratio 5:1, langere release
          → minder verschil in volume tussen zachte en luide passages
        - Removed: dubbele EQ boost op 3kHz die soms scherp klonk
        """
        enhanced = audio_path.parent / f"tts_enhanced_{idx:02d}.mp3"

        # Room reverb: enkele korte reflectie — minimaal effect maar
        # voorkomt het "droge" gevoel dat TTS onnatuurlijk maakt
        room_reverb = (
            "aresample=44100,"
            "asplit=2[dry][wet];"
            "[wet]adelay=12|12,volume=0.05[r1];"
            "[dry][r1]amix=inputs=2:duration=first:weights='0.95 0.05'"
        )

        # Twee-pass: eerst reverb (complexe filter), dan EQ chain
        reverb_path = audio_path.parent / f"tts_reverb_{idx:02d}.mp3"
        cmd_reverb = [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-filter_complex", room_reverb,
            "-c:a", "libmp3lame", "-q:a", "2",
            str(reverb_path),
        ]
        result = subprocess.run(cmd_reverb, capture_output=True, timeout=30)
        reverb_input = reverb_path if (
            result.returncode == 0 and reverb_path.exists()
            and reverb_path.stat().st_size > 1000
        ) else audio_path

        # Tweede pass: EQ chain + compressor + loudnorm
        cmd = [
            "ffmpeg", "-y", "-i", str(reverb_input),
            "-af", (
                # Silence removal: milder dan v8 — behoud natuurlijke pauzes
                "silenceremove=stop_periods=-1:stop_duration=0.5:stop_threshold=-42dB,"
                # Highpass: verwijder ruis/geroemel onder 80Hz
                "highpass=f=80,"
                # De-esser: dempt scherpe S/SJ klanken (Nederlands)
                "equalizer=f=6500:t=q:w=2.0:g=-4,"
                # Warmth: mid-presence boost voor telefoon (subtieler dan v8)
                "equalizer=f=3000:t=q:w=1.5:g=2.0,"
                # Body: lage mids voor warmte
                "equalizer=f=220:t=q:w=1.0:g=1.5,"
                # Air: hoge helderheid zonder scherpte
                "equalizer=f=10000:t=q:w=1.5:g=1.5,"
                # STRAKKE compressor: dit is de key voor consistentie
                # Lagere threshold + hogere ratio = minder volume-variatie
                # Langere release = vloeiendere overgang (geen pumping)
                "acompressor=threshold=0.04:ratio=5:attack=3:release=80:makeup=2,"
                # Hard limiter: voorkom clipping
                "alimiter=limit=0.95:level=false,"
                # Broadcast loudnorm — dit normaliseert ALLE output
                # naar exact hetzelfde volume (-14 LUFS), ongeacht input
                "loudnorm=I=-14:LRA=5:TP=-1.5"
            ),
            "-c:a", "libmp3lame", "-q:a", "2",
            str(enhanced),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode == 0 and enhanced.exists() and enhanced.stat().st_size > 1000:
            return enhanced
        return audio_path  # fallback naar origineel

    def _tts_openai(
        self, text: str, audio_path: Path, idx: int,
    ) -> tuple[Path | None, float | None]:
        """Genereer audio met OpenAI TTS-HD (fallback)."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None, None

        # Map ElevenLabs voice naar OpenAI equivalent als fallback
        voice = self.voice
        if voice in self.ELEVENLABS_VOICES:
            # Fallback mapping: vrouwelijk -> nova, mannelijk -> onyx
            el_desc = self.ELEVENLABS_VOICES[voice].get("desc", "").lower()
            voice = "nova" if "vrouwelijk" in el_desc else "onyx"

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)

            logger.info(f"[ProVideo] OpenAI TTS scene {idx} ({voice}): {text[:60]}...")

            response = client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text,
                response_format="mp3",
                speed=self.tts_speed,
            )
            response.stream_to_file(str(audio_path))

            duration = self._get_media_duration(audio_path)
            self.total_cost_usd += len(text) * self.COST_PER_TTS_CHAR
            logger.info(f"[ProVideo] OpenAI TTS scene {idx} klaar: {duration:.1f}s")
            return audio_path, duration

        except Exception as e:
            logger.warning(f"[ProVideo] OpenAI TTS scene {idx} mislukt: {e}")
            return None, None

    def _tts_azure(self, text: str, audio_path: Path) -> tuple[Path, float] | None:
        """Azure Neural TTS — beste kwaliteit voor Nederlands (gratis 500K tekens/maand).

        Vereist: AZURE_TTS_KEY en optioneel AZURE_TTS_REGION (.env)
        Stemmen: nl-NL-MaartenNeural (man) of nl-NL-ColetteNeural (vrouw)
        Aanmaken: https://portal.azure.com → Cognitive Services → Speech
        """
        api_key = os.getenv("AZURE_TTS_KEY", "")
        if not api_key or len(api_key) < 10:
            return None

        region = os.getenv("AZURE_TTS_REGION", "westeurope")

        # Kies stem op basis van geconfigureerde voice
        el_desc = ""
        if self.voice in self.ELEVENLABS_VOICES:
            el_desc = self.ELEVENLABS_VOICES[self.voice].get("desc", "").lower()
        elif self.voice in self.OPENAI_VOICES:
            el_desc = self.OPENAI_VOICES[self.voice].lower()

        if "vrouwelijk" in el_desc or self.voice in ("nova", "shimmer", "aria", "sarah", "laura"):
            azure_voice = "nl-NL-ColetteNeural"
        else:
            azure_voice = "nl-NL-MaartenNeural"

        # Optioneel: override via env
        azure_voice = os.getenv("AZURE_TTS_VOICE", azure_voice)

        # SSML voor betere intonatie en pauzes
        import xml.sax.saxutils as saxutils
        safe_text = saxutils.escape(text[:5000])
        ssml = (
            f"<speak version='1.0' xml:lang='nl-NL'>"
            f"<voice xml:lang='nl-NL' name='{azure_voice}'>"
            f"<prosody rate='0.95' pitch='+0Hz'>{safe_text}</prosody>"
            f"</voice></speak>"
        )

        try:
            import httpx
            logger.info(f"[ProVideo] Azure TTS ({azure_voice}, {len(text)} tekens)...")
            resp = httpx.post(
                f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": api_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-48khz-192kbitrate-mono-mp3",
                },
                content=ssml.encode("utf-8"),
                timeout=30,
            )
            resp.raise_for_status()
            audio_path.write_bytes(resp.content)
            dur = self._get_media_duration(audio_path)
            if not dur or dur < 1:
                return None
            logger.info(f"[ProVideo] Azure TTS klaar: {dur:.1f}s")
            return audio_path, dur
        except Exception as e:
            logger.warning(f"[ProVideo] Azure TTS mislukt: {e}")
            return None

    @staticmethod
    def _prep_dutch_text(text: str) -> str:
        """Prep tekst voor TTS — consistent tempo, natuurlijke pauzes.

        Doel: ELKE run moet hetzelfde ritme en tempo opleveren.
        Probleem bij lage stability: wisselende pauzes, snel/langzaam.
        Oplossing: normaliseer leestekens zodat TTS minder vrijheid
        heeft om pauzes te variëren.

        Regels v2:
        1. Korte zinnen (< 6 woorden) worden samengevoegd met komma
           → voorkomt staccato-effect
        2. Em-dashes en ellipsis → komma (uniforme pauze)
        3. Opeenvolgende punten opruimen
        4. Getallen uitschrijven
        5. Uitroeptekens normaliseren (max 1, niet 3)
        6. Vraagtekens na hele korte zinnen → punt (voorkomt rare stijging)
        """
        import re

        # Stap 1: Em-dash / en-dash → komma
        text = text.replace('—', ', ')
        text = text.replace('–', ', ')
        text = text.replace(' - ', ', ')

        # Stap 2: Ellipsis → korte pauze (komma, niet punt)
        text = text.replace('...', ',')
        text = text.replace('…', ',')

        # Stap 3: Dubbele/triple leestekens normaliseren
        text = re.sub(r'\.(\s*\.)+', '.', text)
        text = re.sub(r',\s*,', ',', text)
        text = re.sub(r'!{2,}', '!', text)      # !!! → !
        text = re.sub(r'\?{2,}', '?', text)      # ??? → ?
        text = re.sub(r'[!?]\s*[!?]', '.', text)  # !? → .

        # Stap 4: Korte fragmenten samenvoegen
        # "Tien uur. Per week. Weg." → "Tien uur, per week, weg."
        # Dit is de grootste oorzaak van staccato TTS
        sentences = re.split(r'(?<=[.!?])\s+', text)
        merged = []
        i = 0
        while i < len(sentences):
            s = sentences[i].strip()
            if not s:
                i += 1
                continue
            # Als de zin kort is (< 6 woorden) EN de volgende ook kort is,
            # voeg ze samen met een komma ipv punt
            word_count = len(s.split())
            if (word_count <= 5 and i + 1 < len(sentences)
                    and len(sentences[i + 1].split()) <= 5
                    and s[-1] == '.'):
                # Vervang de punt door komma en voeg samen
                merged.append(s[:-1] + ',')
            else:
                merged.append(s)
            i += 1
        text = ' '.join(merged)

        # Stap 5: Verwijder dubbele spaties
        text = re.sub(r'  +', ' ', text)

        # Stap 6: Getallen uitschrijven voor consistente uitspraak
        _num_map = {
            '1': 'een', '2': 'twee', '3': 'drie', '4': 'vier', '5': 'vijf',
            '6': 'zes', '7': 'zeven', '8': 'acht', '9': 'negen', '10': 'tien',
            '11': 'elf', '12': 'twaalf', '13': 'dertien', '14': 'veertien',
            '15': 'vijftien', '16': 'zestien', '17': 'zeventien',
            '18': 'achttien', '19': 'negentien', '20': 'twintig',
            '25': 'vijfentwintig', '30': 'dertig', '40': 'veertig',
            '50': 'vijftig', '60': 'zestig', '75': 'vijfenzeventig',
            '80': 'tachtig', '100': 'honderd', '500': 'vijfhonderd',
            '1000': 'duizend',
        }
        for digit, word in sorted(_num_map.items(), key=lambda x: -len(x[0])):
            text = re.sub(rf'\b{digit}\b', word, text)

        # Stap 7: Percentages en eenheden uitschrijven
        text = re.sub(r'(\d+)%', r'\1 procent', text)
        text = re.sub(r'(\d+)x\b', r'\1 keer', text)

        # Stap 8: Trim witruimte bij leestekens
        text = re.sub(r'\s+([,.])', r'\1', text)

        return text.strip()

    def _generate_single_voiceover(
        self, text: str, work_dir: Path,
    ) -> tuple[Path | None, float | None]:
        """Genereer VOLLEDIGE voiceover als één audio — geen scene-cuts."""
        if not text or not text.strip():
            return None, None

        audio_path = work_dir / "full_voiceover.mp3"
        text = self._prep_dutch_text(text)

        # 1. Probeer Azure Neural TTS (beste Nederlands — nl-NL-MaartenNeural)
        result = self._tts_azure(text, audio_path)
        if result:
            return result

        # 2. ElevenLabs — gekloonde stem heeft prioriteit boven premade
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if api_key and len(api_key) >= 10:
            # Gekloonde stem: ELEVENLABS_CLONE_VOICE_ID overschrijft alles
            clone_id = os.getenv("ELEVENLABS_CLONE_VOICE_ID", "").strip()
            if clone_id:
                voice_id = clone_id
                logger.info(f"[ProVideo] ElevenLabs GEKLOONDE stem ({len(text)} tekens)...")
            elif self._is_elevenlabs_voice():
                voice_id = self.ELEVENLABS_VOICES[self.voice]["id"]
                logger.info(f"[ProVideo] ElevenLabs {self.voice} ({len(text)} tekens)...")
            else:
                voice_id = None

            if voice_id:
                try:
                    import httpx
                    # Consistente voice settings — identiek aan per-scene TTS
                    # Gebruik custom settings van dashboard als beschikbaar.
                    voice_settings = {
                        "stability": (self._custom_voice_settings or {}).get("stability", 0.58),
                        "similarity_boost": (self._custom_voice_settings or {}).get("similarity_boost", 0.92),
                        "style": (self._custom_voice_settings or {}).get("style", 0.45),
                        "use_speaker_boost": True,
                    }

                    resp = httpx.post(
                        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                        headers={
                            "xi-api-key": api_key,
                            "Content-Type": "application/json",
                            "Accept": "audio/mpeg",
                        },
                        json={
                            "text": text[:5000],
                            "model_id": "eleven_multilingual_v2",
                            "voice_settings": voice_settings,
                        },
                        timeout=60,
                    )
                    resp.raise_for_status()
                    audio_path.write_bytes(resp.content)
                    audio_path = self._enhance_voice_audio(audio_path, 0)
                    dur = self._get_media_duration(audio_path)
                    self.total_cost_usd += len(text) * self.COST_PER_ELEVENLABS_CHAR
                    label = "gekloond" if clone_id else self.voice
                    logger.info(f"[ProVideo] ElevenLabs ({label}) klaar: {dur:.1f}s")
                    return audio_path, dur
                except Exception as e:
                    logger.warning(f"[ProVideo] ElevenLabs voiceover mislukt: {e}")

        # 3. OpenAI TTS-HD fallback (onyx klinkt beter voor NL dan nova)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None, None

        voice = self.voice
        if voice in self.ELEVENLABS_VOICES:
            el_desc = self.ELEVENLABS_VOICES[voice].get("desc", "").lower()
            # onyx (diep, mannelijk) klinkt minder robotisch voor NL dan nova
            voice = "fable" if "vrouwelijk" in el_desc else "onyx"

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            logger.info(f"[ProVideo] OpenAI TTS-HD volledige voiceover ({voice}, {len(text)} tekens)...")
            response = client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text[:4096],
                response_format="mp3",
                speed=self.tts_speed,
            )
            response.stream_to_file(str(audio_path))
            dur = self._get_media_duration(audio_path)
            self.total_cost_usd += len(text) * self.COST_PER_TTS_CHAR
            logger.info(f"[ProVideo] OpenAI TTS volledige voiceover klaar: {dur:.1f}s")
            return audio_path, dur
        except Exception as e:
            logger.warning(f"[ProVideo] OpenAI TTS volledig mislukt: {e}")
            return None, None

    def _create_visual_clip(
        self, scene_data: dict, idx: int, work_dir: Path, total_scenes: int,
    ) -> Path | None:
        """Maak visuele clip met captions — GEEN audio (audio komt van full track).

        Visual pipeline v6:
        - Verbeterde kleurcorrectie per scene-type
        - GEEN unsharp mask (versterkt compressie-artefacten van stock footage)
        - Subtielere flash transitie (0.04s, eased opacity)
        - Smoother gradient (10 lagen, exponentieel, minder zichtbare banding)
        - Variabele vignette per scene-type
        - Variabele filmgrain (exposure-aware: minder bij donkere scenes)
        - Scene-specifieke caption kleur (hook=wit, problem=oranje, solution=groen, cta=geel)
        - Safe-zone captions (y=0.65 ipv 0.72 — uit TikTok UI overlay zone)
        - Headline op y=0.18 (uit username overlay zone)
        - Variabele fade timing per scene-type
        - Progress bar bovenaan (dunne voortgangslijn)
        """
        visual = scene_data["visual"]
        scene = scene_data["scene"]
        duration = scene_data["duration"]
        total_duration = scene_data.get("total_duration", 30.0)
        time_offset = scene_data.get("time_offset", 0.0)

        v_size = visual.stat().st_size if visual and visual.exists() else 0
        if not visual or not visual.exists() or v_size < 5000:
            logger.warning(f"[ProVideo] Geen geldige visual voor scene {idx} (size={v_size})")
            return None

        clip_path = work_dir / f"visual_{idx:02d}.mp4"

        font_bold = _get_font_path(extra_bold=False)
        font_extra = _get_font_path(extra_bold=True)
        font_headline = font_extra or font_bold
        font_caption = font_bold

        vf_parts = []
        scene_type = scene.get("type", "body")

        # ── 0a. Normaliseer input naar 1080x1920 — alle filters verwachten dit formaat
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1")

        # ── 1. Kleurcorrectie per scene-type ──────────────────────────
        if scene_type == "hook":
            vf_parts.append("curves=r='0/0 0.25/0.30 0.5/0.58 0.75/0.82 1/1':g='0/0 0.5/0.50 1/1':b='0/0 0.25/0.28 0.5/0.42 1/0.95'")
            vf_parts.append("eq=saturation=1.20:contrast=1.15:brightness=0.02:gamma=0.93")
        elif scene_type == "problem":
            vf_parts.append("curves=r='0/0 0.5/0.44 1/0.92':g='0/0 0.5/0.47 1/0.95':b='0/0 0.5/0.55 1/1'")
            vf_parts.append("eq=saturation=0.80:contrast=1.16:brightness=-0.04:gamma=1.10")
        elif scene_type in ("solution", "demo", "feature"):
            # Demo/feature scenes: helder, schoon, hoge leesbaarheid
            # Subtielere grading zodat de app UI goed zichtbaar blijft
            vf_parts.append("curves=r='0/0 0.25/0.28 0.5/0.58 1/1':g='0/0 0.5/0.55 1/1':b='0/0 0.5/0.42 1/0.88'")
            if scene_type in ("demo", "feature"):
                # Minder agressieve grading voor phone mockups (UI moet leesbaar zijn)
                vf_parts.append("eq=saturation=1.10:contrast=1.04:brightness=0.03:gamma=0.95")
            else:
                vf_parts.append("eq=saturation=1.22:contrast=1.06:brightness=0.06:gamma=0.90")
        else:  # cta
            vf_parts.append("curves=r='0/0 0.5/0.56 1/1':g='0/0 0.5/0.52 1/1':b='0/0 0.5/0.44 1/0.92'")
            vf_parts.append("eq=saturation=1.28:contrast=1.10:brightness=0.04:gamma=0.92")

        # NOTE: unsharp mask VERWIJDERD — stock footage is al gecomprimeerd,
        # sharpening versterkt JPEG/H.264 artefacten (ringing rond randen).

        # ── 1b. Color LUT — cinematic finishing layer ────────────────
        lut_path = self._select_lut_for_scene(scene_type, getattr(self, "_current_memory", None))
        if lut_path:
            lut_escaped = str(lut_path).replace("\\", "/").replace(":", "\\:")
            # LUT als finishing touch, 60% intensity blend met origineel
            vf_parts.append(f"lut3d=file='{lut_escaped}':interp=trilinear")

        # ── 1c. Scene-transition flash — subtielere eased flash ───────
        if idx > 0:
            # Korter (0.04s) en zachter (0.08 alpha) — minder "glitch" gevoel
            vf_parts.append(
                "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.08:t=fill:"
                "enable='between(t,0,0.04)'"
            )

        # ── 2. Gradient onderkant — 10 lagen, smoother exponentieel ───
        # Minder lagen met betere alpha curve = onzichtbare overgang
        for gi in range(10):
            gy = 0.88 - gi * 0.04
            # Kwadratische curve: bodem zwaar, bovenrand nauwelijks zichtbaar
            alpha = 0.06 * ((10 - gi) / 10) ** 1.8
            vf_parts.append(
                f"drawbox=y=ih*{gy:.3f}:w=iw:h=ih*{1 - gy:.3f}:color=black@{alpha:.3f}:t=fill"
            )

        # ── 2b. Top gradient — voor headline leesbaarheid ─────────────
        for gi in range(4):
            alpha = 0.025 * (1 - gi / 4)
            vf_parts.append(
                f"drawbox=y=0:w=iw:h=ih*{(gi + 1) * 0.03:.3f}:color=black@{alpha:.3f}:t=fill"
            )

        # ── 3. Vignette — sterker voor drama, lichter voor positief ───
        # Demo/feature: zeer lichte vignette (UI moet leesbaar zijn)
        _vig = {"hook": "PI/4.5", "problem": "PI/4", "solution": "PI/5.5",
                "demo": "PI/6", "feature": "PI/6", "cta": "PI/5"}
        vf_parts.append(f"vignette={_vig.get(scene_type, 'PI/5')}")

        # ── 4. Filmgrain — variabel per scene-type ────────────────────
        # Demo/feature: minimale grain (scherpe UI)
        _grain = {"hook": 3, "problem": 4, "solution": 2, "demo": 1, "feature": 1, "cta": 2}
        vf_parts.append(f"noise=alls={_grain.get(scene_type, 3)}:allf=t")

        # ── 5. Progress bar — dunne voortgangslijn bovenaan ───────────
        # Toont kijker hoe ver de video is (verhoogt watch-through)
        if total_duration > 0:
            # Lijn loopt van links naar rechts over de video duur
            # Gebruik scene-lokale tijd + offset voor globale progressie
            bar_height = 4
            # Bereken start/eind positie voor deze scene
            progress_start = time_offset / total_duration
            progress_end = (time_offset + duration) / total_duration
            vf_parts.append(
                f"drawbox=x=0:y=0:w=iw*{progress_start:.4f}+iw*{progress_end - progress_start:.4f}*(t/{duration:.2f}):"
                f"h={bar_height}:color=white@0.85:t=fill"
            )

        # ── 6. On-screen headline — safe-zone y=0.18 ─────────────────
        on_screen = scene.get("on_screen_text", "").strip()
        if on_screen and len(on_screen) <= 50:
            if len(on_screen) > 25:
                cut = on_screen[:25].rfind(" ")
                if cut > 10:
                    on_screen = on_screen[:cut]
                else:
                    on_screen = on_screen[:25]

            safe_headline = _escape_drawtext(on_screen)
            font_spec = f"fontfile='{font_headline}':" if font_headline else ""

            if len(on_screen) <= 12:
                h_fontsize = 95
            elif len(on_screen) <= 18:
                h_fontsize = 82
            else:
                h_fontsize = 68

            # Headline glow — fade-in
            vf_parts.append(
                f"drawtext=text='{safe_headline}':"
                f"{font_spec}"
                f"fontsize={h_fontsize + 6}:fontcolor=white@0.25:"
                f"borderw=0:"
                f"x=(w-text_w)/2:y=h*0.18:"
                f"alpha='min(1,(t-0.3)/0.3)':"
                f"enable='between(t,0.3,{duration:.2f})'"
            )
            # Headline tekst
            vf_parts.append(
                f"drawtext=text='{safe_headline}':"
                f"{font_spec}"
                f"fontsize={h_fontsize}:fontcolor=white:"
                f"borderw=6:bordercolor=black:"
                f"shadowcolor=black@0.85:shadowx=5:shadowy=5:"
                f"x=(w-text_w)/2:y=h*0.18:"
                f"alpha='min(1,(t-0.3)/0.3)':"
                f"enable='between(t,0.3,{duration:.2f})'"
            )

        # ── 7. Pro CTA overlay — gelaagd design met pulse effect ─────
        # Ontwerp: gradient achtergrond → tekst badge → app naam → swipe-up pijl
        # Alles verschijnt gestaffeld (0.6s, 0.8s, 1.0s, 1.3s) voor dynamiek
        if scene_type == "cta":
            cta_font_spec = f"fontfile='{font_extra or font_bold}':" if (font_extra or font_bold) else ""
            cta_y_base = "h*0.48"
            cta_appear = 0.6  # CTA verschijnt sneller dan voorheen

            # Layer 1: Donker gradient scrim over heel de scene (sfeer)
            for gi in range(6):
                alpha = 0.08 * (1 - gi / 6) ** 1.5
                vf_parts.append(
                    f"drawbox=y=ih*{0.38 + gi * 0.04:.3f}:w=iw:h=ih*0.30:"
                    f"color=black@{alpha:.3f}:t=fill:"
                    f"enable='between(t,{cta_appear:.1f},{duration:.2f})'"
                )

            # Layer 2: Gekleurde accent box (gradient-achtig via 3 overlapping boxes)
            # Hoofd badge: warm gradient (oranje-geel) met pulse scale-effect
            # Pulse via drawbox w-oscillatie: w schaalt 2% elke 0.8s
            box_w_pct = 0.64
            box_h = 88
            for ci, (color, alpha) in enumerate([
                ("0xFFA500", 0.92),   # oranje kern
                ("0xFFBF40", 0.60),   # gouden glow eromheen
                ("0xFFD700", 0.30),   # gele outer glow
            ]):
                expand = ci * 8  # elke laag 8px groter
                vf_parts.append(
                    f"drawbox=x=iw*{(1 - box_w_pct) / 2 - ci * 0.008:.4f}:"
                    f"y={cta_y_base}-{expand // 2}:"
                    f"w=iw*{box_w_pct + ci * 0.016:.4f}:"
                    f"h={box_h + expand}:"
                    f"color={color}@{alpha:.2f}:t=fill:"
                    f"enable='between(t,{cta_appear:.1f},{duration:.2f})'"
                )

            # Layer 3: CTA tekst — wit op gekleurde achtergrond
            cta_text = _escape_drawtext("START NU GRATIS")
            cta_fs = 54
            vf_parts.append(
                f"drawtext=text='{cta_text}':"
                f"{cta_font_spec}"
                f"fontsize={cta_fs}:"
                f"fontcolor=white:"
                f"borderw=3:bordercolor=black@0.4:"
                f"shadowcolor=black@0.5:shadowx=3:shadowy=3:"
                f"x=(w-text_w)/2:y={cta_y_base}+14:"
                f"alpha='min(1,(t-{cta_appear})/0.2)':"
                f"enable='between(t,{cta_appear:.1f},{duration:.2f})'"
            )

            # Layer 4: Subtekst "Link in bio ↑" onder de badge
            link_text = _escape_drawtext("LINK IN BIO")
            link_appear = cta_appear + 0.3
            vf_parts.append(
                f"drawtext=text='{link_text}':"
                f"{cta_font_spec}"
                f"fontsize=34:fontcolor=white@0.90:"
                f"borderw=5:bordercolor=black@0.6:"
                f"x=(w-text_w)/2:y={cta_y_base}+{box_h + 24}:"
                f"alpha='min(1,(t-{link_appear:.1f})/0.25)':"
                f"enable='between(t,{link_appear:.1f},{duration:.2f})'"
            )

            # Layer 5: App naam — branded, verschijnt als laatste
            app_name = scene_data.get("app_name", "")
            if app_name:
                safe_app = _escape_drawtext(app_name.upper())
                app_appear = cta_appear + 0.5
                vf_parts.append(
                    f"drawtext=text='{safe_app}':"
                    f"{cta_font_spec}"
                    f"fontsize=40:fontcolor=0xFFD700:"
                    f"borderw=5:bordercolor=black:"
                    f"shadowcolor=black@0.7:shadowx=4:shadowy=4:"
                    f"x=(w-text_w)/2:y={cta_y_base}+{box_h + 64}:"
                    f"alpha='min(1,(t-{app_appear:.1f})/0.3)':"
                    f"enable='between(t,{app_appear:.1f},{duration:.2f})'"
                )

            # Layer 6: Pulserende glow-ring om badge (elke 1.2s pulse)
            # Dit is de "klik hier" visual cue die aandacht trekt
            pulse_appear = cta_appear + 0.4
            vf_parts.append(
                f"drawbox=x=iw*{(1 - box_w_pct) / 2 - 0.03:.4f}:"
                f"y={cta_y_base}-14:"
                f"w=iw*{box_w_pct + 0.06:.4f}:"
                f"h={box_h + 28}:"
                f"color=white@'0.15*abs(sin(t*2.6))':t=fill:"
                f"enable='between(t,{pulse_appear:.1f},{duration:.2f})'"
            )

        # ── 8. Captions — scene-type kleur + safe-zone positie ────────
        voiceover = scene.get("voiceover", "")
        scene_words = self._get_scene_whisper_words(voiceover, time_offset, duration)
        caption_filters = self._build_caption_filters(
            voiceover, duration, font_caption,
            whisper_words=scene_words,
            scene_type=scene_type,
        )
        vf_parts.extend(caption_filters)

        # ── 8. Variabele fade timing per scene-type ───────────────────
        # Hook: snelle fade-in (urgentie), demo: langzamere fade (UI focus)
        _fade_in = {"hook": 0.0, "problem": 0.3, "solution": 0.15,
                    "demo": 0.2, "feature": 0.2, "cta": 0.1}
        _fade_out = {"hook": 0.15, "problem": 0.25, "solution": 0.2,
                     "demo": 0.2, "feature": 0.2, "cta": 0.3}
        fade_in = _fade_in.get(scene_type, 0.2) if idx > 0 else 0.0
        fade_out = _fade_out.get(scene_type, 0.2)
        if fade_in > 0:
            vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
        vf_parts.append(f"fade=t=out:st={duration - fade_out:.2f}:d={fade_out}")

        vf_parts.append("format=yuv420p")
        vf = ",".join(vf_parts)

        # Detecteer of input een afbeelding is (alleen op basis van extensie)
        _IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff")
        is_image = str(visual).lower().endswith(_IMG_EXTS)

        # Input flags: -loop 1 voor images, -stream_loop -1 voor video
        if is_image:
            input_flags = ["-loop", "1", "-i", str(visual)]
            logger.info(f"[ProVideo] Clip {idx}: input is afbeelding, gebruik -loop 1")
        else:
            input_flags = ["-stream_loop", "-1", "-i", str(visual)]

        cmd = [
            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            *input_flags,
            "-vf", vf,
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-t", str(duration), "-r", "30",
            str(clip_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0 or not (clip_path.exists() and clip_path.stat().st_size > 5000):
            # Log volledige fout + filter count voor debugging
            stderr_tail = (result.stderr or '')[-500:]
            vf_count = len(vf_parts)
            logger.warning(
                f"[ProVideo] Clip {idx} full effects fout (rc={result.returncode}, "
                f"filters={vf_count}, is_image={is_image}, "
                f"visual_size={visual.stat().st_size if visual.exists() else 0}): {stderr_tail}"
            )

            # Fallback 1: "lite" effects — kleurcorrectie + gradient + vignette (geen drawtext/zoompan/lut)
            lite_vf = []
            # Kleurcorrectie
            if scene_type == "hook":
                lite_vf.append("eq=saturation=1.20:contrast=1.15:brightness=0.02")
            elif scene_type == "problem":
                lite_vf.append("eq=saturation=0.80:contrast=1.16:brightness=-0.04")
            elif scene_type in ("solution", "demo", "feature"):
                lite_vf.append("eq=saturation=1.15:contrast=1.06:brightness=0.04")
            else:
                lite_vf.append("eq=saturation=1.25:contrast=1.10:brightness=0.03")
            # Gradient onderkant (simpeler: 3 lagen)
            for gi in range(3):
                gy = 0.82 - gi * 0.06
                alpha = 0.12 * ((3 - gi) / 3) ** 1.5
                lite_vf.append(f"drawbox=y=ih*{gy:.3f}:w=iw:h=ih*{1 - gy:.3f}:color=black@{alpha:.3f}:t=fill")
            # Vignette
            lite_vf.append("vignette=PI/5")
            # Fade
            if idx > 0:
                lite_vf.append("fade=t=in:st=0:d=0.2")
            lite_vf.append(f"fade=t=out:st={duration - 0.2:.2f}:d=0.2")
            lite_vf.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p")

            cmd_lite = [
                "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                *input_flags,
                "-vf", ",".join(lite_vf),
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-t", str(duration), "-r", "30",
                str(clip_path),
            ]
            fb_lite = subprocess.run(cmd_lite, capture_output=True, text=True, timeout=60)
            if fb_lite.returncode == 0 and clip_path.exists() and clip_path.stat().st_size > 5000:
                logger.info(f"[ProVideo] Clip {idx}: lite effects OK ({clip_path.stat().st_size} bytes)")
            else:
                logger.warning(f"[ProVideo] Clip {idx} lite effects mislukt (rc={fb_lite.returncode}): {(fb_lite.stderr or '')[-200:]}")

                # Fallback 2: simpele scale naar portrait
                cmd_simple = [
                    "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                    *input_flags,
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-t", str(duration), "-r", "30",
                    str(clip_path),
                ]
                fb1 = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=60)
                if fb1.returncode != 0 or not (clip_path.exists() and clip_path.stat().st_size > 5000):
                    logger.warning(f"[ProVideo] Clip {idx} simpele fallback ook mislukt (rc={fb1.returncode}): {(fb1.stderr or '')[-200:]}")
                    # Fallback 3: input zonder filter (alleen re-encode)
                    cmd_ultra = [
                        "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                        *input_flags,
                        "-vf", "scale=1080:1920,format=yuv420p",
                        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-t", str(duration), "-r", "30",
                        str(clip_path),
                    ]
                    fb2 = subprocess.run(cmd_ultra, capture_output=True, text=True, timeout=60)
                    if fb2.returncode != 0:
                        logger.error(f"[ProVideo] Clip {idx} ALLE fallbacks mislukt: {(fb2.stderr or '')[-200:]}")

        # ── 9. Logo watermark overlay — klein logo linksonder ──────────
        # Zoek logo in memory of standaard locaties
        logo_path = scene_data.get("logo_path")
        if not logo_path:
            # Probeer logo uit de Dossiertijd public dir
            for candidate in [
                ROOT.parent / "dossiertijd" / "app" / "public" / "logo.png",
                ROOT / "assets" / "logo.png",
            ]:
                if candidate.exists():
                    logo_path = str(candidate)
                    break

        if logo_path and clip_path.exists() and clip_path.stat().st_size > 5000:
            logo_clip = work_dir / f"visual_logo_{idx:02d}.mp4"
            logo_esc = str(logo_path).replace("\\", "/")
            cmd_logo = [
                "ffmpeg", "-y",
                "-i", str(clip_path),
                "-i", logo_esc,
                "-filter_complex", (
                    # Schaal logo naar 60x60, fade-in na 0.3s
                    f"[1:v]scale=60:60,format=rgba,"
                    f"colorchannelmixer=aa=0.7[logo];"
                    f"[0:v][logo]overlay=x=28:y=main_h-90:"
                    f"enable='between(t,0.3,{duration:.2f})'[out]"
                ),
                "-map", "[out]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-an", "-r", "30",
                str(logo_clip),
            ]
            result_logo = subprocess.run(cmd_logo, capture_output=True, timeout=60)
            if result_logo.returncode == 0 and logo_clip.exists() and logo_clip.stat().st_size > 5000:
                # Vervang originele clip met logo versie
                import shutil
                shutil.move(str(logo_clip), str(clip_path))
                logger.debug(f"[ProVideo] Logo watermark toegevoegd: scene {idx}")

        return clip_path if (clip_path.exists() and clip_path.stat().st_size > 5000) else None

    def _get_scene_whisper_words(
        self, voiceover: str, time_offset: float, duration: float,
    ) -> list[dict] | None:
        """Filter Whisper word timestamps voor deze specifieke scene.

        Matcht woorden uit de voiceover-tekst op de globale Whisper timestamps
        en verschuift ze naar scene-lokale tijd (0..duration).
        """
        all_words = getattr(self, "_word_timestamps", None)
        if not all_words or not voiceover:
            return None

        scene_end = time_offset + duration
        vo_words = voiceover.lower().split()

        # Zoek woorden in het Whisper resultaat die binnen het scene-tijdvenster vallen
        # EN matchen met de voiceover tekst
        scene_words = []
        vo_idx = 0
        for w in all_words:
            if vo_idx >= len(vo_words):
                break
            # Check of dit woord qua tijd in deze scene past
            if w["start"] < time_offset - 0.5:
                continue
            if w["start"] > scene_end + 0.5:
                break

            # Fuzzy match: strip punctuatie en vergelijk
            w_clean = w["word"].lower().strip(".,!?;:\"'()-")
            vo_clean = vo_words[vo_idx].strip(".,!?;:\"'()-")
            if w_clean == vo_clean or w_clean.startswith(vo_clean[:3]):
                scene_words.append({
                    "word": w["word"],
                    "start": max(0, w["start"] - time_offset),
                    "end": min(duration, w["end"] - time_offset),
                })
                vo_idx += 1

        if len(scene_words) >= len(vo_words) * 0.5:
            logger.debug(
                f"[ProVideo] Whisper sync: {len(scene_words)}/{len(vo_words)} "
                f"woorden gematcht voor scene"
            )
            return scene_words
        return None

    def _build_caption_filters(
        self, voiceover: str, duration: float, font_path: str,
        whisper_words: list[dict] | None = None,
        scene_type: str = "body",
    ) -> list[str]:
        """Bouw TikTok-native captions — triple-layer rendering + word highlight.

        v7 caption systeem:
        - Triple-layer rendering: shadow → outline → fill (diepte + leesbaarheid)
        - Dikkere outline (borderw=10) — TikTok-standaard
        - Word-level highlight: actief woord in accent kleur (per-woord drawtext)
        - Bounce-in animatie (fontsize expression)
        - Safe-zone y=0.65
        - Scene-type kleuren + getallen altijd geel
        - Variabele fontsize per scene-type
        """
        import re as _re_cap

        if not voiceover or not voiceover.strip():
            return []

        font_spec = f"fontfile='{font_path}':" if font_path else ""

        # Caption positie — safe-zone (TikTok UI overlay = y>0.75)
        caption_y = "h*0.65"

        # Scene-type caption kleuren — emotionele connotatie
        _scene_colors = {
            "hook": "white",
            "problem": "0xFFB347",   # warm oranje
            "solution": "0x90EE90",  # licht groen
            "demo": "white",         # clean wit voor app demo
            "feature": "0xB794F4",   # zacht paars (brand kleur)
            "cta": "yellow",
        }
        base_color = _scene_colors.get(scene_type, "white")

        # Highlight kleur — accent voor actieve woorden
        _highlight_colors = {
            "hook": "0x00D4FF",      # cyan — aandacht
            "problem": "0xFF6B35",   # diep oranje
            "solution": "0x4FFFB0",  # helder groen
            "demo": "0x6C63FF",      # paars — brand accent
            "feature": "0x6C63FF",   # paars — brand accent
            "cta": "0xFFD700",       # goud
        }
        highlight_color = _highlight_colors.get(scene_type, "0x00D4FF")

        # Scene-type caption fontsize
        # Demo/feature: iets kleiner om ruimte te geven aan phone mockup
        _scene_fontsize = {"hook": 100, "problem": 92, "solution": 96,
                          "demo": 84, "feature": 84, "cta": 100}
        main_fontsize = _scene_fontsize.get(scene_type, 96)
        glow_fontsize = main_fontsize + 6

        # Woord-getallen voor gele highlight
        _num_words = {
            "nul", "een", "één", "twee", "drie", "vier", "vijf", "zes", "zeven",
            "acht", "negen", "tien", "elf", "twaalf", "dertien", "veertien",
            "vijftien", "zestien", "zeventien", "achttien", "negentien",
            "twintig", "dertig", "veertig", "vijftig", "zestig", "zeventig",
            "tachtig", "negentig", "honderd", "duizend",
        }

        def _is_number_word(text: str) -> bool:
            words_in = text.lower().split()
            return bool(_re_cap.search(r'\b\d+\b', text)) or any(
                w.strip(".,!?") in _num_words for w in words_in
            )

        # Static fontsize — bounce expressie verwijderd voor stabiliteit
        def _bounce_fs(base: int, start: float) -> str:
            return str(base)

        def _build_triple_layer(
            text: str, t_start: float, t_end: float,
            fontcolor: str, fs: int,
        ) -> list[str]:
            """Triple-layer caption: shadow → outline → fill.

            Geeft 3 drawtext filters terug voor één caption chunk.
            Dit is de TikTok-native stijl met diepe schaduw.

            v8: borderw schaalt proportioneel met fontsize (10% van fs).
            Voorheen was borderw=10 vast — disproportioneel dik bij kleine tekst.
            """
            safe = _escape_drawtext(text.upper())
            layers = []

            # Proportionele border: ~10% van fontsize, min 6, max 14
            border_w = max(6, min(14, round(fs * 0.10)))

            # Layer 1: outline + shadow (zwarte rand met schaduw)
            layers.append(
                f"drawtext=text='{safe}':"
                f"{font_spec}"
                f"fontsize={_bounce_fs(fs, t_start)}:"
                f"fontcolor=black:"
                f"borderw={border_w}:bordercolor=black:"
                f"shadowcolor=black@0.6:shadowx=4:shadowy=4:"
                f"x=(w-text_w)/2:y={caption_y}:"
                f"enable='between(t,{t_start:.2f},{t_end:.2f})'"
            )
            # Layer 2: fill (gekleurde tekst bovenop)
            layers.append(
                f"drawtext=text='{safe}':"
                f"{font_spec}"
                f"fontsize={_bounce_fs(fs, t_start)}:"
                f"fontcolor={fontcolor}:"
                f"borderw=0:"
                f"x=(w-text_w)/2:y={caption_y}:"
                f"enable='between(t,{t_start:.2f},{t_end:.2f})'"
            )
            return layers

        # ── Whisper-synced captions met word highlight ──
        if whisper_words and len(whisper_words) >= 2:
            filters = []
            i = 0
            while i < len(whisper_words):
                chunk_words = whisper_words[i:i + 2]
                chunk_text = " ".join(w["word"] for w in chunk_words)
                t = chunk_words[0]["start"]
                chunk_end = chunk_words[-1]["end"]
                chunk_end = max(chunk_end, t + 0.3)

                # Bepaal kleur
                if _is_number_word(chunk_text):
                    fontcolor = "yellow"
                else:
                    fontcolor = base_color

                # Triple-layer caption voor het chunk
                filters.extend(
                    _build_triple_layer(chunk_text, t, chunk_end, fontcolor, main_fontsize)
                )

                # Word-level highlight: als er 2 woorden zijn, highlight het
                # actieve woord in accent kleur (per-woord timing)
                if len(chunk_words) == 2 and not _is_number_word(chunk_text):
                    for wi, word_info in enumerate(chunk_words):
                        w_start = word_info["start"]
                        w_end = word_info["end"]
                        w_end = max(w_end, w_start + 0.1)
                        w_text = word_info["word"].upper()
                        w_safe = _escape_drawtext(w_text)

                        # Bereken x-offset: eerste woord links, tweede woord rechts
                        # We gebruiken een overlay die het volledige chunk bedekt
                        # maar alleen het actieve woord kleurt
                        if wi == 0:
                            # Highlight eerste woord — zelfde positie als chunk maar alleen woord 1
                            # Gebruik text_w trick: bereken offset van woord 2 tekst
                            other_word = chunk_words[1]["word"].upper()
                            other_safe = _escape_drawtext(other_word)
                            filters.append(
                                f"drawtext=text='{w_safe}':"
                                f"{font_spec}"
                                f"fontsize={_bounce_fs(main_fontsize, t)}:"
                                f"fontcolor={highlight_color}:"
                                f"borderw=0:"
                                f"x=(w-text_w)/2-(tw('{other_safe}')+{main_fontsize}*0.3)/2:"
                                f"y={caption_y}:"
                                f"enable='between(t,{w_start:.2f},{w_end:.2f})'"
                            )

                i += 2

            logger.debug(f"[ProVideo] TikTok-native captions: {len(filters)//3} chunks")
            return filters

        # ── Fallback: proportionele timing ──
        words = voiceover.split()
        if not words:
            return []

        chunks = []
        i = 0
        while i < len(words):
            chunks.append(" ".join(words[i:i + 2]))
            i += 2

        total_chars = sum(len(c) for c in chunks)
        if total_chars == 0:
            return []

        start_time = 0.15
        end_time = duration - 0.2
        available = max(0.1, end_time - start_time)

        filters = []
        t = start_time

        for chunk in chunks:
            chunk_dur = available * (len(chunk) / total_chars)
            chunk_dur = max(0.25, chunk_dur)
            chunk_end = min(t + chunk_dur, end_time)

            if _is_number_word(chunk):
                fontcolor = "yellow"
            else:
                fontcolor = base_color

            # Triple-layer caption
            filters.extend(
                _build_triple_layer(chunk, t, chunk_end, fontcolor, main_fontsize)
            )

            t = chunk_end

        return filters

    def _concat_visual_clips(
        self, clips: list[Path], output_path: Path,
        scene_types: list[str] | None = None,
    ) -> None:
        """Concat visuele clips — robuust met per-clip pre-normalisatie.

        Strategie: eerst elk clip individueel re-encoden naar exact hetzelfde
        formaat (1080x1920 30fps yuv420p H.264), dan concat demuxer met
        stream copy. Dit omzeilt alle format-mismatch problemen.
        """
        if len(clips) == 1:
            import shutil
            shutil.copy(clips[0], output_path)
            return

        work_dir = clips[0].parent

        # Stap 1: pre-normaliseer elke clip INDIVIDUEEL naar identiek formaat
        safe_clips = []
        for i, clip in enumerate(clips):
            safe_path = work_dir / f"safe_{i:02d}.mp4"
            cmd = [
                "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                "-i", str(clip),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                       "crop=1080:1920,setsar=1,format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-r", "30", "-g", "30",  # keyframe every 30 frames = 1s
                "-video_track_timescale", "15360",
                "-an",
                "-movflags", "+faststart",
                str(safe_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and safe_path.exists() and safe_path.stat().st_size > 1000:
                safe_clips.append(safe_path)
                logger.info(f"[ProVideo] Safe clip {i}: {safe_path.stat().st_size} bytes")
            else:
                logger.warning(f"[ProVideo] Safe clip {i} mislukt, skip: {(res.stderr or '')[-200:]}")

        if not safe_clips:
            raise RuntimeError("Geen clips na normalisatie — concat onmogelijk")

        # Stap 2: concat demuxer met stream copy (alle clips zijn nu identiek formaat)
        concat_file = work_dir / "concat_safe.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for clip in safe_clips:
                safe_path = str(clip).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd_concat = [
            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd_concat, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 5000:
            logger.info(f"[ProVideo] Concat klaar ({len(safe_clips)} clips, stream copy)")
            return

        # Fallback: concat demuxer met re-encode (als stream copy faalt)
        logger.warning(f"[ProVideo] Stream copy concat mislukt, re-encode: {(result.stderr or '')[-200:]}")
        cmd_reencode = [
            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-r", "30", "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result2 = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=180)
        if result2.returncode != 0:
            raise RuntimeError(f"Visuele concat mislukt: {(result2.stderr or '')[-300:]}")

    def _build_sfx_track(
        self, work_dir: Path, script: dict, video_dur: float,
    ) -> Path | None:
        """Genereer sound effects track met scene-specifieke SFX.

        SFX mapping:
        - Scene transitions: whoosh_transition.mp3 (layered sweep + pink noise)
        - Hook text appear: text_pop.mp3 (subtle pop)
        - Problem tension: riser_tension.mp3 (ascending sweep, low volume)
        - Solution reveal: ding_notification.mp3 (harmonic chime + reverb)
        - CTA accent: swoosh_cta.mp3 (pink noise burst)

        SFX worden op de juiste timestamps geplaatst en gemixt tot één track.
        """
        scenes = script.get("scenes", [])
        if not scenes or not SFX_DIR.exists():
            return None

        sfx_files = {
            "whoosh": SFX_DIR / "whoosh_transition.mp3",
            "ding": SFX_DIR / "ding_notification.mp3",
            "swoosh": SFX_DIR / "swoosh_cta.mp3",
            "riser": SFX_DIR / "riser_tension.mp3",
            "pop": SFX_DIR / "text_pop.mp3",
            "bell": SFX_DIR / "notification_bell.mp3",
            "success": SFX_DIR / "success_chime.mp3",
        }

        # Check of SFX bestaan
        available_sfx = {k: v for k, v in sfx_files.items() if v.exists()}
        if not available_sfx:
            return None

        # Bereken scene timestamps
        word_counts = [max(1, len(s.get("voiceover", "").split())) for s in scenes]
        total_words = max(1, sum(word_counts))
        scene_starts = []
        scene_durations = []
        t = 0.0
        for wc in word_counts:
            scene_starts.append(t)
            dur = (wc / total_words) * video_dur
            scene_durations.append(dur)
            t += dur

        # Bouw SFX placements: (sfx_file, timestamp, volume)
        placements = []
        for i, scene in enumerate(scenes):
            st = scene.get("type", "body")
            ts = scene_starts[i] if i < len(scene_starts) else 0.0
            sdur = scene_durations[i] if i < len(scene_durations) else 3.0

            if i > 0 and "whoosh" in available_sfx:
                # Whoosh bij elke scene transitie
                placements.append((available_sfx["whoosh"], ts, 0.30))

            if st == "hook" and "pop" in available_sfx:
                # Pop bij hook text appearance (0.3s na start)
                placements.append((available_sfx["pop"], ts + 0.3, 0.20))

            if st == "problem" and "riser" in available_sfx:
                # Tension riser onder problem scene (fade in)
                placements.append((available_sfx["riser"], ts + 0.5, 0.12))

            if st == "demo":
                # Bell bij app demo start (telefoon verschijnt)
                if "bell" in available_sfx:
                    placements.append((available_sfx["bell"], ts + 0.3, 0.18))
                elif "ding" in available_sfx:
                    placements.append((available_sfx["ding"], ts + 0.4, 0.18))
                # Success chime halverwege demo als scene lang genoeg is
                if "success" in available_sfx and sdur > 5.0:
                    placements.append((available_sfx["success"], ts + sdur * 0.55, 0.12))

            if st == "feature":
                # Pop bij feature highlight
                if "pop" in available_sfx:
                    placements.append((available_sfx["pop"], ts + 0.3, 0.16))
                # Bell bij 2e feature highlight als die er is
                if "bell" in available_sfx and i > 0 and scenes[i - 1].get("type") == "feature":
                    placements.append((available_sfx["bell"], ts + 0.3, 0.14))

            if st == "solution":
                # Success chime bij solution reveal
                if "success" in available_sfx:
                    placements.append((available_sfx["success"], ts + 0.4, 0.20))
                elif "ding" in available_sfx:
                    placements.append((available_sfx["ding"], ts + 0.5, 0.22))

            if st == "cta":
                # Swoosh bij CTA entrance
                if "swoosh" in available_sfx:
                    placements.append((available_sfx["swoosh"], ts + 0.2, 0.28))
                elif "pop" in available_sfx:
                    placements.append((available_sfx["pop"], ts + 0.2, 0.25))
                # Ding accent na CTA text (versterkt urgentie)
                if "ding" in available_sfx and sdur > 3.0:
                    placements.append((available_sfx["ding"], ts + sdur * 0.6, 0.15))

        if not placements:
            return None

        # Genereer SFX track: leg alles op een stille basis en mix
        sfx_output = work_dir / "sfx_track.mp3"

        # Maak stille basis track
        silence_path = work_dir / "silence.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(video_dur),
            "-c:a", "libmp3lame", "-q:a", "4",
            str(silence_path),
        ], capture_output=True, timeout=30)

        if not silence_path.exists():
            return None

        # Overlay elk SFX op de juiste timestamp
        current = silence_path
        for pi, (sfx_file, timestamp, volume) in enumerate(placements):
            next_path = work_dir / f"sfx_mix_{pi:02d}.mp3"
            cmd = [
                "ffmpeg", "-y",
                "-i", str(current),
                "-i", str(sfx_file),
                "-filter_complex", (
                    f"[1:a]volume={volume},adelay={int(timestamp * 1000)}|{int(timestamp * 1000)}[sfx];"
                    f"[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=0[out]"
                ),
                "-map", "[out]",
                "-c:a", "libmp3lame", "-q:a", "4",
                str(next_path),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and next_path.exists():
                current = next_path
            else:
                logger.debug(f"[ProVideo] SFX overlay {pi} mislukt, skip")

        if current != silence_path:
            import shutil
            shutil.copy(str(current), str(sfx_output))
            logger.info(f"[ProVideo] SFX track: {len(placements)} effecten")
            return sfx_output

        return None

    def _assemble_final_video(
        self,
        raw_video: Path,
        full_audio: Path | None,
        work_dir: Path,
        script: dict,
        output_path: Path,
    ) -> None:
        """Combineer visuele video + voiceover + achtergrondmuziek + SFX.

        Audio lagen (v8):
        1. Voiceover: compressie, warmth EQ, stereo widening, split voor sidechain
        2. Achtergrondmuziek: sidechain ducking (zachter tijdens spraak), fade-in/out
        3. Sound effects: whoosh (transities), ding (solution), swoosh (CTA)

        Sidechain ducking: voice wordt gesplit — één copy stuurt het sidechain-
        signaal naar de muziek compressor, de andere gaat naar de finale mix.
        """
        video_dur = self._get_media_duration(raw_video) or 30.0

        # Gebruik gecachte muziek (zelfde track als beat-sync) of selecteer nieuwe
        music_track = getattr(self, "_selected_music", None) or self._select_music_for_mood(script)
        has_audio = full_audio and full_audio.exists()
        has_music = music_track and music_track.exists()

        # Genereer SFX track
        sfx_track = self._build_sfx_track(work_dir, script, video_dur)
        has_sfx = sfx_track and sfx_track.exists()

        if has_audio and has_music:
            fade_out_start = max(0, video_dur - 4.0)

            # Bouw inputs en filter chain
            inputs = [
                "-i", str(raw_video),
                "-i", str(full_audio),
                "-stream_loop", "-1",
                "-i", str(music_track),
            ]
            input_idx = 3  # Next available input index

            # ── Voice processing (v8) ──────────────────────────────────
            # Compressor + mid-presence EQ + stereo widening
            # Voice wordt gesplit: [voice_mix] voor finale mix,
            # [voice_sc] als sidechain trigger voor muziek ducking
            voice_chain = (
                f"[1:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                f"acompressor=threshold=0.06:ratio=4:attack=2:release=35:makeup=1.4,"
                f"equalizer=f=3000:t=q:w=1.2:g=2.0,"
                f"equalizer=f=8000:t=q:w=2.0:g=1.0,"
                f"aeval='val(0)|val(0)':channel_layout=stereo,"
                f"extrastereo=m=0.3,"
                # Split voice: één voor mix, één als sidechain signaal
                f"asplit=2[voice_mix][voice_sc];"
            )

            # ── Music processing (v8) — sidechain ducking ─────────────
            # sidechaincompress: eerste input = signaal dat geduckt wordt (muziek)
            # tweede input = sidechain trigger (voice_sc)
            # Muziek gaat ~12dB omlaag tijdens spraak, komt langzaam terug
            music_chain = (
                f"[2:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                f"volume=0.22,"
                f"afade=t=in:d=2.0,"
                f"afade=t=out:st={fade_out_start:.1f}:d=4.0[music_raw];"
                # Sidechain ducking: muziek duckt onder voice
                f"[music_raw][voice_sc]sidechaincompress="
                f"threshold=0.02:ratio=6:attack=8:release=180:"
                f"level_in=3:level_sc=1[music];"
            )

            if has_sfx:
                # 3-weg mix: voice + music (geduckt) + SFX
                inputs += ["-i", str(sfx_track)]
                sfx_chain = (
                    f"[{input_idx}:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                    f"volume=0.7[sfx];"
                )
                mix_chain = (
                    f"[voice_mix][music][sfx]amix=inputs=3:duration=first:"
                    f"dropout_transition=3:weights='1 0.8 0.5'[aout]"
                )
                filter_complex = voice_chain + music_chain + sfx_chain + mix_chain
            else:
                # 2-weg mix: voice + music (geduckt)
                mix_chain = (
                    f"[voice_mix][music]amix=inputs=2:duration=first:"
                    f"dropout_transition=3:weights='1 0.8'[aout]"
                )
                filter_complex = voice_chain + music_chain + mix_chain

            cmd = [
                "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            ] + inputs + [
                "-filter_complex", filter_complex,
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-shortest", "-movflags", "+faststart",
                str(output_path),
            ]
        elif has_audio:
            cmd = [
                "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                "-i", str(raw_video),
                "-i", str(full_audio),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-shortest", "-movflags", "+faststart",
                str(output_path),
            ]
        elif has_music:
            fade_out_start = max(0, video_dur - 2.5)
            cmd = [
                "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                "-i", str(raw_video),
                "-stream_loop", "-1",
                "-i", str(music_track),
                "-filter_complex", (
                    f"[1:a]atrim=0:{video_dur},asetpts=PTS-STARTPTS,"
                    f"volume=0.25,"
                    f"afade=t=in:d=1.5,"
                    f"afade=t=out:st={fade_out_start:.1f}:d=2.5[aout]"
                ),
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-t", str(video_dur), "-movflags", "+faststart",
                str(output_path),
            ]
        else:
            import shutil
            shutil.copy(str(raw_video), str(output_path))
            return

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode != 0:
            logger.warning(f"[ProVideo] Final assembly mislukt: {result.stderr[-300:]}")
            if has_audio:
                subprocess.run([
                    "ffmpeg", "-y", "-i", str(raw_video), "-i", str(full_audio),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "128k", "-shortest",
                    str(output_path),
                ], capture_output=True, timeout=120)
            else:
                import shutil
                shutil.copy(str(raw_video), str(output_path))
        else:
            sfx_msg = f" + {len([1 for s in script.get('scenes', []) if s.get('type') in ('solution', 'cta')]) + max(0, len(script.get('scenes', [])) - 1)} SFX" if has_sfx else ""
            logger.info(f"[ProVideo] Audio assembly klaar: voiceover + muziek{sfx_msg}")

    # ── Visual per scene ──────────────────────────────────────────

    def _get_scene_visual(
        self, scene: dict, idx: int, memory: dict,
        work_dir: Path, duration: float, app_url: str,
    ) -> Path:
        """Product-aware visual selectie — kiest visueel type per scene.

        Visual priority v8:
        0. App demo (phone mockup) — voor "demo" en "feature" scene types
        1. D-ID talking head — voor hook scene (als DID_API_KEY beschikbaar)
        2. Pexels stock video — gratis, goede kwaliteit
        3. Pixabay stock video — gratis, extra variatie
        4. AI-gegenereerd beeld — kost ~$0.04 per beeld

        Scene types en hun visuele strategie:
        - hook: D-ID talking head → stock (persoon die kijkt naar camera)
        - problem: Stock footage (frustratie, werkdruk, probleem-situatie)
        - demo: Phone mockup met app screenshot (PRODUCT SPECIFIEK)
        - solution: Phone mockup of stock (oplossing in actie)
        - feature: Phone mockup met specifiek feature-screenshot
        - cta: Stock footage + CTA overlay
        """
        visual = None
        scene_type = scene.get("type", "body")

        # 0. APP DEMO — phone mockup met echte app screenshots
        # Dit is wat het product-specifiek maakt: de kijker ziet de echte app
        if scene_type in ("demo", "feature"):
            app_screenshots = getattr(self, "_app_screenshots", None)
            if not app_screenshots and app_url:
                # Capture app screenshots (gecached per sessie)
                demo_pages = scene.get("demo_pages", None)
                self._app_screenshots = self._capture_app_screenshots(
                    app_url, work_dir, pages=demo_pages
                )
                app_screenshots = self._app_screenshots

            if app_screenshots:
                # Kies screenshot op basis van scene index
                # demo_page_index in scene dict kan specifiek screenshot aanwijzen
                page_idx = scene.get("demo_page_index", idx % len(app_screenshots))
                page_idx = min(page_idx, len(app_screenshots) - 1)
                screenshot = app_screenshots[page_idx]

                # Haal accent color uit memory
                accent = memory.get("visual_style", {}).get("accent_color", "#6C63FF")
                accent_hex = accent.lstrip("#")

                # Maak achtergrond stock clip voor blur
                bg_clip = self._get_stock_video(scene, memory, work_dir, idx, duration)

                visual = self._create_phone_mockup_clip(
                    screenshot, work_dir, idx, duration,
                    bg_clip=bg_clip, accent_color=accent_hex,
                )

        # Voor solution scenes: ook phone mockup proberen als er screenshots zijn.
        # Als een script geen expliciete demo-scene had, maar wel een echte product-URL,
        # initialiseer de screenshot-cache hier alsnog zodat de bestaande rich app-demo
        # route niet stil terugvalt op generieke stock.
        if not visual and scene_type == "solution":
            app_screenshots = getattr(self, "_app_screenshots", None)
            if not app_screenshots and app_url:
                demo_pages = scene.get("demo_pages", None)
                self._app_screenshots = self._capture_app_screenshots(
                    app_url, work_dir, pages=demo_pages
                )
                app_screenshots = self._app_screenshots
            if app_screenshots:
                # Gebruik een later screenshot (dashboard/resultaat pagina)
                page_idx = min(len(app_screenshots) - 1, 1)
                screenshot = app_screenshots[page_idx]
                accent = memory.get("visual_style", {}).get("accent_color", "#6C63FF")
                bg_clip = self._get_stock_video(scene, memory, work_dir, idx, duration)
                visual = self._create_phone_mockup_clip(
                    screenshot, work_dir, idx, duration,
                    bg_clip=bg_clip, accent_color=accent.lstrip("#"),
                )

        # 1. D-ID TALKING HEAD — alleen voor hook scene
        if not visual and scene_type == "hook" and os.getenv("DID_API_KEY") and not os.getenv("DID_SKIP"):
            visual = self._get_did_hook_visual(scene, memory, work_dir, duration)

        # 2. PRIMAIR: Pexels stock video (gratis, goede kwaliteit)
        if not visual:
            visual = self._get_stock_video(scene, memory, work_dir, idx, duration)

        # Kwaliteitscheck na elke bron
        if visual and visual.exists() and visual.stat().st_size < 5000:
            logger.warning(f"[ProVideo] Pexels visual scene {idx} te klein ({visual.stat().st_size}b), skip")
            visual = None

        # 3. SECUNDAIR: Pixabay stock video (gratis, extra variatie)
        if not visual:
            visual = self._get_pixabay_video(scene, memory, work_dir, idx, duration)
            if visual and visual.exists() and visual.stat().st_size < 5000:
                logger.warning(f"[ProVideo] Pixabay visual scene {idx} te klein, skip")
                visual = None

        # 4. FALLBACK: AI-gegenereerd beeld (kost ~$0.04 per beeld)
        if not visual:
            visual = self._generate_ai_clip(scene, memory, work_dir, idx, duration)
            if visual and visual.exists() and visual.stat().st_size < 5000:
                logger.warning(f"[ProVideo] AI visual scene {idx} te klein, skip")
                visual = None

        # 5. ULTIMATE FALLBACK: eenvoudige kleur-achtergrond video
        if not visual:
            logger.warning(f"[ProVideo] Alle visuele bronnen mislukt voor scene {idx}, color fallback")
            _colors = {"hook": "0x1a1a2e", "problem": "0x16213e", "solution": "0x0f3460", "cta": "0x533483"}
            bg_color = _colors.get(scene_type, "0x1a1a2e")
            fallback_path = work_dir / f"fallback_{idx:02d}.mp4"
            # Methode 1: lavfi color source
            fb_cmd = [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c={bg_color}:s=1080x1920:d={duration}:r=30",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-pix_fmt", "yuv420p",
                str(fallback_path),
            ]
            fb_res = subprocess.run(fb_cmd, capture_output=True, text=True, timeout=30)
            if not (fallback_path.exists() and fallback_path.stat().st_size > 1000):
                # Methode 2: rawvideo als lavfi niet beschikbaar is
                logger.warning(f"[ProVideo] Color lavfi mislukt (rc={fb_res.returncode}): {(fb_res.stderr or '')[-150:]}")
                # Genereer 1 frame PNG en loop
                frame_path = work_dir / f"frame_{idx:02d}.raw"
                # 1080x1920 * 3 bytes (RGB) = 6,220,800 bytes per frame — te groot
                # Gebruik een klein formaat en laat FFmpeg opschalen
                import struct
                # Maak een 2x2 pixel raw RGB bestand
                pixel = bytes.fromhex(bg_color.replace("0x", ""))
                raw_data = pixel * 4  # 2x2 pixels
                frame_path.write_bytes(raw_data)
                fb_cmd2 = [
                    "ffmpeg", "-y",
                    "-f", "rawvideo", "-pixel_format", "rgb24",
                    "-video_size", "2x2", "-framerate", "1",
                    "-i", str(frame_path),
                    "-vf", "scale=1080:1920,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-t", str(duration), "-r", "30",
                    str(fallback_path),
                ]
                subprocess.run(fb_cmd2, capture_output=True, timeout=30)
            if fallback_path.exists() and fallback_path.stat().st_size > 1000:
                visual = fallback_path
                logger.info(f"[ProVideo] Color fallback scene {idx}: {fallback_path.stat().st_size}b")

        return visual

    def _get_did_hook_visual(
        self, scene: dict, memory: dict, work_dir: Path, duration: float,
    ) -> Path | None:
        """Genereer D-ID talking head voor hook scene.

        Stappen:
        1. Maak kort TTS audio fragment (alleen hook voiceover)
        2. Stuur naar D-ID API met presenter foto
        3. Post-process naar 1080x1920 portrait met blurred achtergrond
        """
        try:
            import httpx
            api_key = os.getenv("DID_API_KEY", "")
            if not api_key:
                return None

            voiceover = scene.get("voiceover", "").strip()
            if not voiceover:
                return None

            presenter_url = os.getenv("DID_PRESENTER_URL",
                "https://clips-presenters.d-id.com/v2/ella/p9l_fpg2_k/q15Yu1RvRA/image.png")

            # TTS provider config
            elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
            clone_voice_id = os.getenv("ELEVENLABS_CLONE_VOICE_ID", "")
            voice_id = clone_voice_id or os.getenv("ELEVENLABS_VOICE_ID", "9BWtsMINqrJLrRacOk9x")

            if elevenlabs_key:
                tts_provider = {
                    "type": "elevenlabs",
                    "voice_id": voice_id,
                    "voice_config": {
                        "model_id": "eleven_multilingual_v2",
                        "stability": 0.58,
                        "similarity_boost": 0.92,
                        "style": 0.45,
                        "use_speaker_boost": True,
                    },
                }
            else:
                tts_provider = {"type": "microsoft", "voice_id": "nl-NL-ColetteNeural"}

            logger.info(f"[ProVideo] D-ID hook: genereer talking head ({len(voiceover)} tekens)...")

            # Stap 1: Maak D-ID talk aan
            with httpx.Client(timeout=60) as client:
                response = client.post(
                    "https://api.d-id.com/talks",
                    headers={
                        "Authorization": f"Basic {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "script": {
                            "type": "text",
                            "input": voiceover,
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
                logger.warning(f"[ProVideo] D-ID hook API fout: {response.status_code}")
                return None

            talk_id = response.json()["id"]

            # Stap 2: Poll tot klaar (max 120s)
            video_url = None
            start = time.time()
            with httpx.Client(timeout=30) as client:
                while time.time() - start < 120:
                    resp = client.get(
                        f"https://api.d-id.com/talks/{talk_id}",
                        headers={"Authorization": f"Basic {api_key}"},
                    )
                    data = resp.json()
                    status = data.get("status")
                    if status == "done":
                        video_url = data.get("result_url") or data.get("video_url")
                        break
                    if status == "error":
                        logger.warning(f"[ProVideo] D-ID hook mislukt: {data.get('error')}")
                        return None
                    time.sleep(3)

            if not video_url:
                logger.warning("[ProVideo] D-ID hook timeout")
                return None

            # Stap 3: Download raw video
            raw_path = work_dir / "did_hook_raw.mp4"
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                dl = client.get(video_url)
                dl.raise_for_status()
                raw_path.write_bytes(dl.content)

            # Stap 4: Post-process naar 1080x1920 portrait
            # D-ID output is 512x512 — schaal naar portrait met blurred achtergrond
            clip_path = work_dir / "did_hook.mp4"
            face_size = 900
            face_x = (1080 - face_size) // 2

            fc = (
                f"[0:v]scale={face_size}:{face_size}[face];"
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,"
                f"gblur=sigma=25,"
                f"eq=brightness=-0.20:saturation=0.5[bg];"
                f"[bg][face]overlay={face_x}:100[out]"
            )

            cmd = [
                "ffmpeg", "-y",
                "-i", str(raw_path),
                "-filter_complex", fc,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "copy",
                "-t", str(duration), "-r", "30",
                str(clip_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode == 0 and clip_path.exists() and clip_path.stat().st_size > 10000:
                self.total_cost_usd += (duration / 60) * 0.30  # D-ID cost
                logger.info(f"[ProVideo] D-ID hook klaar: {clip_path}")
                return clip_path

            logger.warning("[ProVideo] D-ID hook post-processing mislukt")
            return None

        except Exception as e:
            logger.warning(f"[ProVideo] D-ID hook fout: {e}")
            return None

    # ── Stock Video (Pexels) ──────────────────────────────────────

    def _build_stock_queries(self, scene: dict, memory: dict) -> list[str]:
        """Bouw queries: AI-generated → script query → voiceover-based → niche → generiek.

        V9: GPT-4o-mini genereert 3 hyper-specifieke Pexels queries op basis van:
        - Voiceover inhoud (wat wordt er gezegd?)
        - Scene type (emotie: hook=aandacht, problem=frustratie, etc.)
        - Vorige scene context (visuele continuïteit)
        - Product/niche context
        """
        scene_type = scene.get("type", "body")
        niche = (memory.get("niche", "") if memory else "").lower()
        visual_desc = scene.get("visual_description", "")
        voiceover = scene.get("voiceover", "")

        queries = []

        # 0. AI-GENERATED queries (beste matching) ────────────────────
        ai_queries = self._generate_ai_visual_queries(scene, memory)
        if ai_queries:
            queries.extend(ai_queries)

        # 1. PRIORITEIT: visual_search_query uit het script (kort, gericht)
        search_query = scene.get("visual_search_query", "").strip()
        if search_query and search_query.lower() not in ("n/a", "nvt") and len(search_query) > 3:
            queries.append(search_query)
            queries.append(search_query + " cinematic")

        # 2. Kernwoorden uit visual_description
        is_talking_head_desc = visual_desc.lower().strip().startswith("creator")
        if visual_desc and not is_talking_head_desc:
            words = [
                w.strip(".,;:!?()\"'-").lower()
                for w in visual_desc.split()
                if len(w.strip(".,;:!?()\"'-")) > 3
            ]
            keywords = [w for w in words if w not in _STOP_WORDS and w.isascii()]
            if len(keywords) >= 2:
                queries.append(" ".join(keywords[:3]))
                if len(keywords) > 3:
                    queries.append(" ".join(keywords[1:4]))
                if len(keywords) > 4:
                    queries.append(" ".join(keywords[2:5]))

        # 3. Voiceover-gebaseerde query (statische keyword mapping)
        if voiceover:
            vo_query = self._voiceover_to_visual_query(voiceover, scene_type)
            if vo_query:
                queries.append(vo_query)

        # 4. Niche-specifieke curated queries (fallback)
        if niche in NICHE_SEARCH_TERMS:
            niche_terms = NICHE_SEARCH_TERMS[niche]
            queries.extend(niche_terms.get(scene_type, niche_terms.get("hook", [])))

        # 5. Generieke fallback queries (last resort)
        queries.extend(GENERIC_SEARCH_TERMS.get(scene_type, GENERIC_SEARCH_TERMS["body"]))

        return queries

    def _generate_ai_visual_queries(
        self, scene: dict, memory: dict | None = None,
    ) -> list[str]:
        """GPT-4o-mini genereert 3 hyper-specifieke Pexels video zoekopdrachten.

        Input: voiceover text + scene type + niche + vorige scene context
        Output: 3 korte Engelse queries die exact matchen met de scene inhoud

        Dit is het verschil tussen generieke stock footage en footage die
        naadloos aansluit bij het verhaal. Elke query beschrijft:
        - WIE (leeftijd, geslacht, uiterlijk)
        - WAT (actie, houding, expressie)
        - WAAR (setting, achtergrond, licht)
        """
        voiceover = scene.get("voiceover", "").strip()
        if not voiceover or len(voiceover) < 10:
            return []

        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not openai_key or len(openai_key) < 10:
            return []

        scene_type = scene.get("type", "body")
        niche = (memory.get("niche", "") if memory else "").lower()
        app_name = (memory.get("app_name", "") if memory else "")
        visual_desc = scene.get("visual_description", "")

        # Context van vorige scenes voor visuele continuïteit
        prev_context = getattr(self, "_last_visual_context", "")

        # Emotie map per scene type
        emotion_map = {
            "hook": "attention-grabbing, scroll-stopping, immediate recognition",
            "problem": "frustration, stress, overwhelm, relatable pain",
            "demo": "curiosity, discovery, seeing the product in action",
            "feature": "understanding, clarity, seeing how it works",
            "solution": "relief, hope, transformation, things getting better",
            "cta": "excitement, urgency, motivation to act NOW",
        }
        emotion = emotion_map.get(scene_type, "neutral, engaged")

        prompt = f"""Generate exactly 3 Pexels video search queries for a TikTok ad scene.

SCENE CONTEXT:
- Voiceover (Dutch): "{voiceover}"
- Scene type: {scene_type} (emotion: {emotion})
- Product: {app_name or 'mobile app'} ({niche or 'general'} niche)
- Visual description hint: {visual_desc or 'none'}
- Previous scene showed: {prev_context or 'nothing yet (this is the first scene)'}

RULES:
1. Each query must be 3-6 English words — short and specific
2. Describe EXACTLY what should be VISIBLE on screen
3. Focus on: person + action + setting (not abstract concepts)
4. The footage must visually MATCH what the voiceover is saying
5. Ensure visual continuity: if the previous scene showed a woman at a desk, this scene should show a similar person
6. For "{scene_type}" scenes, the visual should convey: {emotion}
7. Prefer diverse casting (young adults 22-35), modern settings
8. Never use brand names or app-specific terms in queries
9. Each query must be DIFFERENT (different angle, setting, or framing)

OUTPUT: Return ONLY 3 queries, one per line. No numbering, no explanation."""

        try:
            import httpx
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0.7,
                },
                timeout=10,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()

            # Parse de 3 queries
            lines = [
                line.strip().strip("- ").strip("123.)")
                for line in text.split("\n")
                if line.strip() and len(line.strip()) > 5
            ]
            queries = lines[:3]

            if queries:
                # Bewaar context voor volgende scene (visuele continuïteit)
                self._last_visual_context = queries[0]
                logger.info(f"[ProVideo] AI visual queries scene '{scene_type}': {queries}")
                self.total_cost_usd += 0.0005  # ~500 tokens mini

            return queries

        except Exception as e:
            logger.debug(f"[ProVideo] AI visual query generatie mislukt: {e}")
            return []

    @staticmethod
    def _voiceover_to_visual_query(voiceover: str, scene_type: str) -> str:
        """Extraheer een visuele zoekterm uit de voiceover-tekst."""
        vo = voiceover.lower()

        # Scene-type specifieke visuele mapping
        visual_cues = {
            "hook": {
                "administratie": "healthcare worker overwhelmed paperwork frustrated",
                "admin": "person overwhelmed admin tasks desk",
                "dossier": "healthcare worker typing documents stressed",
                "rapportage": "nurse paperwork typing computer frustrated",
                "zorg": "healthcare worker tired desk morning",
                "verpleeg": "nurse overwhelmed documents hospital",
                "telefoon": "person scrolling phone",
                "phone": "person scrolling phone",
                "dag": "young person morning routine",
                "routine": "person daily routine morning",
                "moe": "tired person exhausted",
                "stress": "stressed young person",
                "werk": "person working overwhelmed",
                "klaar": "frustrated person",
                "geld": "person worried money",
                "gezond": "person health fitness",
                "fit": "person exercise workout",
                "slapen": "person tired bed",
                "tijd": "person checking watch busy",
                "school": "student studying desk",
                "studie": "student studying laptop",
            },
            "problem": {
                "administratie": "healthcare worker frustrated papers pile desk",
                "dossier": "nurse staring computer screen frustrated",
                "rapportage": "person overwhelmed documents paperwork",
                "verpleeg": "nurse tired documents computer overwhelmed",
                "to-do": "person staring at messy desk",
                "todo": "person staring at messy desk",
                "lijst": "person writing list frustrated",
                "vergeten": "person confused forgetful",
                "chaos": "messy disorganized workspace",
                "druk": "busy person rushing",
                "stress": "person head in hands stressed",
                "niet af": "unfinished tasks papers desk",
                "saai": "bored person scrolling phone couch",
                "alleen": "person sitting alone",
                "moeite": "person struggling effort",
                "probleem": "person thinking worried",
            },
            "solution": {
                "app": "person using phone app smiling",
                "makkelijk": "person relaxed using phone",
                "simpel": "person easily using smartphone",
                "bijhoudt": "person organized productive phone",
                "overzicht": "person looking at phone satisfied",
                "planning": "person planning phone calendar",
                "resultaat": "person happy achievement",
                "werkt": "person productive happy laptop",
                "helpt": "helpful technology smartphone",
            },
            "cta": {
                "download": "person downloading app phone",
                "probeer": "person excited trying phone",
                "gratis": "person happy smartphone download",
                "link": "person tapping phone screen",
                "bio": "person using social media phone",
                "begin": "person starting new motivated",
            },
        }

        cues = visual_cues.get(scene_type, {})
        for keyword, query in cues.items():
            if keyword in vo:
                return query

        # Fallback: scene-type algemeen
        type_fallbacks = {
            "hook": "young person looking at camera relatable",
            "problem": "person frustrated daily life",
            "solution": "person happy using smartphone",
            "cta": "person tapping phone excited",
        }
        return type_fallbacks.get(scene_type, "")

    def _get_stock_video(
        self, scene: dict, memory: dict, work_dir: Path,
        idx: int, duration: float,
    ) -> Path | None:
        """Zoek en download stock video — curated library eerst, dan Pexels search."""
        api_key = os.getenv("PEXELS_API_KEY", "")
        if not api_key or api_key.startswith("...") or len(api_key) < 10:
            return None

        import httpx
        scene_type = scene.get("type", "body")
        niche = (memory.get("niche", "") if memory else "").lower()
        used = getattr(self, "_used_video_ids", set())

        # 1. CURATED LIBRARY — handmatig geselecteerde video's (altijd passend)
        curated = CURATED_VIDEOS.get(niche, {}).get(scene_type, [])
        if curated:
            available = [vid_id for vid_id in curated if vid_id not in used]
            if available:
                chosen_id = random.choice(available)
                try:
                    resp = httpx.get(
                        f"https://api.pexels.com/videos/videos/{chosen_id}",
                        headers={"Authorization": api_key},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    video = resp.json()
                    video_file = self._select_best_file(video)
                    if video_file:
                        logger.info(f"[ProVideo] Curated video scene {idx}: ID={chosen_id}")
                        raw_path = work_dir / f"stock_raw_{idx:02d}.mp4"
                        dl = httpx.get(video_file["link"], timeout=60, follow_redirects=True)
                        dl.raise_for_status()
                        raw_path.write_bytes(dl.content)
                        used.add(chosen_id)

                        clip_path = work_dir / f"stock_{idx:02d}.mp4"
                        kb_effects = [
                            (
                                "scale=1188:2112:force_original_aspect_ratio=increase,"
                                f"crop=1080:1920:'(iw-1080)/2':'max(0,(ih-1920)/2*(1-t/{duration}))',"
                                "setsar=1,format=yuv420p"
                            ),
                            (
                                "scale=1188:2112:force_original_aspect_ratio=increase,"
                                f"crop=1080:1920:'min(iw-1080,(iw-1080)*t/{duration})':'(ih-1920)/2',"
                                "setsar=1,format=yuv420p"
                            ),
                            (
                                "scale=1188:2112:force_original_aspect_ratio=increase,"
                                f"crop=1080:1920:'(iw-1080)/2':'min(ih-1920,(ih-1920)*t/{duration})',"
                                "setsar=1,format=yuv420p"
                            ),
                            (
                                "scale=1188:2112:force_original_aspect_ratio=increase,"
                                "crop=1080:1920:'(iw-1080)/2+(iw-1080)/4*sin(t*0.5)':'(ih-1920)/2',"
                                "setsar=1,format=yuv420p"
                            ),
                        ]
                        kb_vf = kb_effects[idx % len(kb_effects)]
                        if scene_type == "hook":
                            kb_vf = "setpts=1.12*PTS," + kb_vf

                        cmd = [
                            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                            "-stream_loop", "-1",
                            "-i", str(raw_path), "-vf", kb_vf,
                            "-t", str(duration),
                            "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                            "-pix_fmt", "yuv420p", "-preset", STOCK_INTERMEDIATE_PRESET, "-crf", STOCK_INTERMEDIATE_CRF,
                            "-an", "-r", str(STOCK_INTERMEDIATE_FPS), str(clip_path),
                        ]
                        cur_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                        if clip_path.exists() and clip_path.stat().st_size > 5000:
                            return clip_path
                        # Simpele fallback voor curated
                        logger.warning(f"[ProVideo] Curated KB mislukt scene {idx}: {(cur_result.stderr or '')[-150:]}")
                        simple_vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p"
                        cmd_simple = [
                            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                            "-stream_loop", "-1",
                            "-i", str(raw_path), "-vf", simple_vf,
                            "-t", str(duration),
                            "-c:v", "libx264", "-preset", STOCK_INTERMEDIATE_PRESET,
                            "-crf", STOCK_INTERMEDIATE_CRF,
                            "-an", "-r", str(STOCK_INTERMEDIATE_FPS), str(clip_path),
                        ]
                        subprocess.run(cmd_simple, capture_output=True, text=True, timeout=120)
                        if clip_path.exists() and clip_path.stat().st_size > 5000:
                            logger.info(f"[ProVideo] Curated simple fallback OK scene {idx}")
                            return clip_path
                except Exception as e:
                    logger.debug(f"[ProVideo] Curated video {chosen_id} mislukt: {e}")

        # 2. FALLBACK: Pexels search (als curated niet beschikbaar)
        queries = self._build_stock_queries(scene, memory)
        # AI-gegenereerde queries staan al vooraan — shuffle alleen de rest
        # zodat AI-queries altijd als eerste geprobeerd worden
        ai_count = len([q for q in queries[:3] if q])  # Max 3 AI queries
        ai_part = queries[:ai_count]
        rest_part = queries[ai_count:]
        random.shuffle(rest_part)
        queries = ai_part + rest_part

        for query in queries[:7]:  # Meer queries proberen voor betere match
            try:
                # Check cache eerst
                cached = _pexels_cache_get(query)
                if cached:
                    data = cached
                    logger.debug(f"[ProVideo] Pexels cache hit: '{query}'")
                else:
                    resp = httpx.get(
                        "https://api.pexels.com/videos/search",
                        params={
                            "query": query,
                            "per_page": 10,
                            "orientation": "portrait",
                            "size": "medium",
                        },
                        headers={"Authorization": api_key},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    _pexels_cache_set(query, data)

                if not data.get("videos"):
                    continue

                video = self._select_best_video(data["videos"], idx)
                if not video:
                    continue

                video_file = self._select_best_file(video)
                if not video_file:
                    continue

                logger.info(f"[ProVideo] Stock video scene {idx}: '{query}'")
                raw_path = work_dir / f"stock_raw_{idx:02d}.mp4"
                dl = httpx.get(video_file["link"], timeout=60, follow_redirects=True)
                dl.raise_for_status()
                raw_path.write_bytes(dl.content)

                clip_path = work_dir / f"stock_{idx:02d}.mp4"

                # Ken Burns: scale 5% groter voor bewegingsruimte
                kb_effects = [
                    # Langzaam inzoomen vanuit centrum (10% headroom = vloeiender)
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2':'max(0,(ih-1920)/2*(1-t/{duration}))',"
                        "setsar=1,format=yuv420p"
                    ),
                    # Langzaam naar rechts pannen
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'min(iw-1080,(iw-1080)*t/{duration})':'(ih-1920)/2',"
                        "setsar=1,format=yuv420p"
                    ),
                    # Langzaam naar beneden pannen
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2':'min(ih-1920,(ih-1920)*t/{duration})',"
                        "setsar=1,format=yuv420p"
                    ),
                    # Subtiele horizontale sway
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        "crop=1080:1920:'(iw-1080)/2+(iw-1080)/4*sin(t*0.5)':'(ih-1920)/2',"
                        "setsar=1,format=yuv420p"
                    ),
                    # Zoom-uit effect (cinematisch — start vergroot, eindigt normaal)
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2*(1-t/{duration})':'(ih-1920)/2*(1-t/{duration})',"
                        "setsar=1,format=yuv420p"
                    ),
                    # Diagonale pan (top-links naar rechts-onder — dynamisch)
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)*t/{duration}':'(ih-1920)*t/{duration}',"
                        "setsar=1,format=yuv420p"
                    ),
                ]
                kb_vf = kb_effects[idx % len(kb_effects)]

                # Subtiele slow-motion op hook scenes voor dramatisch effect
                stock_scene_type = scene.get("type", "body")
                if stock_scene_type == "hook":
                    kb_vf = "setpts=1.12*PTS," + kb_vf

                cmd = [
                    "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                    "-stream_loop", "-1",  # Loop video als korter dan scene duur
                    "-i", str(raw_path),
                    "-vf", kb_vf,
                    "-t", str(duration),
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                    "-pix_fmt", "yuv420p",
                    "-preset", STOCK_INTERMEDIATE_PRESET, "-crf", STOCK_INTERMEDIATE_CRF,
                    "-an", "-r", str(STOCK_INTERMEDIATE_FPS),
                    str(clip_path),
                ]
                pre_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

                if clip_path.exists() and clip_path.stat().st_size > 5000:
                    return clip_path

                # Ken Burns mislukt — probeer simpele scale+crop
                logger.warning(
                    f"[ProVideo] KB pre-proc scene {idx} mislukt "
                    f"(rc={pre_result.returncode}, raw={raw_path.stat().st_size}b): "
                    f"{(pre_result.stderr or '')[-200:]}"
                )
                simple_vf = (
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,setsar=1,format=yuv420p"
                )
                cmd_simple = [
                    "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
                    "-stream_loop", "-1",
                    "-i", str(raw_path),
                    "-vf", simple_vf,
                    "-t", str(duration),
                    "-c:v", "libx264", "-preset", STOCK_INTERMEDIATE_PRESET,
                    "-crf", STOCK_INTERMEDIATE_CRF,
                    "-an", "-r", str(STOCK_INTERMEDIATE_FPS),
                    str(clip_path),
                ]
                simple_result = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=120)
                if clip_path.exists() and clip_path.stat().st_size > 5000:
                    logger.info(f"[ProVideo] Simple scale+crop fallback OK scene {idx}")
                    return clip_path
                logger.warning(f"[ProVideo] Simple fallback ook mislukt scene {idx}: {(simple_result.stderr or '')[-200:]}")

            except Exception as e:
                logger.debug(f"[ProVideo] Stock query '{query}' mislukt: {e}")
                continue

        logger.info(f"[ProVideo] Geen stock video gevonden voor scene {idx}")
        return None

    def _select_best_video(self, videos: list[dict], idx: int) -> dict | None:
        """Selecteer de beste video met random factor voor variatie.
        Excludeert video's die al voor andere scenes zijn gebruikt."""
        used = getattr(self, "_used_video_ids", set())

        scored = []
        for v in videos:
            vid = v.get("id", 0)
            if vid in used:
                continue  # Skip al gebruikte video's
            w = v.get("width", 0)
            h = v.get("height", 0)
            dur = v.get("duration", 0)
            score = 0
            if h > w:
                score += 50
            if h >= 1080:
                score += 30
            elif h >= 720:
                score += 15
            if 5 <= dur <= 15:
                score += 20
            elif dur >= 3:
                score += 10
            score += random.randint(0, 15)  # Random factor voor variatie
            scored.append((score, v))

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return None

        # Random uit top 5 ipv deterministic top 3
        top = scored[:min(5, len(scored))]
        chosen = random.choice(top)[1]

        # Track deze video als gebruikt
        chosen_id = chosen.get("id", 0)
        if chosen_id:
            used.add(chosen_id)

        return chosen

    def _select_best_file(self, video: dict) -> dict | None:
        """Selecteer het beste videobestand (prefer portrait HD mp4)."""
        files = video.get("video_files", [])
        mp4s = [f for f in files if f.get("file_type") == "video/mp4"]
        if not mp4s:
            return None

        best = None
        best_score = -1
        for f in mp4s:
            w = f.get("width", 0)
            h = f.get("height", 0)
            score = 0
            if h > w:
                score += 50
            # Vercel hoeft geen 2K bron te herencoderen als 1080p portrait al voldoende is.
            # Richt op snelle, hoogwaardige tussenclips die dicht bij de referentievideo blijven.
            if 1000 <= h <= 1440:
                score += 40
            elif h >= 1920:
                score += 18
            elif h >= 1080:
                score += 30
            elif h >= 720:
                score += 15
            if f.get("quality") == "hd":
                score += 20
            if score > best_score:
                best_score = score
                best = f

        return best

    # ── Pixabay Stock Video ───────────────────────────────────────

    def _get_pixabay_video(
        self, scene: dict, memory: dict, work_dir: Path,
        idx: int, duration: float,
    ) -> Path | None:
        """Zoek en download stock video van Pixabay (extra variatie)."""
        api_key = os.getenv("PIXABAY_API_KEY", "")
        if not api_key or len(api_key) < 5:
            return None

        queries = self._build_stock_queries(scene, memory)
        random.shuffle(queries)  # Randomiseer volgorde voor variatie

        import httpx

        for query in queries[:5]:  # Max 5 queries proberen
            try:
                cached = _stock_cache_get(query, "pixabay")
                if cached:
                    data = cached
                    logger.debug(f"[ProVideo] Pixabay cache hit: '{query}'")
                else:
                    resp = httpx.get(
                        "https://pixabay.com/api/videos/",
                        params={
                            "key": api_key,
                            "q": query,
                            "per_page": 15,
                            "safesearch": "true",
                            "min_width": 720,
                        },
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    _stock_cache_set(query, data, "pixabay")

                hits = data.get("hits", [])
                if not hits:
                    continue

                # Filter portrait videos (hoogte > breedte)
                portrait = [
                    h for h in hits
                    if h.get("videos", {}).get("medium", {}).get("height", 0) >
                       h.get("videos", {}).get("medium", {}).get("width", 0)
                ]
                # Als geen portrait, neem alle videos
                candidates = portrait if portrait else hits

                # Score en selecteer beste video (excl. al gebruikte)
                used = getattr(self, "_used_video_ids", set())
                scored = []
                for v in candidates:
                    vid = v.get("id", 0)
                    if vid in used:
                        continue  # Skip al gebruikte video's

                    vid_info = v.get("videos", {})
                    large = vid_info.get("large", {})
                    medium = vid_info.get("medium", {})
                    best_res = large if large.get("url") else medium
                    h = best_res.get("height", 0)
                    w = best_res.get("width", 0)
                    dur = v.get("duration", 0)
                    s = 0
                    if h > w:
                        s += 50  # Portrait bonus
                    if h >= 1080:
                        s += 30
                    elif h >= 720:
                        s += 15
                    if 8 <= dur <= 25:
                        s += 25  # Prefer langere clips voor vloeiende video
                    elif 5 <= dur <= 8:
                        s += 15
                    elif dur >= 3:
                        s += 5
                    s += random.randint(0, 15)  # Random factor voor variatie
                    scored.append((s, v))

                scored.sort(key=lambda x: x[0], reverse=True)
                if not scored:
                    continue

                # Pak random uit top 5 voor variatie
                top = scored[:min(5, len(scored))]
                chosen = random.choice(top)[1]

                # Track als gebruikt
                chosen_id = chosen.get("id", 0)
                if chosen_id:
                    used.add(chosen_id)

                # Download video
                vid_files = chosen.get("videos", {})
                # Prefer large, fallback medium, fallback small
                for quality in ("large", "medium", "small"):
                    dl_info = vid_files.get(quality, {})
                    if dl_info.get("url"):
                        break
                else:
                    continue

                dl_url = dl_info["url"]
                logger.info(f"[ProVideo] Pixabay video scene {idx}: '{query}'")

                raw_path = work_dir / f"pixabay_raw_{idx:02d}.mp4"
                dl = httpx.get(dl_url, timeout=60, follow_redirects=True)
                dl.raise_for_status()
                raw_path.write_bytes(dl.content)

                # Ken Burns effect (hergebruik dezelfde effecten)
                clip_path = work_dir / f"pixabay_{idx:02d}.mp4"
                kb_effects = [
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2':'max(0,(ih-1920)/2*(1-t/{duration}))',"
                        "setsar=1,format=yuv420p"
                    ),
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'min(iw-1080,(iw-1080)*t/{duration})':'(ih-1920)/2',"
                        "setsar=1,format=yuv420p"
                    ),
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2':'min(ih-1920,(ih-1920)*t/{duration})',"
                        "setsar=1,format=yuv420p"
                    ),
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        "crop=1080:1920:'(iw-1080)/2+(iw-1080)/4*sin(t*0.5)':'(ih-1920)/2',"
                        "setsar=1,format=yuv420p"
                    ),
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)/2*(1-t/{duration})':'(ih-1920)/2*(1-t/{duration})',"
                        "setsar=1,format=yuv420p"
                    ),
                    (
                        "scale=1188:2112:force_original_aspect_ratio=increase,"
                        f"crop=1080:1920:'(iw-1080)*t/{duration}':'(ih-1920)*t/{duration}',"
                        "setsar=1,format=yuv420p"
                    ),
                ]
                kb_vf = kb_effects[(idx + 3) % len(kb_effects)]  # Offset zodat anders dan Pexels

                scene_type = scene.get("type", "body")
                if scene_type == "hook":
                    kb_vf = "setpts=1.12*PTS," + kb_vf

                cmd = [
                    "ffmpeg", "-y",
                    "-stream_loop", "-1",
                    "-i", str(raw_path),
                    "-vf", kb_vf,
                    "-t", str(duration),
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                    "-preset", STOCK_INTERMEDIATE_PRESET, "-crf", STOCK_INTERMEDIATE_CRF,
                    "-pix_fmt", "yuv420p",
                    "-an", "-r", str(STOCK_INTERMEDIATE_FPS),
                    str(clip_path),
                ]
                subprocess.run(cmd, capture_output=True, text=True, timeout=120)

                if clip_path.exists() and clip_path.stat().st_size > 5000:
                    return clip_path

            except Exception as e:
                logger.debug(f"[ProVideo] Pixabay query '{query}' mislukt: {e}")
                continue

        return None

    # ── Website Capture (Playwright) — FIXED timeout ──────────────

    def _capture_website(
        self, url: str, work_dir: Path, idx: int, duration: float,
    ) -> Path | None:
        """Maak een scrollende video van de website."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.info("[ProVideo] Playwright niet beschikbaar")
            return None

        try:
            screenshot_path = work_dir / f"web_{idx:02d}.png"

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(url, wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(screenshot_path), full_page=True)
                browser.close()

            if not screenshot_path.exists():
                return None

            dimensions = _probe_dimensions(screenshot_path)
            if dimensions:
                w, h = dimensions
            else:
                w, h = 1280, 3000

            clip_path = work_dir / f"web_clip_{idx:02d}.mp4"
            scroll_distance = max(0, h - 800)

            if scroll_distance > 200:
                # Scrollende video (boven naar beneden)
                scale_h = int(h * (1080 / w))
                crop_scroll = max(0, scale_h - 1920)
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-t", str(duration),
                    "-i", str(screenshot_path),
                    "-vf", (
                        f"scale=1080:{scale_h},"
                        f"crop=1080:1920:0:"
                        f"'min({crop_scroll},{crop_scroll}*t/{duration})',"
                        f"format=yuv420p"
                    ),
                    "-r", "30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                    str(clip_path),
                ]
            else:
                # Statisch — simpele scale+pad, GEEN zoompan (voorkomt timeout)
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-t", str(duration),
                    "-i", str(screenshot_path),
                    "-vf", (
                        "scale=1080:-2,"
                        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x111111,"
                        "format=yuv420p"
                    ),
                    "-r", "30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                    str(clip_path),
                ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if clip_path.exists() and clip_path.stat().st_size > 5000:
                logger.info(f"[ProVideo] Website capture scene {idx}: {url}")
                return clip_path

            logger.warning(f"[ProVideo] Website clip FFmpeg fout: {result.stderr[-300:]}")
            return None

        except Exception as e:
            logger.warning(f"[ProVideo] Website capture mislukt: {e}")
            return None

    # ── Product Demo — App UI in Phone Mockup ──────────────────────

    def _capture_app_screenshots(
        self, app_url: str, work_dir: Path, pages: list[str] | None = None,
    ) -> list[Path]:
        """Capture meerdere pagina's van de app als screenshots.

        Gebruikt Playwright om door de app te navigeren en screenshots te maken.
        Screenshots worden gecached in APP_ASSETS_DIR zodat ze hergebruikt worden.

        Args:
            app_url: Base URL van de app (bijv. https://dossiertijd.nl)
            work_dir: Werk directory
            pages: Lijst van subpaden (bijv. ["/app", "/app/dossiers", "/app/settings"])
                   Als None: probeert standaard pagina's
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.info("[ProVideo] Playwright niet beschikbaar voor app screenshots")
            return []

        if not app_url:
            return []

        # Cache check — screenshots per domain
        domain = app_url.replace("https://", "").replace("http://", "").split("/")[0].strip("/")
        normalized_domains = [domain]
        if domain.startswith("www."):
            normalized_domains.append(domain[4:])

        cache_candidates = []
        for candidate_domain in normalized_domains:
            if candidate_domain:
                cache_candidates.append(APP_ASSETS_DIR / candidate_domain.replace(".", "_"))

        for candidate_dir in cache_candidates:
            existing = sorted(candidate_dir.glob("*.png"))
            if existing and (time.time() - existing[0].stat().st_mtime < 86400):
                logger.info(f"[ProVideo] {len(existing)} gecachte app screenshots gevonden")
                return existing

        cache_dir = cache_candidates[-1] if cache_candidates else (APP_ASSETS_DIR / "default")
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Standaard pagina's als geen specifieke opgegeven
        if not pages:
            # Probeer meerdere standaard pagina's die bij SaaS apps horen
            # Elke pagina die lukt wordt een aparte screenshot
            pages = [
                "/",                  # Landing page
                "/features",          # Feature overzicht
                "/pricing",           # Pricing pagina
                "/app",               # App login/dashboard
                "/app/dashboard",     # Dashboard
                "/#features",         # Anchor naar features sectie
            ]

        screenshots = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                # iPhone 14 Pro viewport — native app-feel
                context = browser.new_context(
                    viewport={"width": 393, "height": 852},
                    device_scale_factor=3,  # Retina voor scherpe screenshots
                    is_mobile=True,
                    has_touch=True,
                )
                page = context.new_page()

                for i, path in enumerate(pages):
                    url = f"{app_url.rstrip('/')}{path}"
                    try:
                        page.goto(url, wait_until="networkidle", timeout=12000)
                        page.wait_for_timeout(1500)

                        # Screenshot in native mobiele resolutie
                        ss_path = cache_dir / f"app_page_{i:02d}.png"
                        page.screenshot(path=str(ss_path))

                        if ss_path.exists() and ss_path.stat().st_size > 5000:
                            screenshots.append(ss_path)
                            logger.info(f"[ProVideo] App screenshot: {path}")
                    except Exception as e:
                        logger.debug(f"[ProVideo] App pagina {path} mislukt: {e}")
                        continue

                browser.close()

        except Exception as e:
            logger.warning(f"[ProVideo] App screenshot capture mislukt: {e}")

        return screenshots

    def _create_phone_mockup_clip(
        self, screenshot_path: Path, work_dir: Path, idx: int,
        duration: float, bg_clip: Path | None = None,
        accent_color: str = "6C63FF",
    ) -> Path | None:
        """Plaats een app screenshot in een telefoon-frame op een achtergrond.

        Resultaat: 1080x1920 video met:
        - Blurred achtergrond (stock footage of gradient)
        - Telefoon frame (donker bezel met rounded corners via overlay)
        - App screenshot binnenin de telefoon
        - Subtiele schaduw onder de telefoon
        - Ken Burns zoom op de telefoon (langzame zoom-in)

        Het telefoon-frame wordt getekend met FFmpeg drawbox filters
        (gradient bezel, notch indicator, home bar).
        """
        mockup_path = work_dir / f"phone_mockup_{idx:02d}.mp4"

        # Stap 1: Maak achtergrond
        # Als we een stock clip hebben, blur die. Anders gradient achtergrond.
        bg_path = work_dir / f"phone_bg_{idx:02d}.mp4"

        if bg_clip and bg_clip.exists():
            # Blur de stock footage als achtergrond
            cmd_bg = [
                "ffmpeg", "-y", "-i", str(bg_clip),
                "-vf", (
                    "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,"
                    "boxblur=25:5,"
                    "eq=brightness=-0.15:saturation=0.6,"
                    "format=yuv420p"
                ),
                "-t", str(duration), "-r", "30",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-an", str(bg_path),
            ]
        else:
            # Gradient achtergrond met brand color
            r = int(accent_color[:2], 16)
            g = int(accent_color[2:4], 16)
            b = int(accent_color[4:6], 16)
            # Donkere versie van de accent kleur
            dr, dg, db = max(0, r - 80), max(0, g - 80), max(0, b - 80)
            cmd_bg = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", (
                    f"color=c=0x{dr:02X}{dg:02X}{db:02X}:s=1080x1920:d={duration}:r=60"
                ),
                "-vf", f"format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-an", str(bg_path),
            ]

        result = subprocess.run(cmd_bg, capture_output=True, timeout=30)
        if not bg_path.exists():
            return None

        # Stap 2: Animated telefoon-frame met screenshot
        # Telefoon dimensies (iPhone-achtig, gecentreerd)
        phone_w = 620       # Telefoon breedte
        phone_h = 1280      # Telefoon hoogte
        final_phone_x = (1080 - phone_w) // 2  # Gecentreerd horizontaal
        final_phone_y = 280  # Eindpositie: iets boven midden
        bezel = 12          # Rand dikte
        screen_w = phone_w - bezel * 2
        screen_h = phone_h - bezel * 2 - 48  # Minus notch + home bar

        # ── Animatie parameters ─────────────────────────────────────
        # Stevige, goed zichtbare animatie — niet subtiel, maar smooth
        entrance_dur = 0.85  # Slide-up duur (seconden)
        float_amp = 14       # Float amplitude (duidelijk zichtbaar op 1920px)
        float_freq = 0.55    # Langzame, kalme floating
        start_y = 2200       # Ver onder scherm voor dramatische entrance
        overshoot = 25       # Bounce overshoot in pixels (gaat even voorbij target)

        # Eased slide-up met overshoot bounce:
        # Fase 1 (0 → entrance_dur): cubic ease-out slide up
        # Fase 2 (entrance_dur → +0.3s): kleine bounce terug (overshoot settling)
        # Fase 3 (daarna): smooth floating
        settle_dur = 0.3
        total_intro = entrance_dur + settle_dur

        y_expr = (
            f"if(lt(t,{entrance_dur:.2f}),"
            # Fase 1: Slide up met cubic ease-out
            f"{start_y}+({final_phone_y - overshoot}-{start_y})*(1-pow(1-t/{entrance_dur:.2f},3)),"
            f"if(lt(t,{total_intro:.2f}),"
            # Fase 2: Bounce settle (overshoot terug naar final)
            f"{final_phone_y - overshoot}+{overshoot}*((t-{entrance_dur:.2f})/{settle_dur:.2f}),"
            # Fase 3: Duidelijke floating
            f"{final_phone_y}+{float_amp}*sin(2*PI*{float_freq}*(t-{total_intro:.2f}))))"
        )

        # x float: zichtbaar maar niet gek — licht heen en weer
        x_float_amp = 8
        x_expr = (
            f"if(lt(t,{total_intro:.2f}),"
            f"{final_phone_x},"
            f"{final_phone_x}+{x_float_amp}*sin(2*PI*{float_freq * 0.6:.2f}*(t-{total_intro:.2f})+1.5))"
        )

        # ── Stap 2a: Genereer statisch telefoon-frame (bezel + notch) ───
        # We tekenen het telefoon frame op een transparante laag (RGBA)
        # zodat we het als overlay met animated positie kunnen plaatsen
        phone_frame_path = work_dir / f"phone_frame_{idx:02d}.png"

        # Maak het frame als statisch beeld met drawbox filters
        notch_w = 120
        notch_x = (phone_w - notch_w) // 2
        bar_w = 160
        bar_x = (phone_w - bar_w) // 2
        bar_y = phone_h - bezel - 16

        frame_vf = ",".join([
            # Schaduw laag (iets groter dan de telefoon)
            f"drawbox=x=0:y=8:w={phone_w + 12}:h={phone_h + 4}:color=black@0.35:t=fill",
            # Bezel
            f"drawbox=x=6:y=0:w={phone_w}:h={phone_h}:color=0x1A1A1A@1.0:t=fill",
            # Notch area
            f"drawbox=x={6 + bezel}:y={bezel}:w={screen_w}:h=32:color=0x000000@1.0:t=fill",
            # Notch indicator
            f"drawbox=x={6 + notch_x}:y={bezel + 6}:w={notch_w}:h=20:color=0x111111@1.0:t=fill",
            # Home bar
            f"drawbox=x={6 + bar_x}:y={bar_y}:w={bar_w}:h=6:color=0x444444@0.8:t=fill",
        ])

        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x000000@0.0:s={phone_w + 12}x{phone_h + 12}:d=0.04",
            "-vf", frame_vf + ",format=yuva420p",
            "-frames:v", "1", str(phone_frame_path),
        ], capture_output=True, timeout=15)

        # ── Stap 2b: Maak de screenshot content als video (met scroll) ──
        # De screenshot scrollt langzaam van boven naar beneden
        content_path = work_dir / f"phone_content_{idx:02d}.mp4"
        scroll_dur = max(1, duration - 0.5)  # Scroll start na entrance

        cmd_content = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(screenshot_path),
            "-filter_complex", (
                f"[0:v]scale={screen_w}:-1,"
                f"crop={screen_w}:{screen_h}:0:"
                f"'min(ih-{screen_h},max(0,(ih-{screen_h})*max(0,t-{entrance_dur:.1f})/{scroll_dur:.1f}))',"
                f"format=yuv420p[out]"
            ),
            "-map", "[out]",
            "-t", str(duration), "-r", "30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-an", str(content_path),
        ]
        result = subprocess.run(cmd_content, capture_output=True, text=True, timeout=30)
        if not content_path.exists():
            logger.debug(f"[ProVideo] Phone content clip mislukt, fallback naar statisch")
            # Fallback: statisch screenshot
            cmd_content_simple = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(screenshot_path),
                "-vf", f"scale={screen_w}:{screen_h}:force_original_aspect_ratio=decrease,pad={screen_w}:{screen_h},format=yuv420p",
                "-t", str(duration), "-r", "30",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-an", str(content_path),
            ]
            subprocess.run(cmd_content_simple, capture_output=True, timeout=30)

        if not content_path.exists():
            return None

        # ── Stap 2c: Composiet — achtergrond + content + bezel overlay ──
        # Gebruik twee animated overlays:
        # 1. Screenshot content op de scherm-positie (meebewegend met telefoon)
        # 2. Telefoon bezel frame eroverheen
        #
        # Alle elementen bewegen mee met dezelfde y-expressie zodat
        # het er uitziet als één geheel dat omhoog schuift en float.

        # Content offset binnen het frame: bezel + notch area
        content_offset_x = 6 + bezel   # Relatief t.o.v. frame origin
        content_offset_y = bezel + 36   # Notch area hoogte

        # Opacity: fade in tijdens entrance
        opacity_expr = f"if(lt(t,{entrance_dur:.2f}),t/{entrance_dur:.2f},1)"

        filter_complex = (
            # Content overlay: positie = frame_positie + interne offset
            f"[0:v][1:v]overlay="
            f"x='{x_expr}+{content_offset_x}':"
            f"y='{y_expr}+{content_offset_y}':"
            f"shortest=1[with_content];"
            # Frame overlay bovenop (bezel, notch, home bar verbergen de randen)
            f"[with_content][2:v]overlay="
            f"x='{x_expr}-6':"  # -6 voor schaduw offset
            f"y='{y_expr}-4':"  # -4 voor schaduw offset boven
            f"shortest=1,format=yuv420p[out]"
        )

        cmd_composite = [
            "ffmpeg", "-y",
            "-i", str(bg_path),           # [0] achtergrond
            "-i", str(content_path),       # [1] scrollende screenshot content
            "-loop", "1", "-i", str(phone_frame_path),  # [2] statisch telefoon frame
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-t", str(duration), "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21",
            "-an", str(mockup_path),
        ]
        result = subprocess.run(cmd_composite, capture_output=True, text=True, timeout=90)

        if mockup_path.exists() and mockup_path.stat().st_size > 10000:
            logger.info(f"[ProVideo] Phone mockup klaar (animated): scene {idx}")
            return mockup_path

        # ── Fallback: eenvoudige statische overlay ──────────────────
        logger.debug(f"[ProVideo] Animated mockup mislukt, fallback naar statisch overlay")
        err_msg = result.stderr[-500:] if hasattr(result, 'stderr') and result.stderr else "onbekend"
        logger.debug(f"[ProVideo] FFmpeg stderr: {err_msg}")

        # Simpele twee-pass fallback (statisch, geen animatie)
        bezel_path = work_dir / f"phone_bezel_{idx:02d}.mp4"
        bezel_vf = ",".join([
            f"drawbox=x={final_phone_x - 6}:y={final_phone_y + 8}:w={phone_w + 12}:h={phone_h + 4}:color=black@0.35:t=fill",
            f"drawbox=x={final_phone_x}:y={final_phone_y}:w={phone_w}:h={phone_h}:color=0x1A1A1A@1.0:t=fill",
            f"drawbox=x={final_phone_x + bezel}:y={final_phone_y + bezel}:w={screen_w}:h=32:color=0x000000@1.0:t=fill",
            f"drawbox=x={final_phone_x + (phone_w - notch_w) // 2}:y={final_phone_y + bezel + 6}:w={notch_w}:h=20:color=0x111111@1.0:t=fill",
            f"drawbox=x={final_phone_x + (phone_w - bar_w) // 2}:y={final_phone_y + phone_h - bezel - 16}:w={bar_w}:h=6:color=0x444444@0.8:t=fill",
            "format=yuv420p"
        ])
        subprocess.run([
            "ffmpeg", "-y", "-i", str(bg_path), "-vf", bezel_vf,
            "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-an", str(bezel_path),
        ], capture_output=True, timeout=45)

        if bezel_path.exists():
            screen_x = final_phone_x + bezel
            screen_y = final_phone_y + bezel + 36
            cmd_simple = [
                "ffmpeg", "-y",
                "-i", str(bezel_path), "-i", str(screenshot_path),
                "-filter_complex", (
                    f"[1:v]scale={screen_w}:-1,"
                    f"crop={screen_w}:{screen_h}:0:"
                    f"'min(ih-{screen_h},max(0,(ih-{screen_h})*t/{max(1, duration - 1):.1f}))',"
                    f"format=yuv420p[scaled];"
                    f"[0:v][scaled]overlay=x={screen_x}:y={screen_y}:shortest=1[out]"
                ),
                "-map", "[out]", "-r", "30",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-an", str(mockup_path),
            ]
            subprocess.run(cmd_simple, capture_output=True, timeout=60)
            if mockup_path.exists() and mockup_path.stat().st_size > 10000:
                logger.info(f"[ProVideo] Phone mockup klaar (statisch fallback): scene {idx}")
                return mockup_path

        logger.warning(f"[ProVideo] Phone mockup volledig mislukt voor scene {idx}")
        return None

    def _add_logo_watermark(
        self, vf_parts: list[str], logo_path: str, duration: float,
    ) -> None:
        """Voeg logo watermark toe aan video filter chain.

        Klein logo (80x80px) in de linkerbovenhoek met fade-in.
        Subtiel genoeg om niet af te leiden, maar zichtbaar als branding.
        """
        if not logo_path:
            return

        # Logo wordt via overlay filter toegevoegd (niet via drawtext)
        # We gebruiken hier drawtext met een placeholder omdat overlay
        # een aparte input vereist. Logo wordt later in _create_visual_clip
        # als overlay toegevoegd als het beschikbaar is.
        # Voor nu markeren we de positie.
        pass  # Logo wordt via aparte overlay stap toegevoegd

    # ── AI Image Fallback ─────────────────────────────────────────

    def _generate_ai_clip(
        self, scene: dict, memory: dict, work_dir: Path,
        idx: int, duration: float,
    ) -> Path:
        """Genereer AI-beeld en converteer naar video clip met Ken Burns."""
        img_path = self._generate_ai_image(scene, memory, work_dir, idx)
        is_gradient = img_path.name.startswith("grad_")
        clip_path = work_dir / f"ai_clip_{idx:02d}.mp4"

        if not is_gradient:
            # Snelle Ken Burns via scale+crop (NIET zoompan — te traag op grote beelden)
            kb_effects = [
                # Langzaam inzoomen vanuit centrum
                (
                    "scale=1188:2112:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920:'(iw-1080)/2':'max(0,(ih-1920)/2*(1-t/{duration}))',"
                    "setsar=1,format=yuv420p"
                ),
                # Pan naar rechts
                (
                    "scale=1188:2112:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920:'min(iw-1080,(iw-1080)*t/{duration})':'(ih-1920)/2',"
                    "setsar=1,format=yuv420p"
                ),
                # Pan naar beneden
                (
                    "scale=1188:2112:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920:'(iw-1080)/2':'min(ih-1920,(ih-1920)*t/{duration})',"
                    "setsar=1,format=yuv420p"
                ),
                # Zoom uit
                (
                    "scale=1188:2112:force_original_aspect_ratio=increase,"
                    f"crop=1080:1920:'(iw-1080)/2*(1-t/{duration})':'(ih-1920)/2*(1-t/{duration})',"
                    "setsar=1,format=yuv420p"
                ),
            ]
            vf = kb_effects[idx % len(kb_effects)]

            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-t", str(duration),
                "-i", str(img_path),
                "-vf", vf,
                "-r", "30",
                "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
                "-pix_fmt", "yuv420p",
                "-preset", "fast", "-crf", "22",
                str(clip_path),
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)
        else:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=0x0a0d14:size=1080x1920:duration={duration}:rate=30",
                "-vf", "format=yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                str(clip_path),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)

        if clip_path.exists() and clip_path.stat().st_size > 1000:
            return clip_path

        return self._create_gradient_clip(work_dir, idx, duration)

    def _generate_ai_image(
        self, scene: dict, memory: dict, work_dir: Path, idx: int,
    ) -> Path:
        """Genereer scene-afbeelding via OpenAI."""
        api_key = os.getenv("OPENAI_API_KEY")
        output_path = work_dir / f"ai_img_{idx:02d}.png"

        if not api_key:
            return self._create_gradient_image(work_dir, idx)

        prompt = self._build_image_prompt(scene, memory)

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            logger.info(f"[ProVideo] AI image scene {idx}: {prompt[:80]}...")

            try:
                response = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt, n=1, size="1024x1792", quality="medium",
                )
            except Exception:
                response = client.images.generate(
                    model="dall-e-3",
                    prompt=prompt, n=1, size="1024x1792", quality="standard",
                )

            image_url = response.data[0].url
            if image_url:
                import httpx
                img_resp = httpx.get(image_url, timeout=30, follow_redirects=True)
                img_resp.raise_for_status()
                output_path.write_bytes(img_resp.content)
            elif hasattr(response.data[0], "b64_json") and response.data[0].b64_json:
                import base64
                output_path.write_bytes(base64.b64decode(response.data[0].b64_json))

            self.total_cost_usd += self.COST_PER_IMAGE
            return output_path

        except Exception as e:
            logger.warning(f"[ProVideo] AI image scene {idx} mislukt: {e}")
            return self._create_gradient_image(work_dir, idx)

    def _build_image_prompt(self, scene: dict, memory: dict) -> str:
        """Bouw fotorealistische prompt — consistente persoon door hele video."""
        visual_desc = scene.get("visual_description", "")
        voiceover = scene.get("voiceover", "")
        scene_type = scene.get("type", "body")
        niche = memory.get("niche", "") if memory else ""

        # Consistente persoon voor visuele samenhang
        persona = (
            "A young Dutch woman in her late 20s with dark brown shoulder-length hair, "
            "wearing casual professional clothes (dark blouse, no lab coat). "
            "She looks like a real person, not a model. Slight imperfections, natural skin."
        )

        parts = [
            "Ultra-realistic photograph, shot on Sony A7III with 35mm f/1.4 lens.",
            "Vertical 9:16 aspect ratio for mobile.",
            "Natural lighting, shallow depth of field with creamy bokeh.",
            "ABSOLUTELY NO text, NO watermarks, NO logos, NO UI elements, NO overlays.",
            "Must look like a real candid photograph, NOT a stock photo pose.",
            f"Subject: {persona}",
        ]

        if visual_desc:
            parts.append(f"Scene composition: {visual_desc}")
        elif voiceover:
            parts.append(f"Scene depicting: {voiceover[:150]}")

        if niche == "health":
            parts.append("Setting: modern healthcare office or hospital hallway.")
        elif niche:
            parts.append(f"Professional {niche} setting.")

        mood = {
            "hook": (
                "High contrast dramatic side lighting from left. "
                "Subject faces camera with engaging, curious expression. "
                "Slightly warm color temperature (5000K). "
                "Tight medium close-up, subject fills 60% of frame. "
                "Background softly blurred with warm bokeh circles."
            ),
            "problem": (
                "Cool blue-grey color temperature (6500K). "
                "Slightly underexposed, moody atmosphere. "
                "Overhead fluorescent lighting casting subtle shadows. "
                "Medium shot showing subject and cluttered environment. "
                "Subject appears focused, slightly stressed or overwhelmed."
            ),
            "solution": (
                "Warm golden hour lighting from behind subject (backlit). "
                "Soft lens flare, inviting atmosphere. "
                "Color temperature 4200K, slightly overexposed highlights. "
                "Subject appears relieved, content, or pleasantly surprised. "
                "Clean, bright, modern environment with plants or natural elements."
            ),
            "cta": (
                "Bright, high-key studio-style lighting. "
                "Clean white or light background with soft gradient. "
                "Subject appears confident, forward-looking, energetic. "
                "Vibrant but not oversaturated colors. "
                "Medium portrait shot, direct eye contact with camera."
            ),
        }
        parts.append(mood.get(scene_type, mood["hook"]))

        return " ".join(parts)

    def _create_gradient_image(self, work_dir: Path, idx: int) -> Path:
        path = work_dir / f"grad_{idx:02d}.png"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=0x0a0d14:size=1080x1920:duration=1",
            "-frames:v", "1", str(path),
        ], capture_output=True, timeout=15)
        return path

    def _create_gradient_clip(self, work_dir: Path, idx: int, duration: float) -> Path:
        path = work_dir / f"grad_clip_{idx:02d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x0a0d14:size=1080x1920:duration={duration}:rate=30",
            "-c:v", "libx264", "-preset", "fast",
            str(path),
        ], capture_output=True, timeout=30)
        return path

    # ── Scene Clip Assembly — v4 cinematisch ────────────────────────

    def _create_scene_clip(
        self, scene_data: dict, idx: int, work_dir: Path, total_scenes: int,
    ) -> Path | None:
        """Maak cinematische scene clip met scene-afhankelijke grading, captions en transitions."""
        visual = scene_data["visual"]
        audio = scene_data["audio"]
        scene = scene_data["scene"]
        duration = scene_data["duration"]

        if not visual or not visual.exists():
            logger.warning(f"[ProVideo] Geen visual voor scene {idx}")
            return None

        clip_path = work_dir / f"final_{idx:02d}.mp4"

        cmd = ["ffmpeg", "-y", "-i", str(visual)]

        if audio and audio.exists():
            cmd += ["-i", str(audio)]
        else:
            cmd += ["-f", "lavfi", "-t", str(duration),
                    "-i", "anullsrc=r=44100:cl=stereo"]

        # Font paden
        font_bold = _get_font_path(extra_bold=False)
        font_extra = _get_font_path(extra_bold=True)
        font_headline = font_extra or font_bold
        font_caption = font_bold

        # -- Video filter keten (v4 cinematisch)
        vf_parts = []
        scene_type = scene.get("type", "body")

        # 1. Scene-afhankelijke kleurcorrectie — emotionele sfeer per scene
        if scene_type == "hook":
            # Warm, cinematic hoog contrast — dramatisch, stopt het scrollen
            vf_parts.append("curves=r='0/0 0.25/0.30 0.5/0.58 0.75/0.82 1/1':g='0/0 0.5/0.52 1/1':b='0/0 0.5/0.42 1/0.95'")
            vf_parts.append("eq=saturation=1.18:contrast=1.12:brightness=0.02:gamma=0.95")
        elif scene_type == "problem":
            # Koel blauw, underexposed — moody, spanning, onbehagen
            vf_parts.append("curves=r='0/0 0.5/0.46 1/0.95':g='0/0 0.5/0.49 1/0.97':b='0/0 0.5/0.55 1/1'")
            vf_parts.append("eq=saturation=0.88:contrast=1.10:brightness=-0.03:gamma=1.08")
        elif scene_type == "solution":
            # Warm gouden gloed — hoopvol, opluchting, positief resultaat
            vf_parts.append("curves=r='0/0 0.5/0.56 1/1':g='0/0 0.5/0.53 1/1':b='0/0 0.5/0.44 1/0.92'")
            vf_parts.append("eq=saturation=1.20:contrast=1.04:brightness=0.05:gamma=0.92")
        elif scene_type == "cta":
            # Levendig, hoog energie — oproep tot actie, vibrant
            vf_parts.append("curves=r='0/0 0.5/0.54 1/1':g='0/0 0.5/0.52 1/1':b='0/0 0.5/0.47 1/0.96'")
            vf_parts.append("eq=saturation=1.25:contrast=1.08:brightness=0.03:gamma=0.94")
        else:
            vf_parts.append("curves=r='0/0 0.5/0.52 1/1':b='0/0 0.5/0.47 1/1'")
            vf_parts.append("eq=saturation=1.08:contrast=1.03:brightness=0.02")

        # 2. Vloeiende gradient onderaan (10 lagen, ultra-smooth)
        for gi in range(10):
            gy = 0.90 - gi * 0.04
            vf_parts.append(
                f"drawbox=y=ih*{gy:.2f}:w=iw:h=ih*{1-gy:.2f}:color=black@0.035:t=fill"
            )

        # 3. Subtiele top-gradient (3 lagen, smooth)
        vf_parts.append("drawbox=y=0:w=iw:h=ih*0.10:color=black@0.04:t=fill")
        vf_parts.append("drawbox=y=0:w=iw:h=ih*0.06:color=black@0.06:t=fill")
        vf_parts.append("drawbox=y=0:w=iw:h=ih*0.03:color=black@0.08:t=fill")

        # 4. Cinematische vignette
        vf_parts.append("vignette=PI/5")

        # 5. Subtiel filmgrain voor organische textuur
        vf_parts.append("noise=alls=4:allf=t")

        # On-screen headline uitgeschakeld — zag eruit als een PowerPoint slide.
        # Alleen word-by-word captions onderaan, zoals echte TikTok UGC content.

        # 7. Word-by-word animated captions (CapCut-stijl)
        voiceover = scene.get("voiceover", "")
        caption_filters = self._build_word_caption_filters(
            voiceover, duration, font_caption,
        )
        vf_parts.extend(caption_filters)

        # 8. Fade in/out — kort voor tussenscenes (xfade doet de rest)
        fade_in = 0.15 if idx > 0 else 0.3
        fade_out = 0.15 if idx < total_scenes - 1 else 0.5
        vf_parts.append(f"fade=t=in:st=0:d={fade_in}")
        vf_parts.append(f"fade=t=out:st={duration - fade_out}:d={fade_out}")

        vf_parts.append("format=yuv420p")
        vf = ",".join(vf_parts)

        cmd += ["-vf", vf]
        cmd += ["-map", "0:v", "-map", "1:a"]

        # Audio fade
        af_parts = []
        if idx > 0:
            af_parts.append(f"afade=t=in:st=0:d={fade_in}")
        if idx < total_scenes - 1:
            af_parts.append(f"afade=t=out:st={duration - fade_out}:d={fade_out}")
        if af_parts:
            cmd += ["-af", ",".join(af_parts)]

        cmd += [
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-t", str(duration),
            "-r", "30",
            "-shortest",
            str(clip_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            error_tail = result.stderr[-300:] if result.stderr else "geen output"
            logger.warning(
                f"[ProVideo] Scene {idx} clip mislukt: "
                f"{error_tail}"
            )
            if not self._allow_degraded_video():
                raise RuntimeError(f"Scene {idx} rich render mislukt: {error_tail}")
            return self._simple_clip_fallback(visual, audio, duration, clip_path)

        if clip_path.exists() and clip_path.stat().st_size > 5000:
            return clip_path

        if not self._allow_degraded_video():
            raise RuntimeError(f"Scene {idx} rich render leverde geen bruikbare output op")
        return self._simple_clip_fallback(visual, audio, duration, clip_path)

    # ── Word-by-word Animated Captions (CapCut-stijl) ─────────────

    def _build_word_caption_filters(
        self, voiceover: str, duration: float, font_path: str,
    ) -> list[str]:
        """Bouw CapCut-stijl word-by-word caption filters.

        Splitst de voiceover in chunks van 2-3 woorden die
        sequentieel verschijnen, gesynced met de geschatte
        spreektiming.
        """
        if not voiceover or not voiceover.strip():
            return []

        words = voiceover.split()
        if not words:
            return []

        # Groepeer in chunks van 2-3 woorden
        chunks = []
        i = 0
        while i < len(words):
            # Korte woorden: pak 3, langere woorden: pak 2
            chunk_size = 3 if len(words[i]) <= 4 else 2
            chunk = words[i:i + chunk_size]
            chunks.append(" ".join(chunk))
            i += chunk_size

        if not chunks:
            return []

        # Bereken timing proportioneel aan karakterlengte
        total_chars = sum(len(c) for c in chunks)
        if total_chars == 0:
            return []

        start_time = 0.2
        end_time = duration - 0.3
        available = end_time - start_time

        font_spec = f"fontfile='{font_path}':" if font_path else ""

        filters = []
        t = start_time

        for chunk in chunks:
            chunk_dur = available * (len(chunk) / total_chars)
            chunk_dur = max(0.3, chunk_dur)  # Min 0.3s per chunk
            chunk_end = min(t + chunk_dur, end_time)

            safe = _escape_drawtext(chunk)

            # CapCut-stijl — wit vetgedrukt op zwarte achtergrond pill
            filters.append(
                f"drawtext=text='{safe}':"
                f"{font_spec}"
                f"fontsize=56:fontcolor=white:"
                f"box=1:boxcolor=black@0.72:boxborderw=14:"
                f"borderw=0:"
                f"shadowcolor=black@0.5:shadowx=2:shadowy=2:"
                f"x=(w-text_w)/2:y=h*0.74:"
                f"enable='between(t,{t:.2f},{chunk_end:.2f})'"
            )

            t = chunk_end

        return filters

    # ── Beat Detection ─────────────────────────────────────────────

    @staticmethod
    def _detect_beats(music_path: Path) -> list[float]:
        """Detecteer beat-timestamps in muziektrack met librosa.

        Retourneert een gesorteerde lijst van beat-tijden in seconden.
        Bij fout of ontbrekende library: lege lijst (graceful fallback).
        """
        try:
            import librosa
            import numpy as np
            y, sr = librosa.load(str(music_path), sr=22050, mono=True, duration=60)
            tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
            # librosa >= 0.10 retourneert tempo als ndarray — flatten naar scalar
            if isinstance(tempo, np.ndarray):
                tempo = float(tempo.item()) if tempo.ndim == 0 else float(tempo[0])
            beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
            logger.debug(f"[ProVideo] Beat detection: {len(beat_times)} beats @ ~{tempo:.0f} BPM")
            return sorted(beat_times)
        except ImportError:
            logger.debug("[ProVideo] librosa niet beschikbaar, skip beat-sync")
            return []
        except Exception as e:
            logger.debug(f"[ProVideo] Beat detection mislukt: {e}")
            return []

    @staticmethod
    def _snap_to_beat(target_time: float, beats: list[float], max_shift: float = 0.4) -> float:
        """Snap een timestamp naar de dichtstbijzijnde beat (max max_shift seconden verschuiving).

        Als er geen beat dichtbij genoeg is, retourneer de originele tijd.
        """
        if not beats:
            return target_time
        closest = min(beats, key=lambda b: abs(b - target_time))
        if abs(closest - target_time) <= max_shift:
            return closest
        return target_time

    # ── Color LUT ──────────────────────────────────────────────────

    def _select_lut_for_scene(
        self, scene_type: str, memory: dict | None = None,
    ) -> Path | None:
        """Kies de juiste color LUT op basis van scene type en niche.

        LUT mapping:
        - hook: teal_orange (cinema look, attention-grabbing)
        - problem: moody_dark (somber, tension)
        - demo/feature: clean_bright (helder, UI leesbaar)
        - solution: warm_vintage (positief, gouden gloed)
        - cta: cool_modern (professioneel, actie-gericht)

        Niche override: tech/SaaS → cool_modern voor demo scenes.
        """
        if not LUT_DIR.exists():
            return None

        niche = ""
        if memory:
            niche = (memory.get("niche", "") or "").lower()

        # Scene → LUT mapping
        lut_map = {
            "hook": "teal_orange",
            "problem": "moody_dark",
            "demo": "clean_bright",
            "feature": "clean_bright",
            "solution": "warm_vintage",
            "cta": "cool_modern",
            "body": "teal_orange",
        }

        # Niche overrides
        if niche in ("tech", "saas", "software", "fintech"):
            lut_map["demo"] = "cool_modern"
            lut_map["feature"] = "cool_modern"
        elif niche in ("health", "wellness", "beauty", "lifestyle"):
            lut_map["demo"] = "warm_vintage"
            lut_map["solution"] = "warm_vintage"

        lut_name = lut_map.get(scene_type, "teal_orange")
        lut_path = LUT_DIR / f"{lut_name}.cube"

        if lut_path.exists():
            return lut_path
        return None

    # ── Background Music ──────────────────────────────────────────

    def _select_music_for_mood(self, script: dict) -> Path | None:
        """Kies de beste muziektrack op basis van het script mood."""
        if not MUSIC_DIR.exists():
            return None

        tracks = {t.stem: t for t in MUSIC_DIR.glob("*.mp3") if t.stat().st_size > 1000}
        if not tracks:
            return None

        # Bepaal mood vanuit script content
        vo = (script.get("full_voiceover_text", "") or "").lower()
        goal = ""
        for scene in script.get("scenes", []):
            if scene.get("type") == "hook":
                goal = (scene.get("notes", "") or "").lower()
                break

        # Mood mapping: script inhoud → track naam
        # Detecteer niche voor betere track keuze
        has_demo = any(s.get("type") in ("demo", "feature") for s in script.get("scenes", []))

        if any(w in vo for w in ["grappig", "lol", "haha", "crazy", "insane", "wild"]):
            preferred = ["energetic_bright", "upbeat_positive", "drive_trap"]
        elif any(w in vo for w in ["serieus", "probleem", "stress", "moe", "klaar met"]):
            preferred = ["cinematic_dark", "emotional_piano", "ambient_soft"]
        elif any(w in vo for w in ["chill", "rustig", "simpel", "makkelijk", "gewoon"]):
            preferred = ["chill_lofi", "ambient_soft", "luxury_smooth"]
        elif any(w in vo for w in ["snel", "challenge", "tip", "hack", "wist je"]):
            preferred = ["upbeat_positive", "drive_trap", "energetic_bright"]
        elif any(w in vo for w in ["premium", "luxe", "exclusief", "professioneel"]):
            preferred = ["luxury_smooth", "warm_corporate", "tech_minimal"]
        elif any(w in vo for w in ["app", "software", "platform", "tool", "dashboard"]):
            preferred = ["tech_minimal", "luxury_smooth", "chill_lofi"]
        elif has_demo:
            # Product demo videos: modern tech feel
            preferred = ["tech_minimal", "luxury_smooth", "warm_corporate"]
        else:
            preferred = ["warm_corporate", "chill_lofi", "tech_minimal", "ambient_soft"]

        # Kies eerste beschikbare preferred track
        for name in preferred:
            if name in tracks:
                return tracks[name]

        # Fallback: random
        return random.choice(list(tracks.values()))

    def _mix_background_music(self, video_path: Path, work_dir: Path, script: dict | None = None) -> None:
        """Voeg subtiele achtergrondmuziek toe aan de eindvideo."""
        track = self._select_music_for_mood(script or {})
        if not track:
            return
        video_dur = self._get_media_duration(video_path)
        if not video_dur or video_dur < 3:
            return

        output = work_dir / "with_music.mp4"
        fade_out_start = max(0, video_dur - 2.5)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1",
            "-i", str(track),
            "-filter_complex", (
                f"[1:a]atrim=0:{video_dur},"
                f"volume=0.13,"
                f"afade=t=in:d=1.5,"
                f"afade=t=out:st={fade_out_start:.1f}:d=2.5[music];"
                f"[0:a]acompressor=threshold=0.089:ratio=3:attack=5:release=50:makeup=1.2[voice];"
                f"[voice][music]amix=inputs=2:duration=first:"
                f"dropout_transition=2[aout]"
            ),
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            str(output),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode == 0 and output.exists() and output.stat().st_size > 10000:
            import shutil
            shutil.copy(str(output), str(video_path))
            logger.info("[ProVideo] Achtergrondmuziek toegevoegd")
        else:
            logger.warning(
                f"[ProVideo] Muziek mix mislukt: "
                f"{result.stderr[-200:] if result.stderr else 'geen output'}"
            )

    # ── Fallback & Helpers ────────────────────────────────────────

    def _simple_clip_fallback(
        self, visual: Path, audio: Path | None,
        duration: float, output: Path,
    ) -> Path | None:
        """Eenvoudige fallback: visual + audio zonder overlays."""
        cmd = ["ffmpeg", "-y", "-i", str(visual)]
        if audio and audio.exists():
            cmd += ["-i", str(audio)]
            cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]
        cmd += [
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-t", str(duration), "-r", "30",
            str(output),
        ]
        subprocess.run(cmd, capture_output=True, timeout=60)
        return output if output.exists() else None

    def _concatenate_clips(self, clips: list[Path], output_path: Path) -> None:
        """Voeg scene clips samen met crossfade transities."""
        if len(clips) == 1:
            import shutil
            shutil.copy(clips[0], output_path)
            return

        # Probeer crossfade (cinematischer), fallback naar simpele concat
        if self._crossfade_concat(clips, output_path):
            return
        self._simple_concat(clips, output_path)

    def _crossfade_concat(
        self, clips: list[Path], output_path: Path, xfade_dur: float = 0.4,
    ) -> bool:
        """Crossfade transities tussen scenes voor vloeiende overgang."""
        # Haal duraties op
        durations = []
        for clip in clips:
            d = self._get_media_duration(clip)
            if not d or d < xfade_dur * 2:
                logger.info("[ProVideo] Clip te kort voor crossfade, fallback naar concat")
                return False
            durations.append(d)

        inputs = []
        for clip in clips:
            inputs += ["-i", str(clip)]

        # Bouw xfade chain
        transitions = ["fade", "fadeblack", "smoothleft", "smoothup", "fade"]
        vf_parts = []
        af_parts = []

        for i in range(len(clips) - 1):
            in_v1 = f"[{i}:v]" if i == 0 else f"[v{i}]"
            in_v2 = f"[{i+1}:v]"
            out_v = "[vout]" if i == len(clips) - 2 else f"[v{i+1}]"

            # offset = som van alle clip-duraties tot en met clip i, minus (i+1) * xfade_dur
            offset = sum(durations[:i+1]) - (i + 1) * xfade_dur
            transition = transitions[i % len(transitions)]

            vf_parts.append(
                f"{in_v1}{in_v2}xfade=transition={transition}:"
                f"duration={xfade_dur}:offset={offset:.3f}{out_v}"
            )

            # Audio crossfade
            in_a1 = f"[{i}:a]" if i == 0 else f"[a{i}]"
            in_a2 = f"[{i+1}:a]"
            out_a = "[aout]" if i == len(clips) - 2 else f"[a{i+1}]"
            af_parts.append(
                f"{in_a1}{in_a2}acrossfade=d={xfade_dur}{out_a}"
            )

        filter_complex = ";".join(vf_parts + af_parts)

        cmd = ["ffmpeg", "-y", "-threads", *_FFMPEG_THREADS] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-r", "30", "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 10000:
            logger.info(f"[ProVideo] Crossfade concat klaar: {len(clips)} clips")
            return True

        logger.warning(
            f"[ProVideo] Crossfade mislukt, fallback naar simpele concat: "
            f"{result.stderr[-300:] if result.stderr else 'geen output'}"
        )
        return False

    def _simple_concat(self, clips: list[Path], output_path: Path) -> None:
        """Simpele concat fallback (zonder crossfade)."""
        concat_file = clips[0].parent / "concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for clip in clips:
                safe_path = str(clip).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            "ffmpeg", "-y", "-threads", *_FFMPEG_THREADS,
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-r", "30",
            "-movflags", "+faststart",
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"[ProVideo] Concat mislukt: {result.stderr[-500:]}")
            raise RuntimeError("Video concat mislukt")

    def _get_media_duration(self, path: Path) -> float | None:
        """Haal duur van audio/video op via ffprobe."""
        return _probe_duration_seconds(path)


# ── Text helpers ──────────────────────────────────────────────────

def _escape_drawtext(text: str) -> str:
    """Escape tekst voor FFmpeg drawtext filter."""
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


def _wrap_text(text: str, max_chars: int = 38) -> list[str]:
    """Splits tekst in regels van max_chars breed."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
