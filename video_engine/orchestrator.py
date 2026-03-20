"""
Video Engine Orchestrator.
Kiest de juiste video-provider op basis van video-type en beschikbare API keys.

Provider prioriteit:
1. D-ID provider (talking head UGC) — als DID_API_KEY + video_type == talking_head
2. Pro Video provider (stock footage + TTS voiceover) — als OPENAI_API_KEY
3. OpenAI Image provider (AI-gegenereerde beelden + FFmpeg)
4. FFmpeg provider (gradient + tekst, gratis fallback)
"""

import os
from pathlib import Path
from loguru import logger
from utils.runtime_paths import ensure_dir, get_generated_assets_dir

ROOT = Path(__file__).parent.parent
ASSETS_DIR = ensure_dir(get_generated_assets_dir())


class VideoOrchestrator:
    """
    Beheert video-productie:
    - Pro provider: stock footage + website capture + TTS (standaard)
    - OpenAI Image: AI-beelden + FFmpeg assembly
    - FFmpeg: gratis gradient+tekst fallback
    """

    def __init__(self, voice: str = "nova", tts_speed: float = 1.0, voice_settings: dict | None = None, on_progress=None):
        self.total_cost_usd = 0.0
        self.voice = voice
        self.tts_speed = tts_speed
        self.voice_settings = voice_settings  # stability, similarity_boost, style
        self.on_progress = on_progress
        self.last_error = ""

    def produce(self, script: dict, memory: dict, app_id: str) -> Path | None:
        """
        Produceer een video op basis van het script.

        Returns:
            Pad naar de gegenereerde video, of None bij falen
        """
        video_type = script.get("video_type", "text_on_screen")

        # Output pad
        output_dir = ASSETS_DIR / "videos"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Provider selectie: pro → openai_image → ffmpeg
        provider_name = self._select_provider(video_type)
        logger.info(f"[VideoEngine] Produceer '{video_type}' via '{provider_name}'")

        try:
            if provider_name == "did":
                from video_engine.providers.d_id_provider import DIDProvider
                provider = DIDProvider()
                video_path = provider.produce(script, memory, output_dir)
                self.total_cost_usd += provider.total_cost_usd

            elif provider_name == "pro":
                from video_engine.providers.pro_video_provider import ProVideoProvider
                provider = ProVideoProvider(
                    voice=self.voice, tts_speed=self.tts_speed,
                    voice_settings=self.voice_settings,
                )
                video_path = provider.produce(script, memory, output_dir, on_progress=self.on_progress)
                self.total_cost_usd += provider.total_cost_usd

            elif provider_name == "openai_image":
                from video_engine.providers.openai_image_provider import OpenAIImageProvider
                provider = OpenAIImageProvider()
                video_path = provider.produce(script, memory, output_dir)
                self.total_cost_usd += provider.total_cost_usd

            elif provider_name == "ffmpeg":
                from video_engine.providers.ffmpeg_provider import FFmpegProvider
                provider = FFmpegProvider()
                video_path = provider.produce(script, memory, output_dir)

            else:
                from video_engine.providers.ffmpeg_provider import FFmpegProvider
                provider = FFmpegProvider()
                video_path = provider.produce(script, memory, output_dir)

            logger.success(f"[VideoEngine] Video klaar: {video_path}")
            return video_path

        except Exception as e:
            self.last_error = f"{provider_name}: {e}"
            logger.error(f"[VideoEngine] Productie mislukt ({provider_name}): {e}")

            # Fallback keten: pro → openai_image → ffmpeg
            fallbacks = self._get_fallbacks(provider_name)
            for fb_name in fallbacks:
                logger.info(f"[VideoEngine] Probeer fallback: {fb_name}")
                try:
                    if fb_name == "pro":
                        from video_engine.providers.pro_video_provider import ProVideoProvider
                        fb = ProVideoProvider(
                            voice=self.voice, tts_speed=self.tts_speed,
                            voice_settings=self.voice_settings,
                        )
                        path = fb.produce(script, memory, output_dir)
                        self.total_cost_usd += fb.total_cost_usd
                        return path
                    elif fb_name == "openai_image":
                        from video_engine.providers.openai_image_provider import OpenAIImageProvider
                        fb = OpenAIImageProvider()
                        path = fb.produce(script, memory, output_dir)
                        self.total_cost_usd += fb.total_cost_usd
                        return path
                    elif fb_name == "ffmpeg":
                        from video_engine.providers.ffmpeg_provider import FFmpegProvider
                        fb = FFmpegProvider()
                        return fb.produce(script, memory, output_dir)
                except Exception as e2:
                    self.last_error = f"{fb_name}: {e2}"
                    logger.error(f"[VideoEngine] Fallback {fb_name} mislukt: {e2}")

            return None

    def _select_provider(self, video_type: str) -> str:
        """Selecteer de beste beschikbare provider."""
        has_openai = bool(os.getenv("OPENAI_API_KEY"))
        has_did = bool(os.getenv("DID_API_KEY"))
        env = os.getenv("ENVIRONMENT", "development").lower()
        fast_video_mode = os.getenv("FAST_VIDEO_MODE", "")

        # In productie is snelle, voorspelbare generatie belangrijker dan de zwaarste video-stack.
        # Daarom gebruiken we standaard de lichte FFmpeg provider, tenzij expliciet uitgeschakeld.
        if fast_video_mode:
            use_fast_video = fast_video_mode.lower() == "true"
        else:
            use_fast_video = env == "production"

        if use_fast_video:
            return "ffmpeg"

        # D-ID: prioreteit voor talking_head, MAAR sla over als DID_SKIP=true
        # (gebruik DID_SKIP=true als de D-ID API een 400 geeft door format-mismatch)
        did_skip = os.getenv("DID_SKIP", "false").lower() == "true"
        if has_did and video_type == "talking_head" and not did_skip:
            return "did"

        # Pro provider: stock footage + TTS (standaard)
        if has_openai:
            return "pro"

        return "ffmpeg"

    def _get_fallbacks(self, failed_provider: str) -> list[str]:
        """Geef fallback providers na een gefaalde provider."""
        has_openai = bool(os.getenv("OPENAI_API_KEY"))

        if failed_provider == "did":
            return (["pro", "ffmpeg"] if has_openai else ["ffmpeg"])
        elif failed_provider == "pro":
            return (["openai_image", "ffmpeg"] if has_openai else ["ffmpeg"])
        elif failed_provider == "openai_image":
            return ["ffmpeg"]
        return []
