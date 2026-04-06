"""
OpenAI Image Video Provider v2 — genereert cinematic TikTok video's via DALL-E.

Pipeline:
1. Per scene: genereer afbeelding via gpt-image-1 / DALL-E 3 (1024x1792, portrait)
2. Voiceover via ElevenLabs (primair) of OpenAI TTS (fallback)
3. FFmpeg assembleert:
   - Ken Burns effect (scene-type aware: snelle zoom hook, langzame pan body)
   - Crossfade transitions (0.4s)
   - Achtergrondmuziek (sidechain ducking tijdens voice)
   - Tekst slide-in animaties met schaduw en gradient bar
   - Ondertiteling onderaan
   - App branding

Kosten: ~$0.02-0.08 per video (DALL-E beelden) + ElevenLabs credits.
"""

import os
import random
import subprocess
import uuid
from pathlib import Path

from loguru import logger

try:
    from utils.runtime_paths import ensure_dir, get_generated_assets_dir
    ASSETS_DIR = ensure_dir(get_generated_assets_dir())
except Exception:
    ASSETS_DIR = Path(__file__).parent.parent.parent / "assets" / "generated"
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

ROOT = Path(__file__).parent.parent.parent
MUSIC_DIR = ROOT / "assets" / "music"
FONT_DIR = ROOT / "assets" / "fonts"

# Muziek per mood/scene-type
_MUSIC_MAP = {
    "hook":     ["energetic_bright.mp3", "upbeat_positive.mp3", "drive_trap.mp3"],
    "problem":  ["ambient_soft.mp3", "emotional_piano.mp3", "chill_lofi.mp3"],
    "solution": ["upbeat_positive.mp3", "luxury_smooth.mp3", "warm_corporate.mp3"],
    "cta":      ["energetic_bright.mp3", "tech_minimal.mp3", "drive_trap.mp3"],
    "body":     ["chill_lofi.mp3", "ambient_soft.mp3", "warm_corporate.mp3"],
    "default":  ["chill_lofi.mp3", "ambient_soft.mp3", "upbeat_positive.mp3"],
}

