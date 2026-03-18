"""
Runway ML Provider — premium video-generator voor cinematic shots.
Documentatie: https://docs.runwayml.com
"""

import os
import time
import uuid
from pathlib import Path

import httpx
from loguru import logger


class RunwayProvider:
    """Genereert premium AI-video's via Runway Gen-3 API."""

    BASE_URL = "https://api.runwayml.com/v1"
    COST_PER_SECOND = 0.05

    def __init__(self):
        self.api_key = os.getenv("RUNWAY_API_KEY")
        self.total_cost_usd = 0.0

    def produce(self, script: dict, memory: dict, output_dir: Path) -> Path:
        if not self.api_key:
            raise ValueError("RUNWAY_API_KEY niet ingesteld in .env")

        video_id = str(uuid.uuid4())[:8]
        output_path = output_dir / f"runway_{video_id}.mp4"

        scenes = script.get("scenes", [])
        prompt = " ".join(
            s.get("visual_description", "") for s in scenes[:2] if s.get("visual_description")
        )
        if not prompt:
            prompt = "modern app interface, clean design, professional"

        duration = 10  # Runway Gen-3: 5 of 10 seconden per clip
        logger.info(f"[Runway] Genereer clip ({duration}s)...")

        task_id = self._create_generation(prompt, duration)
        video_url = self._poll_generation(task_id)
        self._download_video(video_url, output_path)

        self.total_cost_usd += duration * self.COST_PER_SECOND
        logger.success(f"[Runway] Video gedownload: {output_path} | kosten=${self.total_cost_usd:.2f}")
        return output_path

    def _create_generation(self, prompt: str, duration: int) -> str:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.BASE_URL}/image_to_video",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Runway-Version": "2024-11-06",
                },
                json={
                    "promptText": prompt,
                    "duration": duration,
                    "ratio": "720:1280",
                    "model": "gen3a_turbo",
                },
            )
            response.raise_for_status()
            return response.json()["id"]

    def _poll_generation(self, task_id: str, max_wait_sec: int = 180) -> str:
        start = time.time()
        with httpx.Client(timeout=30) as client:
            while time.time() - start < max_wait_sec:
                response = client.get(
                    f"{self.BASE_URL}/tasks/{task_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                data = response.json()
                status = data.get("status")

                if status == "SUCCEEDED":
                    return data["output"][0]
                elif status in ("FAILED", "CANCELLED"):
                    raise RuntimeError(f"Runway generatie mislukt: {data.get('failure')}")

                time.sleep(5)

        raise TimeoutError(f"Runway taak {task_id} niet klaar binnen {max_wait_sec}s")

    def _download_video(self, url: str, output_path: Path) -> None:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            output_path.write_bytes(response.content)
