"""
Kling AI Provider — primaire betaalde video-generator.
Documentatie: https://klingai.com/api
"""

import os
import time
import uuid
from pathlib import Path

import httpx
from loguru import logger


class KlingProvider:
    """Genereert AI-video's via Kling AI API."""

    BASE_URL = "https://api.klingai.com/v1"
    COST_PER_SECOND = 0.014  # Geschatte kosten per gegenereerde seconde

    def __init__(self):
        self.api_key = os.getenv("KLING_API_KEY")
        self.total_cost_usd = 0.0

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        if not self.api_key:
            raise ValueError("KLING_API_KEY niet ingesteld in .env")

        video_id = str(uuid.uuid4())[:8]
        output_path = output_dir / f"kling_{video_id}.mp4"

        # Bouw visual prompt op basis van script
        visual_prompt = self._build_visual_prompt(script, memory)
        duration = min(script.get("total_duration_sec", 30), 30)  # Kling max 30s per clip

        logger.info(f"[Kling] Genereer video ({duration}s)...")

        # Initieer generatie
        task_id = self._create_task(visual_prompt, duration)

        # Wacht op voltooiing (polling)
        video_url = self._poll_task(task_id)

        # Download video
        self._download_video(video_url, output_path)

        self.total_cost_usd += duration * self.COST_PER_SECOND
        logger.success(f"[Kling] Video gedownload: {output_path} | kosten=${self.total_cost_usd:.3f}")
        return output_path

    def _build_visual_prompt(self, script: dict, memory: dict) -> str:
        visual_style = memory.get("visual_style", {})
        style_desc = visual_style.get("color_scheme", "dark background, white text, modern")

        scenes = script.get("scenes", [])
        scene_descriptions = []
        for scene in scenes[:3]:  # Eerste 3 scenes voor clip
            desc = scene.get("visual_description", "")
            if desc:
                scene_descriptions.append(desc)

        prompt = ". ".join(scene_descriptions) if scene_descriptions else "modern app demo, clean UI"
        return f"{prompt}. Style: {style_desc}. Vertical video 9:16, 1080x1920."

    def _create_task(self, prompt: str, duration: int) -> str:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.BASE_URL}/videos/text2video",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "prompt": prompt,
                    "duration": duration,
                    "aspect_ratio": "9:16",
                    "mode": "std",
                },
            )
            response.raise_for_status()
            data = response.json()
            task_id = data["data"]["task_id"]
            logger.info(f"[Kling] Taak aangemaakt: {task_id}")
            return task_id

    def _poll_task(self, task_id: str, max_wait_sec: int = 300) -> str:
        start = time.time()
        with httpx.Client(timeout=30) as client:
            while time.time() - start < max_wait_sec:
                response = client.get(
                    f"{self.BASE_URL}/videos/text2video/{task_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()["data"]
                status = data.get("task_status")

                if status == "succeed":
                    return data["task_result"]["videos"][0]["url"]
                elif status == "failed":
                    raise RuntimeError(f"Kling taak mislukt: {data.get('task_status_msg')}")

                logger.info(f"[Kling] Status: {status}, wacht 10s...")
                time.sleep(10)

        raise TimeoutError(f"Kling taak {task_id} niet klaar binnen {max_wait_sec}s")

    def _download_video(self, url: str, output_path: Path) -> None:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
