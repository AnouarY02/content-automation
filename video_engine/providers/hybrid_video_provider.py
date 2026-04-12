"""
Hybrid Video Provider — VisionStory talking head + Pexels B-roll cutaways.

Pipeline:
  1. Genereer volledige talking head via VisionStory + ElevenLabs (audio + lipsync)
  2. Fetch Pexels B-roll clips voor problem/solution scenes
  3. FFmpeg assembly:
     - Scene 1 (hook):     talking head video + audio
     - Scene 2 (problem):  Pexels B-roll video + talking head audio
     - Scene 3 (solution): Pexels B-roll video + talking head audio
     - Scene 4 (CTA):      talking head video + audio
  4. Post-processing: captions + muziek

Resultaat: talking head authenticity + visual variety van B-roll = maximale engagement.
"""

import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
from loguru import logger

from video_engine.providers.visionstory_provider import VisionStoryProvider, _get_duration, _OUT_W, _OUT_H

ROOT = Path(__file__).parent.parent.parent
MUSIC_DIR = ROOT / "assets" / "music"

_PEXELS_KEY = lambda: os.getenv("PEXELS_API_KEY", "")


class HybridVideoProvider:
    """
    Combineert VisionStory talking head met Pexels B-roll cutaways.

    Talking head (hook + CTA) geven authenticiteit.
    B-roll (problem + solution) geven visuele variatie en bewijs.
    Audio is altijd Nadia's stem door het hele video.
    """

    COST_PER_VIDEO_USD = 0.08  # Zelfde als VisionStory (B-roll is gratis via Pexels)

    def __init__(self):
        self._vs = VisionStoryProvider()
        self.total_cost_usd = 0.0

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        if not self._vs.api_key:
            raise ValueError("Geen VisionStory API key voor Hybrid provider")

        video_id = str(uuid.uuid4())[:8]
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = output_dir / f"hybrid_work_{video_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        th_raw = work_dir / "th_raw.mp4"
        th_upscaled = work_dir / "th_upscaled.mp4"
        final_path = output_dir / f"hybrid_{video_id}.mp4"

        try:
            # Stap 1: Genereer volledige talking head video
            logger.info("[Hybrid] Stap 1: Genereer talking head via VisionStory...")
            voiceover = self._vs._extract_voiceover(script)
            vs_id = self._vs._create_video(voiceover, memory)
            video_url = self._vs._poll_video(vs_id)
            self._vs._download_video(video_url, th_raw)
            self._vs._upscale_to_portrait(th_raw, th_upscaled)
            th_raw.unlink(missing_ok=True)

            # Stap 2: Bereken scene-tijden uit script
            scenes = script.get("scenes", [])
            scene_times = self._calc_scene_times(scenes, _get_duration(th_upscaled))
            logger.info(f"[Hybrid] Scene tijden: {scene_times}")

            # Stap 3: Fetch Pexels B-roll voor problem + solution scenes
            broll_clips = self._fetch_broll_clips(scenes, memory, work_dir)

            # Stap 4: Assembly — combineer TH en B-roll per scene
            assembled = work_dir / "assembled.mp4"
            self._assemble_scenes(th_upscaled, scene_times, broll_clips, assembled, work_dir)

            # Stap 5: Captions + muziek
            captioned = work_dir / "captioned.mp4"
            caption_dur = _get_duration(assembled)
            filters = self._vs._build_caption_filters(voiceover, caption_dur)
            if filters:
                self._vs._apply_caption_filters(assembled, captioned, filters)
            else:
                shutil.copy(str(assembled), str(captioned))

            self._vs._add_background_music(captioned, final_path, script)
            if not final_path.exists() or final_path.stat().st_size < 10000:
                shutil.copy(str(captioned), str(final_path))

            size_kb = final_path.stat().st_size // 1024
            logger.info(f"[Hybrid] Post-processing klaar: {size_kb} KB")

            self.total_cost_usd += self.COST_PER_VIDEO_USD
            self._vs.total_cost_usd = self.total_cost_usd
            logger.success(f"[Hybrid] Video klaar: {final_path} | kosten=${self.total_cost_usd:.3f}")
            return final_path

        except Exception as e:
            logger.error(f"[Hybrid] Productie mislukt: {e}")
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ── Scene timing ──────────────────────────────────────────────────

    def _calc_scene_times(self, scenes: list[dict], total_dur: float) -> list[tuple[float, float]]:
        """Bereken (start, einde) tijden per scene, gebaseerd op script durations."""
        if not scenes:
            # Gelijkmatige verdeling als geen scenes
            q = total_dur / 4
            return [(i * q, (i + 1) * q) for i in range(4)]

        durations = [max(1.0, float(s.get("duration_sec", 10))) for s in scenes]
        total_script = sum(durations)
        # Schaal naar werkelijke videoduur
        scale = total_dur / total_script if total_script > 0 else 1.0

        times = []
        t = 0.0
        for d in durations:
            end = min(t + d * scale, total_dur)
            times.append((t, end))
            t = end
        return times

    # ── B-roll fetching ───────────────────────────────────────────────

    def _fetch_broll_clips(self, scenes: list[dict], memory: dict, work_dir: Path) -> dict[int, Path | None]:
        """
        Fetch Pexels B-roll voor scenes met index 1 en 2 (problem + solution).
        Returns: {scene_index: clip_path | None}
        """
        clips = {}
        api_key = _PEXELS_KEY()
        if not api_key or len(api_key) < 10:
            logger.warning("[Hybrid] Geen Pexels API key — gebruik talking head voor alle scenes")
            return clips

        for idx, scene in enumerate(scenes):
            # Alleen problem (1) en solution (2) scenes krijgen B-roll
            if idx not in (1, 2):
                continue
            query = self._extract_broll_query(scene, memory)
            if not query:
                continue
            clip = self._download_pexels_clip(query, idx, work_dir, api_key)
            clips[idx] = clip
            logger.info(f"[Hybrid] B-roll scene {idx}: {'klaar' if clip else 'niet gevonden'} ('{query}')")

        return clips

    def _extract_broll_query(self, scene: dict, memory: dict) -> str:
        """Haal visual_search_query op uit scene, vertaal naar Engelse Pexels query."""
        query = scene.get("visual_search_query", "") or scene.get("visual_description", "")
        if not query:
            return ""
        # Gebruik de eerste 4 woorden van de query
        words = query.strip().split()[:4]
        return " ".join(words)

    def _download_pexels_clip(
        self, query: str, idx: int, work_dir: Path, api_key: str
    ) -> Path | None:
        """Download één Pexels video clip voor een scene."""
        try:
            resp = httpx.get(
                "https://api.pexels.com/videos/search",
                params={"query": query, "per_page": 10, "orientation": "portrait", "size": "medium"},
                headers={"Authorization": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])
            if not videos:
                return None

            # Kies een video met minimale duur van 5s
            video = next(
                (v for v in videos if v.get("duration", 0) >= 5),
                videos[0] if videos else None,
            )
            if not video:
                return None

            # Kies beste bestandsformaat (portrait, ~HD)
            files = sorted(
                [f for f in video.get("video_files", []) if f.get("width", 0) <= 1080],
                key=lambda f: f.get("width", 0),
                reverse=True,
            )
            if not files:
                return None
            best = files[0]

            raw_path = work_dir / f"broll_raw_{idx:02d}.mp4"
            dl = httpx.get(best["link"], timeout=60, follow_redirects=True)
            dl.raise_for_status()
            raw_path.write_bytes(dl.content)

            # Trim en schaal naar 1080×1920 portrait
            clip_path = work_dir / f"broll_{idx:02d}.mp4"
            self._prep_broll_clip(raw_path, clip_path)
            raw_path.unlink(missing_ok=True)
            return clip_path if clip_path.exists() and clip_path.stat().st_size > 10000 else None

        except Exception as e:
            logger.warning(f"[Hybrid] Pexels clip scene {idx} mislukt: {e}")
            return None

    def _prep_broll_clip(self, input_path: Path, output_path: Path) -> None:
        """Schaal en trim B-roll clip naar 1080×1920 portrait."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", (
                f"scale={_OUT_W}:{_OUT_H}:force_original_aspect_ratio=increase,"
                f"crop={_OUT_W}:{_OUT_H},"
                "setsar=1,format=yuv420p"
            ),
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-an",  # Geen audio — gebruiken TH audio
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"B-roll prep mislukt: {result.stderr[-200:]}")

    # ── Scene assembly ────────────────────────────────────────────────

    def _assemble_scenes(
        self,
        th_video: Path,
        scene_times: list[tuple[float, float]],
        broll_clips: dict[int, Path | None],
        output_path: Path,
        work_dir: Path,
    ) -> None:
        """
        Combineer talking head en B-roll per scene en concateneer.

        Voor elke scene:
        - Als er B-roll beschikbaar is (scene 1, 2): gebruik B-roll video + TH audio
        - Anders: gebruik TH video + TH audio
        """
        segment_paths = []

        for i, (start, end) in enumerate(scene_times):
            dur = end - start
            if dur < 0.5:
                continue

            seg_path = work_dir / f"seg_{i:02d}.mp4"
            broll = broll_clips.get(i)

            if broll and broll.exists():
                # B-roll video + TH audio segment
                self._cut_broll_with_th_audio(broll, th_video, start, dur, seg_path)
                logger.info(f"[Hybrid] Scene {i}: B-roll ({dur:.1f}s)")
            else:
                # Pure talking head segment
                self._cut_th_segment(th_video, start, dur, seg_path)
                logger.info(f"[Hybrid] Scene {i}: Talking head ({dur:.1f}s)")

            if seg_path.exists() and seg_path.stat().st_size > 1000:
                segment_paths.append(seg_path)

        if not segment_paths:
            raise RuntimeError("[Hybrid] Geen segmenten gegenereerd")

        self._concat_segments(segment_paths, output_path, work_dir)

    def _cut_th_segment(self, th_video: Path, start: float, duration: float, output: Path) -> None:
        """Knip een segment uit de talking head video."""
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(duration),
            "-i", str(th_video),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"TH segment mislukt: {result.stderr[-200:]}")

    def _cut_broll_with_th_audio(
        self, broll: Path, th_video: Path, th_start: float, duration: float, output: Path
    ) -> None:
        """
        B-roll video met audio uit talking head.
        B-roll wordt getrimd tot de scene-duur.
        TH audio wordt uit het juiste tijdstip genomen.
        """
        broll_dur = _get_duration(broll)
        # Loop B-roll als hij korter is dan de scene
        loop_flag = ["-stream_loop", "-1"] if broll_dur < duration else []

        cmd = [
            "ffmpeg", "-y",
            *loop_flag,
            "-i", str(broll),
            "-ss", str(th_start), "-t", str(duration),
            "-i", str(th_video),
            "-filter_complex",
            f"[0:v]trim=0:{duration},setpts=PTS-STARTPTS,setsar=1[bv];"
            f"[1:a]asetpts=PTS-STARTPTS[ba]",
            "-map", "[bv]", "-map", "[ba]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(duration),
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning(f"[Hybrid] B-roll+TH audio mislukt, fallback naar TH: {result.stderr[-200:]}")
            self._cut_th_segment(th_video, th_start, duration, output)

    def _concat_segments(self, segments: list[Path], output: Path, work_dir: Path) -> None:
        """Concateneer alle segmenten naar één video."""
        concat_list = work_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.resolve()}'" for p in segments)
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(output),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Concat mislukt: {result.stderr[-300:]}")
        logger.info(f"[Hybrid] Concat klaar: {len(segments)} segmenten → {output.name}")
