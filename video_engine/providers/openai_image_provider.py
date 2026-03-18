"""
OpenAI Image Video Provider — genereert scene-afbeeldingen via OpenAI Images API,
assembleert ze tot een professionele 9:16 TikTok video met FFmpeg.

Pipeline:
1. Per scene: genereer afbeelding via gpt-image-1 (1024x1792, portrait)
2. Optioneel: voiceover via ElevenLabs
3. FFmpeg assembleert: Ken Burns effect (zoom/pan), crossfade transitions,
   tekst overlay met schaduw, ondertiteling, branding
4. Output: 1080x1920 H.264 MP4, 30fps

Kosten: ~$0.02-0.08 per video (afhankelijk van aantal scenes).
"""

import os
import subprocess
import uuid
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).parent.parent.parent
ASSETS_DIR = ROOT / "assets" / "generated"


class OpenAIImageProvider:
    """Maakt TikTok video's door AI-gegenereerde afbeeldingen te combineren met FFmpeg."""

    # Kosten per image (gpt-image-1 1024x1792)
    COST_PER_IMAGE = 0.02

    def __init__(self):
        self.total_cost_usd = 0.0

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        """
        Genereer een volledige video van het script.

        Args:
            script: Dict met scenes, full_voiceover_text, video_type
            memory: Brand memory dict (visual_style, app_name, etc.)
            output_dir: Map voor output video

        Returns:
            Path naar de gegenereerde MP4
        """
        video_id = str(uuid.uuid4())[:8]
        output_path = output_dir / f"ai_video_{video_id}.mp4"
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = script.get("scenes", [])
        if not scenes:
            scenes = [{"voiceover": "Content wordt gegenereerd...", "duration_sec": 5, "type": "hook"}]

        # Stap 1: Genereer afbeeldingen per scene
        image_dir = ASSETS_DIR / "images" / video_id
        image_dir.mkdir(parents=True, exist_ok=True)

        image_paths = []
        for i, scene in enumerate(scenes):
            img_path = self._generate_scene_image(scene, memory, image_dir, i)
            image_paths.append(img_path)

        # Stap 2: Optionele voiceover
        voiceover_text = script.get("full_voiceover_text", "")
        audio_path = self._generate_voiceover(voiceover_text, video_id)

        # Stap 3: Assembleer video
        self._assemble_video(
            image_paths=image_paths,
            scenes=scenes,
            audio_path=audio_path,
            output_path=output_path,
            memory=memory,
        )

        logger.success(f"[OpenAIImage] Video klaar: {output_path} | kosten=${self.total_cost_usd:.3f}")
        return output_path

    # ── Image Generation ─────────────────────────────────────────────

    def _generate_scene_image(
        self, scene: dict, memory: dict, image_dir: Path, index: int
    ) -> Path:
        """Genereer één scene-afbeelding via OpenAI Images API."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("[OpenAIImage] Geen OPENAI_API_KEY — gebruik fallback kleur")
            return self._create_fallback_image(scene, image_dir, index)

        prompt = self._build_image_prompt(scene, memory)
        output_path = image_dir / f"scene_{index:02d}.png"

        try:
            import openai
            client = openai.OpenAI(api_key=api_key)

            logger.info(f"[OpenAIImage] Genereer scene {index + 1}: {prompt[:80]}...")

            # Probeer gpt-image-1, fallback naar dall-e-3
            try:
                response = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt,
                    n=1,
                    size="1024x1792",
                    quality="medium",
                )
            except Exception:
                response = client.images.generate(
                    model="dall-e-3",
                    prompt=prompt,
                    n=1,
                    size="1024x1792",
                    quality="standard",
                )

            # Download en sla op
            image_url = response.data[0].url
            if image_url:
                import httpx
                img_response = httpx.get(image_url, timeout=30, follow_redirects=True)
                img_response.raise_for_status()
                output_path.write_bytes(img_response.content)
            elif hasattr(response.data[0], "b64_json") and response.data[0].b64_json:
                import base64
                img_bytes = base64.b64decode(response.data[0].b64_json)
                output_path.write_bytes(img_bytes)

            self.total_cost_usd += self.COST_PER_IMAGE
            logger.info(f"[OpenAIImage] Scene {index + 1} opgeslagen: {output_path}")
            return output_path

        except Exception as e:
            logger.warning(f"[OpenAIImage] Scene {index + 1} generatie mislukt: {e}")
            return self._create_fallback_image(scene, image_dir, index)

    def _build_image_prompt(self, scene: dict, memory: dict) -> str:
        """Bouw een visuele prompt voor de scene."""
        visual_style = memory.get("visual_style", {}) if memory else {}
        app_name = memory.get("app_name", "") if memory else ""
        niche = memory.get("niche", "") if memory else ""

        scene_type = scene.get("type", "body")
        visual_desc = scene.get("visual_description", "")
        voiceover = scene.get("voiceover", "")
        on_screen = scene.get("on_screen_text", "")

        # Bouw prompt op
        parts = [
            "Professional vertical social media content image, 9:16 aspect ratio.",
            "Clean, modern design suitable for TikTok/Instagram Reels.",
            "NO text in the image, NO watermarks, NO logos.",
        ]

        if visual_desc:
            parts.append(f"Scene: {visual_desc}")
        elif voiceover:
            parts.append(f"Visual representing: {voiceover[:150]}")

        if niche:
            parts.append(f"Industry/niche: {niche}")

        style_desc = visual_style.get("color_scheme", "")
        if style_desc:
            parts.append(f"Style: {style_desc}")
        else:
            parts.append("Style: dark modern gradient, subtle lighting, professional")

        if scene_type == "hook":
            parts.append("Eye-catching, bold, attention-grabbing composition.")
        elif scene_type == "outro":
            parts.append("Warm, inviting, call-to-action feel.")

        return " ".join(parts)

    def _create_fallback_image(self, scene: dict, image_dir: Path, index: int) -> Path:
        """Maak een gradient placeholder als image gen mislukt."""
        output_path = image_dir / f"scene_{index:02d}.png"
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x0a0d14:size=1080x1920:duration=1",
            "-frames:v", "1",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
        return output_path

    # ── Video Assembly ────────────────────────────────────────────────

    def _assemble_video(
        self,
        image_paths: list[Path],
        scenes: list[dict],
        audio_path: Path | None,
        output_path: Path,
        memory: dict,
    ) -> None:
        """Assembleer afbeeldingen tot video met FFmpeg."""
        if not image_paths:
            raise RuntimeError("Geen afbeeldingen om te assembleren")

        visual_style = memory.get("visual_style", {}) if memory else {}
        accent = visual_style.get("accent_color", "#6C63FF")
        app_name = memory.get("app_name", "") if memory else ""

        # Bereken durations
        durations = []
        for scene in scenes:
            durations.append(scene.get("duration_sec", 5))

        total_duration = sum(durations)
        crossfade_dur = 0.5  # Crossfade tussen scenes

        # Bouw FFmpeg command
        cmd = ["ffmpeg", "-y"]

        # Input: elke afbeelding als loop
        for i, img_path in enumerate(image_paths):
            d = durations[i] if i < len(durations) else 5
            cmd += ["-loop", "1", "-t", str(d + crossfade_dur), "-i", str(img_path)]

        # Audio input
        if audio_path and audio_path.exists():
            cmd += ["-i", str(audio_path)]

        # Bouw filter_complex
        filter_parts = []
        n = len(image_paths)

        # Scale + Ken Burns effect per image
        for i in range(n):
            d = durations[i] if i < len(durations) else 5
            total_d = d + crossfade_dur
            frames = int(total_d * 30)

            # Ken Burns variaties: zoom in, pan L→R, pan T→B, zoom out
            effect = i % 4
            if effect == 0:
                # Langzaam inzoomen (1.0 → 1.08)
                zoom_expr = "min(1+0.0008*on,1.08)"
                x_expr = "iw/2-(iw/zoom/2)"
                y_expr = "ih/2-(ih/zoom/2)"
            elif effect == 1:
                # Pan links naar rechts
                zoom_expr = "1.06"
                x_expr = f"(iw-iw/zoom)*on/{frames}"
                y_expr = "(ih-ih/zoom)/2"
            elif effect == 2:
                # Pan boven naar onder
                zoom_expr = "1.06"
                x_expr = "(iw-iw/zoom)/2"
                y_expr = f"(ih-ih/zoom)*on/{frames}"
            else:
                # Langzaam uitzoomen (1.08 → 1.0)
                zoom_expr = f"1.08-0.0008*on"
                x_expr = "iw/2-(iw/zoom/2)"
                y_expr = "ih/2-(ih/zoom/2)"

            filter_parts.append(
                f"[{i}:v]scale=2160x3840:force_original_aspect_ratio=increase,"
                f"crop=2160:3840:(iw-2160)/2:(ih-3840)/2,"
                f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}'"
                f":d={frames}:s=1080x1920:fps=30,"
                f"format=yuv420p[v{i}]"
            )

        # Crossfade transitions tussen scenes
        if n == 1:
            filter_parts.append(f"[v0]trim=duration={durations[0]},setpts=PTS-STARTPTS[vout]")
        else:
            # Chain crossfades
            prev = "v0"
            offset = durations[0] - crossfade_dur
            for i in range(1, n):
                out_label = f"cf{i}" if i < n - 1 else "vout"
                filter_parts.append(
                    f"[{prev}][v{i}]xfade=transition=fade:duration={crossfade_dur}"
                    f":offset={offset:.2f}[{out_label}]"
                )
                prev = out_label
                if i < n - 1:
                    d = durations[i] if i < len(durations) else 5
                    offset += d - crossfade_dur

        # Tekst overlays per scene
        current_time = 0.0
        last_label = "vout"

        for i, scene in enumerate(scenes):
            d = durations[i] if i < len(durations) else 5
            scene_type = scene.get("type", "body")
            main_text = scene.get("on_screen_text") or ""
            subtitle = scene.get("voiceover", "")

            if not main_text and not subtitle:
                current_time += d
                continue

            fade_in = current_time + 0.3
            fade_out = current_time + d - 0.4
            vis_start = current_time
            vis_end = current_time + d

            # Hoofd tekst (midden)
            if main_text:
                safe = _escape_ffmpeg(main_text[:90])
                fontsize = 54 if scene_type == "hook" else 40
                y_pos = 700 if scene_type == "hook" else 780
                new_label = f"mt{i}"

                # Schaduw
                filter_parts.append(
                    f"[{last_label}]drawtext="
                    f"text='{safe}':"
                    f"fontcolor=black@0.5:fontsize={fontsize}:"
                    f"x=(w-text_w)/2+2:y={y_pos}+2:"
                    f"enable='between(t,{vis_start},{vis_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{vis_start})/0.3,"
                    f"if(gt(t,{fade_out}),({vis_end}-t)/0.4,1))'[sh{i}]"
                )
                # Tekst
                filter_parts.append(
                    f"[sh{i}]drawtext="
                    f"text='{safe}':"
                    f"fontcolor=white:fontsize={fontsize}:"
                    f"x=(w-text_w)/2:y={y_pos}:"
                    f"enable='between(t,{vis_start},{vis_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{vis_start})/0.3,"
                    f"if(gt(t,{fade_out}),({vis_end}-t)/0.4,1))'[{new_label}]"
                )
                last_label = new_label

            # Subtitle (onderkant, semi-transparant)
            if subtitle and subtitle != main_text:
                safe_sub = _escape_ffmpeg(subtitle[:120])
                sub_label = f"sub{i}"
                filter_parts.append(
                    f"[{last_label}]drawtext="
                    f"text='{safe_sub}':"
                    f"fontcolor=white@0.85:fontsize=26:"
                    f"x=(w-text_w)/2:y=1420:"
                    f"enable='between(t,{vis_start},{vis_end})':"
                    f"alpha='if(lt(t,{fade_in}),(t-{vis_start})/0.3,"
                    f"if(gt(t,{fade_out}),({vis_end}-t)/0.4,1))'[{sub_label}]"
                )
                last_label = sub_label

            current_time += d

        # App branding
        if app_name:
            safe_name = _escape_ffmpeg(app_name[:30])
            filter_parts.append(
                f"[{last_label}]drawtext="
                f"text='{safe_name}':"
                f"fontcolor=white@0.3:fontsize=22:"
                f"x=40:y=h-60[final]"
            )
            last_label = "final"

        filter_parts.append(f"[{last_label}]null[outv]")

        filter_complex = ";".join(filter_parts)

        # Command samenstellen
        cmd += ["-filter_complex", filter_complex, "-map", "[outv]"]

        audio_idx = len(image_paths)
        if audio_path and audio_path.exists():
            cmd += ["-map", f"{audio_idx}:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]

        cmd += [
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-r", "30",
            "-t", str(total_duration),
            str(output_path),
        ]

        logger.info(f"[OpenAIImage] FFmpeg render: {len(image_paths)} scenes, {total_duration}s")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            stderr_tail = result.stderr[-1000:] if result.stderr else "geen output"
            logger.error(f"[OpenAIImage] FFmpeg fout:\n{stderr_tail}")
            raise RuntimeError(f"FFmpeg mislukt (code {result.returncode})")

    # ── Voiceover ─────────────────────────────────────────────────────

    def _generate_voiceover(self, text: str, video_id: str) -> Path | None:
        """Genereer voiceover via ElevenLabs of OpenAI TTS."""
        # Probeer eerst ElevenLabs
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")
        if elevenlabs_key and text.strip():
            return self._voiceover_elevenlabs(text, video_id, elevenlabs_key)

        # Fallback: OpenAI TTS
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key and text.strip():
            return self._voiceover_openai(text, video_id, openai_key)

        logger.info("[OpenAIImage] Geen TTS beschikbaar — video zonder audio")
        return None

    def _voiceover_openai(self, text: str, video_id: str, api_key: str) -> Path | None:
        """Genereer voiceover via OpenAI TTS (betaald via OpenAI key)."""
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)

            audio_path = ASSETS_DIR / "audio" / f"vo_{video_id}.mp3"
            audio_path.parent.mkdir(parents=True, exist_ok=True)

            logger.info("[OpenAIImage] Genereer voiceover via OpenAI TTS...")
            response = client.audio.speech.create(
                model="tts-1",
                voice="onyx",  # Diepe, professionele stem
                input=text[:4096],
                response_format="mp3",
            )

            response.stream_to_file(str(audio_path))
            self.total_cost_usd += len(text) / 1000 * 0.015  # $0.015 per 1K chars
            logger.info(f"[OpenAIImage] OpenAI TTS klaar: {audio_path}")
            return audio_path

        except Exception as e:
            logger.warning(f"[OpenAIImage] OpenAI TTS mislukt: {e}")
            return None

    def _voiceover_elevenlabs(self, text: str, video_id: str, api_key: str) -> Path | None:
        """Genereer voiceover via ElevenLabs."""
        try:
            import httpx
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            audio_path = ASSETS_DIR / "audio" / f"vo_{video_id}.mp3"
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
            logger.info(f"[OpenAIImage] ElevenLabs voiceover klaar: {audio_path}")
            return audio_path

        except Exception as e:
            logger.warning(f"[OpenAIImage] ElevenLabs mislukt: {e}")
            return None


def _escape_ffmpeg(text: str) -> str:
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
        .replace("\n", " ")
    )
