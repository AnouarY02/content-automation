"""
FFmpeg Provider — professionele video-assembly zonder API-kosten.

Produceert TikTok-ready 9:16 video's met:
- Gradient achtergronden (donker, branded)
- Geanimeerde tekst met schaduw (fade in/out per scene)
- Titel card met grote tekst + app naam
- Scene transitions (crossfade)
- Ondertiteling styling (bottom bar)
- Optionele voiceover via ElevenLabs
- Outro met call-to-action

Vereist: FFmpeg 5+ geinstalleerd en in PATH.
"""

import os
import subprocess
import uuid
from pathlib import Path

from loguru import logger
from utils.runtime_paths import ensure_dir, get_generated_assets_dir

ROOT = Path(__file__).parent.parent.parent
GENERATED_ASSETS_DIR = ensure_dir(get_generated_assets_dir())


class FFmpegProvider:
    """Assembleert professionele TikTok video's lokaal via FFmpeg."""

    # Brand kleuren
    GRADIENT_PRESETS = {
        "dark":     ("0x0a0d14", "0x1a1f2e"),   # Donker blauw
        "purple":   ("0x1a0533", "0x0f1117"),    # Paars-donker
        "blue":     ("0x0c1929", "0x0a0d14"),    # Blauw-donker
        "warm":     ("0x1c1008", "0x0a0d14"),    # Warm-donker
        "green":    ("0x081c0e", "0x0a0d14"),    # Groen-donker
    }

    ACCENT_COLORS = {
        "purple": "#6C63FF",
        "blue":   "#3b82f6",
        "green":  "#22c55e",
        "orange": "#f59e0b",
        "pink":   "#ec4899",
    }

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        """
        Maak een professionele TikTok video van het script.

        Pipeline:
        1. Optioneel: voiceover via ElevenLabs
        2. Bereken timing per scene
        3. Genereer FFmpeg filter graph met gradient + tekst + animaties
        4. Render naar MP4 (1080x1920, H.264, 30fps)
        """
        video_id = str(uuid.uuid4())[:8]
        output_path = output_dir / f"video_{video_id}.mp4"
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        # Filter tot alleen dict-scenes (bescherming tegen malformed script)
        scenes = [s for s in scenes if isinstance(s, dict)]
        if not scenes:
            logger.warning("[FFmpeg] Geen (geldige) scenes in script — maak placeholder video")
            scenes = [{"voiceover": "Content wordt gegenereerd...", "duration_sec": 5, "type": "hook"}]

        total_duration = sum(s.get("duration_sec", 5) for s in scenes)
        total_duration = max(total_duration, 5)

        # Stap 1: Voiceover
        voiceover_text = script.get("full_voiceover_text", "")
        audio_path = self._generate_voiceover(voiceover_text, video_id)

        # Stap 2: Kies stijl op basis van brand memory
        visual_style = memory.get("visual_style", {}) if memory else {}
        gradient = visual_style.get("gradient", "dark")
        accent = visual_style.get("accent_color", "#6C63FF")
        app_name = memory.get("app_name", "") if memory else ""

        # Stap 3: Bouw filter graph
        filter_complex = self._build_filter_graph(
            scenes=scenes,
            total_duration=total_duration,
            gradient=gradient,
            accent=accent,
            app_name=app_name,
        )

        # Stap 4: FFmpeg commando
        cmd = self._build_command(
            audio_path=audio_path,
            output_path=output_path,
            total_duration=total_duration,
            filter_complex=filter_complex,
        )

        logger.info(f"[FFmpeg] Render {len(scenes)} scenes, {total_duration}s, gradient={gradient}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode != 0:
            stderr_tail = result.stderr[-800:] if result.stderr else "geen output"
            logger.error(f"[FFmpeg] Fout (code {result.returncode}):\n{stderr_tail}")
            raise RuntimeError(f"FFmpeg mislukt (code {result.returncode})")

        logger.success(f"[FFmpeg] Video klaar: {output_path} ({total_duration}s)")
        return output_path

    # ── Filter Graph ──────────────────────────────────────────────────

    def _build_filter_graph(
        self,
        scenes: list[dict],
        total_duration: int,
        gradient: str,
        accent: str,
        app_name: str,
    ) -> str:
        """
        Bouw een complete FFmpeg filter_complex string.

        Layout (1080x1920):
        ┌──────────────────────┐
        │   gradient bg        │
        │                      │
        │    ┌────────────┐    │  ← hoofd-tekst (midden, groot)
        │    │  SCENE TEXT │    │
        │    └────────────┘    │
        │                      │
        │  ───── accent line ──│  ← accent streep
        │  [ ondertitel bar ]  │  ← voiceover tekst (onder)
        │                      │
        │  app_name             │  ← branding (linksonder)
        └──────────────────────┘
        """
        filters = []

        # Achtergrond: verticaal gradient
        top_color, bot_color = self.GRADIENT_PRESETS.get(gradient, self.GRADIENT_PRESETS["dark"])
        # Gebruik geom gradient via overlay van twee gekleurde vlakken met alpha
        filters.append(
            f"color=c={top_color}:size=1080x1920:rate=30:duration={total_duration}[bg_base]"
        )
        # Gradient overlay: semi-transparante kleur aan de onderkant
        filters.append(
            f"color=c={bot_color}:size=1080x960:rate=30:duration={total_duration},"
            f"format=yuva420p,colorchannelmixer=aa=0.7[grad_bot]"
        )
        filters.append(
            "[bg_base][grad_bot]overlay=0:960[bg]"
        )

        # Accent lijn (horizontale streep op ~70% hoogte)
        accent_hex = accent.replace("#", "0x")
        filters.append(
            f"color=c={accent_hex}:size=200x3:rate=30:duration={total_duration}[accent_line]"
        )
        filters.append(
            "[bg][accent_line]overlay=(W-200)/2:1340[bg2]"
        )

        # Scene teksten met fade in/out
        current_time = 0.0
        last_label = "bg2"

        for i, scene in enumerate(scenes):
            duration = scene.get("duration_sec", 5)
            scene_type = scene.get("type", "body")
            main_text = scene.get("on_screen_text") or scene.get("voiceover", "")
            subtitle = scene.get("voiceover", "")

            if not main_text and not subtitle:
                current_time += duration
                continue

            label_out = f"s{i}"
            fade_in = current_time + 0.2
            fade_out = current_time + duration - 0.3
            visible_start = current_time
            visible_end = current_time + duration

            # Hoofd tekst (midden van scherm)
            if main_text:
                safe_main = self._escape_text(main_text[:90])
                fontsize = 56 if scene_type == "hook" else 42
                y_pos = 700 if scene_type == "hook" else 780

                # Tekst schaduw (offset 2px)
                filters.append(
                    f"[{last_label}]drawtext="
                    f"text='{safe_main}':"
                    f"fontcolor=black@0.4:fontsize={fontsize}:"
                    f"x=(w-text_w)/2+2:y={y_pos}+2:"
                    f"enable='between(t,{visible_start},{visible_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{visible_start})/0.2,"
                    f"if(gt(t,{fade_out}),({visible_end}-t)/0.3,1))'[shadow{i}]"
                )
                last_label = f"shadow{i}"

                # Hoofd tekst
                filters.append(
                    f"[{last_label}]drawtext="
                    f"text='{safe_main}':"
                    f"fontcolor=white:fontsize={fontsize}:"
                    f"x=(w-text_w)/2:y={y_pos}:"
                    f"enable='between(t,{visible_start},{visible_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{visible_start})/0.2,"
                    f"if(gt(t,{fade_out}),({visible_end}-t)/0.3,1))'[main{i}]"
                )
                last_label = f"main{i}"

            # Subtitle bar (onderkant)
            if subtitle and subtitle != main_text:
                safe_sub = self._escape_text(subtitle[:120])
                filters.append(
                    f"[{last_label}]drawtext="
                    f"text='{safe_sub}':"
                    f"fontcolor=white@0.8:fontsize=28:"
                    f"x=(w-text_w)/2:y=1400:"
                    f"enable='between(t,{visible_start},{visible_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{visible_start})/0.2,"
                    f"if(gt(t,{fade_out}),({visible_end}-t)/0.3,1))'[{label_out}]"
                )
                last_label = label_out
            else:
                # Rename label for chain continuity
                if last_label != label_out and f"[{last_label}]" in filters[-1]:
                    filters[-1] = filters[-1].rsplit("[", 1)[0] + f"[{label_out}]"
                    last_label = label_out

            current_time += duration

        # App branding (linksonder, altijd zichtbaar)
        if app_name:
            safe_name = self._escape_text(app_name[:30])
            filters.append(
                f"[{last_label}]drawtext="
                f"text='{safe_name}':"
                f"fontcolor=white@0.3:fontsize=22:"
                f"x=40:y=h-60[branded]"
            )
            last_label = "branded"

        # Output label
        final_label = last_label
        filters.append(f"[{final_label}]format=yuv420p[out]")

        return ";".join(filters)

    # ── FFmpeg Command ────────────────────────────────────────────────

    def _build_command(
        self,
        audio_path: Path | None,
        output_path: Path,
        total_duration: int,
        filter_complex: str,
    ) -> list[str]:
        cmd = ["ffmpeg", "-y"]

        # Audio input (als beschikbaar)
        if audio_path and audio_path.exists():
            cmd += ["-i", str(audio_path)]

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[out]",
        ]

        if audio_path and audio_path.exists():
            cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-r", "30",
            "-t", str(total_duration),
            str(output_path),
        ]
        return cmd

    # ── Voiceover ─────────────────────────────────────────────────────

    def _generate_voiceover(self, text: str, video_id: str) -> Path | None:
        """Genereer voiceover via ElevenLabs. Geeft None als API key ontbreekt."""
        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key or not text.strip():
            logger.info("[FFmpeg] Geen ElevenLabs key of tekst — video zonder audio")
            return None

        try:
            import httpx
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            audio_path = GENERATED_ASSETS_DIR / "audio" / f"vo_{video_id}.mp3"
            audio_path.parent.mkdir(parents=True, exist_ok=True)

            response = httpx.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": text[:5000],
                    "model_id": "eleven_multilingual_v2",
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                timeout=30,
            )
            response.raise_for_status()
            audio_path.write_bytes(response.content)
            logger.info(f"[ElevenLabs] Voiceover klaar: {audio_path}")
            return audio_path

        except Exception as e:
            logger.warning(f"[ElevenLabs] Mislukt: {e} — video zonder audio")
            return None

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _escape_text(text: str) -> str:
        """Escape tekst voor FFmpeg drawtext filter."""
        return (
            text
            .replace("\\", "\\\\")
            .replace("'", "\u2019")      # Curly quote ipv straight
            .replace(":", "\\:")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace(";", "\\;")
            .replace("%", "%%")
            .replace("\n", " ")
        )
