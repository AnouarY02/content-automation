"""
Video Engine Orchestrator.
Kiest de juiste video-provider op basis van video-type en beschikbare API keys.

Provider prioriteit:
1. VisionStory provider (talking head) — als DID_API_KEY=sk-vs-... + video_type == talking_head
2. D-ID provider (talking head UGC) — als DID_API_KEY (niet sk-vs-) + video_type == talking_head
3. Pro Video provider (stock footage + TTS voiceover) — als OPENAI_API_KEY
4. OpenAI Image provider (AI-gegenereerde beelden + FFmpeg)
5. FFmpeg provider (gradient + tekst, gratis fallback)
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
            if provider_name == "visionstory":
                from video_engine.providers.visionstory_provider import VisionStoryProvider
                provider = VisionStoryProvider()
                video_path = provider.produce(script, memory, output_dir)
                self.total_cost_usd += provider.total_cost_usd

            elif provider_name == "did":
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

            # Fallback keten: visionstory/did → pro → openai_image → ffmpeg
            fallbacks = self._get_fallbacks(provider_name)
            for fb_name in fallbacks:
                logger.info(f"[VideoEngine] Probeer fallback: {fb_name}")
                try:
                    if fb_name == "visionstory":
                        from video_engine.providers.visionstory_provider import VisionStoryProvider
                        fb = VisionStoryProvider()
                        path = fb.produce(script, memory, output_dir)
                        self.total_cost_usd += fb.total_cost_usd
                        return path
                    elif fb_name == "did":
                        from video_engine.providers.d_id_provider import DIDProvider
                        fb = DIDProvider()
                        path = fb.produce(script, memory, output_dir)
                        self.total_cost_usd += fb.total_cost_usd
                        return path
                    elif fb_name == "pro":
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

    @staticmethod
    def _is_low_memory_env() -> bool:
        """Detecteer Railway of andere low-memory omgevingen (512MB RAM)."""
        return bool(
            os.getenv("RAILWAY_ENVIRONMENT")
            or os.getenv("RAILWAY_PROJECT_ID")
            or os.getenv("RAILWAY_SERVICE_ID")
        )

    @staticmethod
    def _allow_degraded_video() -> bool:
        raw = os.getenv("ALLOW_DEGRADED_VIDEO", "").strip().lower()
        if raw:
            return raw == "true"
        return os.getenv("ENVIRONMENT", "development").lower() != "production"

    @staticmethod
    def _has_openai() -> bool:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        return len(key) >= 10

    @staticmethod
    def _has_pexels() -> bool:
        key = os.getenv("PEXELS_API_KEY", "").strip()
        return len(key) >= 10 and not key.startswith("...")

    @staticmethod
    def _has_rich_video_stack() -> bool:
        keys = (
            os.getenv("OPENAI_API_KEY", ""),
            os.getenv("PEXELS_API_KEY", ""),
            os.getenv("PIXABAY_API_KEY", ""),
            os.getenv("ELEVENLABS_API_KEY", ""),
            os.getenv("DID_API_KEY", ""),
            os.getenv("AZURE_TTS_KEY", ""),
        )
        return any(value and len(value.strip()) >= 10 for value in keys)

    @staticmethod
    def _has_visionstory() -> bool:
        """VisionStory: DID_API_KEY dat begint met sk-vs-, of aparte VISIONSTORY_API_KEY."""
        vs_key = os.getenv("VISIONSTORY_API_KEY", "").strip()
        if vs_key and vs_key.startswith("sk-vs-"):
            return True
        did_key = os.getenv("DID_API_KEY", "").strip()
        return did_key.startswith("sk-vs-")

    @staticmethod
    def _has_did() -> bool:
        """Echte D-ID key (base64-encoded, niet sk-vs-)."""
        key = os.getenv("DID_API_KEY", "").strip()
        return bool(key) and not key.startswith("sk-vs-")

    def _select_provider(self, video_type: str) -> str:
        """Selecteer de beste beschikbare provider.

        Prioriteit:
        1. VisionStory: talking_head + sk-vs- key → moderne AI avatar
        2. D-ID: talking_head + echte D-ID key → UGC avatar
        3. Pro (Pexels): stock footage — authentieker dan AI-beelden
        4. OpenAI Image: fallback zonder Pexels key
        5. FFmpeg: gratis gradient+tekst fallback
        """
        if os.getenv("FAST_VIDEO_MODE", "").lower() == "true":
            return "ffmpeg"

        did_skip = os.getenv("DID_SKIP", "false").lower() == "true"

        if video_type == "talking_head" and not did_skip:
            # 1. VisionStory (sk-vs- key)
            if self._has_visionstory():
                return "visionstory"
            # 2. D-ID (echte D-ID key)
            if self._has_did():
                return "did"

        # 3. Pro (Pexels stock footage)
        if self._has_pexels():
            return "pro"

        # 4. OpenAI Image als fallback
        if self._has_openai():
            return "openai_image"

        return "ffmpeg"

    def _get_fallbacks(self, failed_provider: str) -> list[str]:
        """Geef fallback providers na een gefaalde provider."""
        allow_degraded = self._allow_degraded_video()
        low_mem = self._is_low_memory_env()

        if not allow_degraded:
            # Productie: alleen waardige fallbacks, geen gradient-slideshow
            if failed_provider in ("visionstory", "did"):
                if self._has_openai():
                    return ["openai_image"]
                if self._has_pexels() and not low_mem:
                    return ["pro"]
                return []
            if failed_provider == "openai_image":
                if low_mem:
                    return ["ffmpeg"]
                return ["pro"] if self._has_pexels() else []
            return []

        # Development: volledige fallback keten
        if failed_provider == "visionstory":
            chain = []
            if self._has_did():
                chain.append("did")
            if self._has_openai():
                chain.append("openai_image")
            if self._has_pexels() and not low_mem:
                chain.append("pro")
            chain.append("ffmpeg")
            return chain
        elif failed_provider == "did":
            chain = []
            if self._has_openai():
                chain.append("openai_image")
            if self._has_pexels() and not low_mem:
                chain.append("pro")
            chain.append("ffmpeg")
            return chain
        elif failed_provider == "openai_image":
            if low_mem:
                return ["ffmpeg"]
            return (["pro", "ffmpeg"] if self._has_pexels() else ["ffmpeg"])
        elif failed_provider == "pro":
            return ["ffmpeg"]
        return []