# Ken Burns presets per scene-type (sneller = meer energie)
_KB_PRESETS = {
    "hook": [
        # Snelle zoom-in — trekt aandacht
        ("min(1+0.002*on,1.12)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        # Snelle diagonal pan
        ("1.10", "(iw-iw/zoom)*on/{f}", "(ih-ih/zoom)*on/{f}"),
    ],
    "problem": [
        # Langzame zoom — drukt gevoel uit
        ("min(1+0.0006*on,1.06)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        # Trage pan omhoog
        ("1.06", "(iw-iw/zoom)/2", "(ih-ih/zoom)*(1-on/{f})"),
    ],
    "solution": [
        # Zoom-out — opluchting
        ("max(1.08-0.0008*on,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        # Pan links-rechts medium
        ("1.07", "(iw-iw/zoom)*on/{f}", "(ih-ih/zoom)/2"),
    ],
    "cta": [
        # Snelle zoom-in + slight shake
        ("min(1+0.0018*on,1.10)", "iw/2-(iw/zoom/2)+(iw*0.002*sin(on*0.3))", "ih/2-(ih/zoom/2)"),
    ],
    "body": [
        # Rustige pan
        ("1.05", "(iw-iw/zoom)*on/{f}", "(ih-ih/zoom)/2"),
        ("1.05", "(iw-iw/zoom)/2", "(ih-ih/zoom)*on/{f}"),
        # Subtiele zoom
        ("min(1+0.0005*on,1.04)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ],
}


def _resolve_ffmpeg() -> str:
    import shutil
    custom = os.getenv("FFMPEG_BINARY", "").strip()
    if custom:
        return custom
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        exe = get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"


def _fontfile_arg() -> str:
    for name in ("Poppins-Bold.ttf", "Montserrat-Bold.ttf"):
        p = FONT_DIR / name
        if p.exists():
            esc = str(p).replace("\\", "/").replace(":", "\\:")
            return f"fontfile='{esc}':"
    return ""


def _escape_ffmpeg(text: str) -> str:
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
        .replace("\n", " ")
    )


def _select_music(scenes: list[dict]) -> Path | None:
    """Kies achtergrondmuziek passend bij de eerste scene-type."""
    if not MUSIC_DIR.exists():
        return None
    first_type = scenes[0].get("type", "default") if scenes else "default"
    candidates = _MUSIC_MAP.get(first_type, _MUSIC_MAP["default"])
    random.shuffle(candidates)
    for name in candidates:
        p = MUSIC_DIR / name
        if p.exists():
            return p
    # Any mp3 in music dir
    mp3s = list(MUSIC_DIR.glob("*.mp3"))
    return random.choice(mp3s) if mp3s else None


class OpenAIImageProvider:
    """Maakt TikTok video's door AI-gegenereerde afbeeldingen te combineren met FFmpeg."""

    COST_PER_IMAGE = 0.02

    def __init__(self):
        self.total_cost_usd = 0.0

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        video_id = str(uuid.uuid4())[:8]
        output_path = output_dir / f"ai_video_{video_id}.mp4"
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = script.get("scenes", [])
        if not scenes:
            scenes = [{"voiceover": "Content wordt gegenereerd...", "duration_sec": 5, "type": "hook"}]

        # Stap 1: DALL-E afbeeldingen per scene
        image_dir = ASSETS_DIR / "images" / video_id
        image_dir.mkdir(parents=True, exist_ok=True)
        image_paths = []
        for i, scene in enumerate(scenes):
            img_path = self._generate_scene_image(scene, memory, image_dir, i, len(scenes))
            image_paths.append(img_path)

        # Stap 2: Voiceover
        voiceover_text = script.get("full_voiceover_text", "")
        if not voiceover_text:
            voiceover_text = " ".join(
                s.get("voiceover", "").strip() for s in scenes if s.get("voiceover", "").strip()
            )
        audio_path = self._generate_voiceover(voiceover_text, video_id)

        # Stap 3: Selecteer muziek
        music_path = _select_music(scenes)
        if music_path:
            logger.info(f"[OpenAIImage] Muziek: {music_path.name}")

        # Stap 4: Assembleer
        self._assemble_video(
            image_paths=image_paths,
            scenes=scenes,
            audio_path=audio_path,
            music_path=music_path,
            output_path=output_path,
            memory=memory,
        )

        logger.success(f"[OpenAIImage] Video klaar: {output_path} | kosten=${self.total_cost_usd:.3f}")
        return output_path

    # ── Image Generation ─────────────────────────────────────────────

    def _generate_scene_image(
        self, scene: dict, memory: dict, image_dir: Path, index: int, total_scenes: int
    ) -> Path:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return self._create_fallback_image(scene, image_dir, index)

        prompt = self._build_image_prompt(scene, memory, index, total_scenes)
        output_path = image_dir / f"scene_{index:02d}.png"

        try:
            import openai
            client = openai.OpenAI(api_key=api_key, timeout=60.0)
            logger.info(f"[OpenAIImage] Genereer scene {index + 1}/{total_scenes}: {prompt[:80]}...")

            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size="1024x1792",
                quality="standard",
            )

            image_url = response.data[0].url
            if image_url:
                import httpx
                r = httpx.get(image_url, timeout=45, follow_redirects=True)
                r.raise_for_status()
                output_path.write_bytes(r.content)
            elif hasattr(response.data[0], "b64_json") and response.data[0].b64_json:
                import base64
                output_path.write_bytes(base64.b64decode(response.data[0].b64_json))

            self.total_cost_usd += self.COST_PER_IMAGE
            logger.info(f"[OpenAIImage] Scene {index + 1} klaar: {output_path}")
            return output_path

        except Exception as e:
            logger.warning(f"[OpenAIImage] Scene {index + 1} mislukt: {e}")
            return self._create_fallback_image(scene, image_dir, index)

    def _build_image_prompt(
        self, scene: dict, memory: dict, index: int, total_scenes: int
    ) -> str:
        """Bouw een cinematic DALL-E prompt voor TikTok/Reels content."""
        visual_style = memory.get("visual_style", {}) if memory else {}
        app_name = memory.get("app_name", "") if memory else ""
        niche = memory.get("niche", "") if memory else ""
        color_scheme = visual_style.get("color_scheme", "")

        scene_type = scene.get("type", "body")
        visual_desc = scene.get("visual_description", "")
        voiceover = scene.get("voiceover", "")

        # Cinematic stijl basis
        parts = [
            "Ultra-realistic cinematic photography, vertical 9:16 portrait format.",
            "Professional color grading, shallow depth of field.",
            "NO text, NO watermarks, NO logos, NO UI elements in the image.",
        ]

        # Scene-specifieke visuele stijl
        if scene_type == "hook":
            parts.append(
                "Bold, eye-catching composition. Close-up or dramatic angle. "
                "High contrast, vibrant colors. First frame that stops scrolling."
            )
        elif scene_type == "problem":
            parts.append(
                "Moody, relatable atmosphere. Shows frustration or challenge. "
                "Slightly desaturated, real-life feel."
            )
        elif scene_type == "solution":
            parts.append(
                "Bright, optimistic, uplifting composition. "
                "Clean, aspirational aesthetic. Warm natural lighting."
            )
        elif scene_type == "cta":
            parts.append(
                "Dynamic, action-oriented. Energetic composition. "
                "Bright with strong focal point."
            )
        else:
            parts.append("Natural, authentic feel. Lifestyle photography style.")

        # Inhoud van de scene
        if visual_desc:
            parts.append(f"Subject: {visual_desc}")
        elif voiceover:
            # Extraheer visueel relevante informatie uit voiceover
            short = voiceover[:180]
            parts.append(f"Visualize this concept: {short}")

        # Niche context
        if niche:
            parts.append(f"Industry context: {niche}.")

        # App/brand context
        if app_name:
            parts.append(f"For {app_name} brand content.")

        # Kleurstijl
        if color_scheme:
            parts.append(f"Color palette: {color_scheme}.")
        else:
            # Standaard dark/premium TikTok stijl
            parts.append(
                "Dark premium aesthetic with accent lighting. "
                "Cinematic teal and orange color grade."
            )

        # Consistentie over scenes
        if total_scenes > 1:
            parts.append("Consistent visual style throughout the series.")

        return " ".join(parts)

    def _create_fallback_image(self, scene: dict, image_dir: Path, index: int) -> Path:
        output_path = image_dir / f"scene_{index:02d}.png"
        ffmpeg = _resolve_ffmpeg()
        # Gradient placeholder (donkerblauw → paars)
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", "color=c=0x0a0d14:size=1080x1920:duration=1",
            "-frames:v", "1",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode != 0 or not output_path.exists():
            logger.warning(f"[OpenAIImage] Fallback afbeelding mislukt voor scene {index}, gebruik lege PNG")
            # Maak een minimale geldige PNG via Python als FFmpeg ook faalt
            try:
                import struct, zlib
                def _minimal_png(w: int, h: int) -> bytes:
                    def chunk(name, data):
                        c = struct.pack(">I", len(data)) + name + data
                        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
                    raw = b"".join(b"\x00" + b"\x0a\x0d\x14" * w for _ in range(h))
                    return (b"\x89PNG\r\n\x1a\n"
                            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                            + chunk(b"IDAT", zlib.compress(raw))
                            + chunk(b"IEND", b""))
                output_path.write_bytes(_minimal_png(1080, 1920))
            except Exception:
                output_path.write_bytes(b"")
        return output_path

    # ── Video Assembly ────────────────────────────────────────────────

    def _assemble_video(
        self,
        image_paths: list[Path],
        scenes: list[dict],
        audio_path: Path | None,
        music_path: Path | None,
        output_path: Path,
        memory: dict,
    ) -> None:
        if not image_paths:
            raise RuntimeError("Geen afbeeldingen om te assembleren")

        ffmpeg = _resolve_ffmpeg()
        font_arg = _fontfile_arg()
        visual_style = memory.get("visual_style", {}) if memory else {}
        accent = visual_style.get("accent_color", "#6C63FF")
        app_name = memory.get("app_name", "") if memory else ""

        durations = [max(2.0, float(scene.get("duration_sec") or 5)) for scene in scenes]
        total_duration = sum(durations)
        crossfade = 0.4

        # ── Inputs ──────────────────────────────────────────────────
        cmd = [ffmpeg, "-y"]
        for i, img_path in enumerate(image_paths):
            d = durations[i] if i < len(durations) else 5
            cmd += ["-loop", "1", "-t", str(d + crossfade + 0.1), "-i", str(img_path)]

        audio_idx = len(image_paths)
        has_voice = audio_path and audio_path.exists()
        has_music = music_path and music_path.exists()

        if has_voice:
            cmd += ["-i", str(audio_path)]
        if has_music:
            cmd += ["-stream_loop", "-1", "-i", str(music_path)]

        # ── Filter Complex ───────────────────────────────────────────
        filters = []
        n = len(image_paths)

        # Ken Burns per scene — snelle scale+crop aanpak (geen zoompan, veel sneller op server)
        # Schaal naar 1200x2133 (10% groter), crop met lineaire offset voor pan-effect
        for i, scene in enumerate(scenes):
            d = durations[i] if i < len(durations) else 5
            total_d = d + crossfade + 0.1
            scene_type = scene.get("type", "body")

            # Pan richting afwisselen per scene voor dynamisch gevoel
            effect = i % 4
            if effect == 0:
                # Pan links → rechts
                vf = (f"scale=1200:2133:force_original_aspect_ratio=increase,"
                      f"crop=1080:1920:'(iw-1080)*t/{total_d}':'(ih-1920)/2',"
                      f"setsar=1,format=yuv420p")
            elif effect == 1:
                # Pan boven → onder
                vf = (f"scale=1200:2133:force_original_aspect_ratio=increase,"
                      f"crop=1080:1920:'(iw-1080)/2':'(ih-1920)*t/{total_d}',"
                      f"setsar=1,format=yuv420p")
            elif effect == 2:
                # Pan rechts → links
                vf = (f"scale=1200:2133:force_original_aspect_ratio=increase,"
                      f"crop=1080:1920:'(iw-1080)*(1-t/{total_d})':'(ih-1920)/2',"
                      f"setsar=1,format=yuv420p")
            else:
                # Pan onder → boven
                vf = (f"scale=1200:2133:force_original_aspect_ratio=increase,"
                      f"crop=1080:1920:'(iw-1080)/2':'(ih-1920)*(1-t/{total_d})',"
                      f"setsar=1,format=yuv420p")

            # Input is al -loop 1 -t duration, dus geen extra loop filter nodig
            filters.append(
                f"[{i}:v]trim=duration={total_d},"
                f"setpts=PTS-STARTPTS,{vf}[v{i}]"
            )

        # Crossfade keten
        if n == 1:
            filters.append(f"[v0]trim=duration={durations[0]},setpts=PTS-STARTPTS[vblend]")
        else:
            prev = "v0"
            offset = durations[0] - crossfade
            for i in range(1, n):
                out = f"cf{i}" if i < n - 1 else "vblend"
                # Varieer transities per scene-type
                transition = "fade"
                if scenes[i].get("type") == "hook":
                    transition = "slideleft"
                elif scenes[i].get("type") == "cta":
                    transition = "slideup"
                filters.append(
                    f"[{prev}][v{i}]xfade=transition={transition}:duration={crossfade}"
                    f":offset={max(0, offset):.2f}[{out}]"
                )
                prev = out
                if i < n - 1:
                    offset += durations[i] - crossfade

        # Tekst overlays — slide-in animatie
        current_t = 0.0
        last_v = "vblend"

        for i, scene in enumerate(scenes):
            d = durations[i] if i < len(durations) else 5
            scene_type = scene.get("type", "body")
            main_text = scene.get("on_screen_text") or ""
            subtitle = scene.get("voiceover", "")

            if main_text:
                safe = _escape_ffmpeg(main_text[:90])
                fontsize = 58 if scene_type == "hook" else 44
                y_pos = 680 if scene_type == "hook" else 760
                vis_start = current_t
                vis_end = current_t + d
                slide_in = current_t + 0.25
                slide_out = current_t + d - 0.35

                # Gradient bar achter tekst
                bar_alpha = f"if(lt(t,{vis_start}),0,if(lt(t,{slide_in}),(t-{vis_start})/0.25,if(gt(t,{slide_out}),({vis_end}-t)/0.35,1)))"
                filters.append(
                    f"[{last_v}]drawbox="
                    f"x=0:y={y_pos - 8}:w=iw:h={fontsize + 30}:"
                    f"color=black@0.45:t=fill:"
                    f"enable='between(t,{vis_start},{vis_end})'[bar{i}]"
                )
                last_v = f"bar{i}"

                # Schaduw tekst
                filters.append(
                    f"[{last_v}]drawtext="
                    f"text='{safe}':"
                    f"{font_arg}"
                    f"fontcolor=black@0.6:fontsize={fontsize}:"
                    f"x=(w-text_w)/2+3:y={y_pos + 3}:"
                    f"enable='between(t,{vis_start},{vis_end})':"
                    f"alpha='{bar_alpha}'[sh{i}]"
                )
                # Hoofd tekst (wit)
                filters.append(
                    f"[sh{i}]drawtext="
                    f"text='{safe}':"
                    f"{font_arg}"
                    f"fontcolor=white:fontsize={fontsize}:"
                    f"x=(w-text_w)/2:y={y_pos}:"
                    f"enable='between(t,{vis_start},{vis_end})':"
                    f"alpha='{bar_alpha}'[mt{i}]"
                )
                last_v = f"mt{i}"

            # Ondertiteling (captions onderaan)
            if subtitle and subtitle != main_text:
                safe_sub = _escape_ffmpeg(subtitle[:130])
                sub_start = current_t
                sub_end = current_t + d
                sub_fade = f"if(lt(t,{sub_start+0.2}),(t-{sub_start})/0.2,if(gt(t,{sub_end-0.3}),({sub_end}-t)/0.3,1))"
                filters.append(
                    f"[{last_v}]drawtext="
                    f"text='{safe_sub}':"
                    f"{font_arg}"
                    f"fontcolor=white@0.9:fontsize=27:"
                    f"x=(w-text_w)/2:y=h-180:"
                    f"borderw=2:bordercolor=black@0.7:"
                    f"enable='between(t,{sub_start},{sub_end})':"
                    f"alpha='{sub_fade}'[cap{i}]"
                )
                last_v = f"cap{i}"

            current_t += d

        # App branding (linksonder)
        if app_name:
            safe_name = _escape_ffmpeg(app_name[:30])
            filters.append(
                f"[{last_v}]drawtext="
                f"text='{safe_name}':"
                f"{font_arg}"
                f"fontcolor=white@0.35:fontsize=24:"
                f"x=36:y=h-56[branded]"
            )
            last_v = "branded"

        filters.append(f"[{last_v}]format=yuv420p[outv]")

        # ── Audio mixing: voice + muziek sidechain ───────────────────
        music_idx = audio_idx + (1 if has_voice else 0)

        if has_voice and has_music:
            # Muziek zacht (0.10) gemixed met voice — geen sidechaincompress (niet altijd beschikbaar)
            filters.append(
                f"[{audio_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo[voice]"
            )
            filters.append(
                f"[{music_idx}:a]volume=0.10,aformat=sample_rates=44100:channel_layouts=stereo[music_bg]"
            )
            filters.append(
                "[voice][music_bg]amix=inputs=2:duration=shortest:normalize=0[outa]"
            )
            audio_map = ["-map", "[outa]"]
        elif has_voice:
            audio_map = ["-map", f"{audio_idx}:a"]
        elif has_music:
            filters.append(
                f"[{music_idx}:a]volume=0.20[outa]"
            )
            audio_map = ["-map", "[outa]"]
        else:
            audio_map = []

        # ── Command finaliseren ──────────────────────────────────────
        filter_complex = ";".join(filters)
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[outv]",
        ]
        cmd += audio_map

        if audio_map:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]

        threads = os.getenv("FFMPEG_THREADS", "2")
        cmd += [
            "-c:v", "libx264",
            "-profile:v", "high",
            "-preset", "fast",
            "-crf", "22",
            "-threads", threads,
            "-r", "30",
            "-t", str(total_duration),
            str(output_path),
        ]

        logger.info(f"[OpenAIImage] Render {n} scenes, {total_duration:.1f}s...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=480)

        if result.returncode != 0:
            stderr_tail = (result.stderr or "")[-1200:]
            logger.error(f"[OpenAIImage] FFmpeg fout:\n{stderr_tail}")
            raise RuntimeError(f"FFmpeg mislukt (code {result.returncode})")

    # ── Voiceover ─────────────────────────────────────────────────────

    def _generate_voiceover(self, text: str, video_id: str) -> Path | None:
        if not text.strip():
            return None

        # ElevenLabs primair (native NL stemmen)
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
        if elevenlabs_key:
            result = self._voiceover_elevenlabs(text, video_id, elevenlabs_key)
            if result:
                return result

        # OpenAI TTS fallback
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return self._voiceover_openai(text, video_id, openai_key)

        logger.info("[OpenAIImage] Geen TTS beschikbaar — video zonder audio")
        return None

    def _voiceover_openai(self, text: str, video_id: str, api_key: str) -> Path | None:
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            audio_path = ASSETS_DIR / "audio" / f"vo_{video_id}.mp3"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            response = client.audio.speech.create(
                model="tts-1-hd",
                voice="onyx",
                input=text[:4096],
                response_format="mp3",
            )
            response.stream_to_file(str(audio_path))
            self.total_cost_usd += len(text) / 1000 * 0.015
            logger.info(f"[OpenAIImage] OpenAI TTS klaar: {audio_path}")
            return audio_path
        except Exception as e:
            logger.warning(f"[OpenAIImage] OpenAI TTS mislukt: {e}")
            return None

    def _voiceover_elevenlabs(self, text: str, video_id: str, api_key: str) -> Path | None:
        try:
            import httpx
            # Gebruik cloned voice als beschikbaar, anders standaard NL stem
            voice_id = (
                os.getenv("ELEVENLABS_CLONE_VOICE_ID")
                or os.getenv("ELEVENLABS_VOICE_ID")
                or "7qdUFMklKPaaAVMsBTBt"  # Roos — native NL vrouw
            )
            audio_path = ASSETS_DIR / "audio" / f"vo_{video_id}.mp3"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            response = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": text[:5000],
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {
                        "stability": 0.45,
                        "similarity_boost": 0.80,
                        "style": 0.35,
                        "use_speaker_boost": True,
                    },
                },
                timeout=45,
            )
            response.raise_for_status()
            audio_path.write_bytes(response.content)
            logger.info(f"[OpenAIImage] ElevenLabs voiceover klaar: {audio_path}")
            return audio_path
        except Exception as e:
            logger.warning(f"[OpenAIImage] ElevenLabs mislukt: {e}")
            return None
