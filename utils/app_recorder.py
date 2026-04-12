"""
App Recorder — maakt screenshots en screen recordings van de GLP Coach app via Playwright.

Gebruikt voor:
- Foto posts: branded app screenshot + tekst overlay
- Video pipeline: B-roll scene met live app
- Bewijs van resultaten / UI showcase

Vereist: PLAYWRIGHT_APP_URL env var (bijv. https://glpcoach.app)
"""

import os
import uuid
from pathlib import Path
from loguru import logger

ROOT = Path(__file__).parent.parent
SCREENSHOTS_DIR = ROOT / "assets" / "generated" / "app_screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

APP_URL = os.getenv("PLAYWRIGHT_APP_URL", "").strip()


def _get_url() -> str:
    url = APP_URL or os.getenv("PLAYWRIGHT_APP_URL", "").strip()
    if not url:
        raise ValueError("PLAYWRIGHT_APP_URL niet ingesteld — voeg toe aan .env")
    return url


def screenshot_app(
    path_suffix: str | None = None,
    viewport_w: int = 390,
    viewport_h: int = 844,
    wait_ms: int = 2000,
    selector: str | None = None,
    full_page: bool = False,
) -> Path:
    """
    Maak een screenshot van de GLP Coach app.

    Args:
        path_suffix: Optioneel achtervoegsel voor de bestandsnaam
        viewport_w/h: Mobiel viewport (iPhone 14 Pro = 393×852)
        wait_ms: Wachttijd na laden voor animaties
        selector: CSS selector om specifiek element te screenshotten
        full_page: Screenshot van volledige pagina

    Returns:
        Pad naar het screenshot PNG bestand
    """
    from playwright.sync_api import sync_playwright

    url = _get_url()
    suffix = path_suffix or str(uuid.uuid4())[:8]
    output_path = SCREENSHOTS_DIR / f"app_{suffix}.png"

    logger.info(f"[AppRecorder] Screenshot van {url} → {output_path.name}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": viewport_w, "height": viewport_h},
            device_scale_factor=2,  # Retina kwaliteit
            is_mobile=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(wait_ms)

        if selector:
            element = page.query_selector(selector)
            if element:
                element.screenshot(path=str(output_path))
            else:
                logger.warning(f"[AppRecorder] Selector '{selector}' niet gevonden, volledige pagina")
                page.screenshot(path=str(output_path), full_page=full_page)
        else:
            page.screenshot(path=str(output_path), full_page=full_page)

        browser.close()

    logger.success(f"[AppRecorder] Screenshot klaar: {output_path} ({output_path.stat().st_size // 1024}KB)")
    return output_path


def record_app_video(
    duration_sec: int = 5,
    path_suffix: str | None = None,
    viewport_w: int = 390,
    viewport_h: int = 844,
    scroll: bool = True,
) -> Path:
    """
    Maak een korte screen recording van de GLP Coach app.

    Args:
        duration_sec: Duur van de recording in seconden
        scroll: Of de pagina naar beneden scrolt tijdens de recording

    Returns:
        Pad naar het MP4 bestand
    """
    from playwright.sync_api import sync_playwright
    import tempfile, shutil, subprocess

    url = _get_url()
    suffix = path_suffix or str(uuid.uuid4())[:8]
    output_path = SCREENSHOTS_DIR / f"app_rec_{suffix}.mp4"

    logger.info(f"[AppRecorder] Recording {duration_sec}s van {url}")

    # Playwright video recording
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": viewport_w, "height": viewport_h},
            device_scale_factor=2,
            is_mobile=True,
            record_video_dir=str(SCREENSHOTS_DIR),
            record_video_size={"width": viewport_w, "height": viewport_h},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(1500)

        if scroll:
            # Zachte scroll animatie voor vloeiende recording
            steps = max(1, duration_sec * 3)
            step_px = viewport_h // steps
            for _ in range(steps):
                page.mouse.wheel(0, step_px)
                page.wait_for_timeout(333)
        else:
            page.wait_for_timeout(duration_sec * 1000)

        # Video pad ophalen vóór sluiten
        video = page.video
        ctx.close()
        browser.close()

        raw_video = Path(video.path())

    # Upscale + portret crop naar 1080×1920 via FFmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(raw_video),
            "-vf", f"scale=1080:{int(1080 * viewport_h / viewport_w)},crop=1080:1920",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-an",  # Geen audio
            str(output_path),
        ], check=True, capture_output=True)
        raw_video.unlink(missing_ok=True)
    except subprocess.CalledProcessError as e:
        # Fallback: gebruik raw video
        shutil.move(str(raw_video), str(output_path))
        logger.warning(f"[AppRecorder] FFmpeg upscale mislukt, gebruik raw: {e.stderr.decode()[:200]}")

    logger.success(f"[AppRecorder] Recording klaar: {output_path} ({output_path.stat().st_size // 1024}KB)")
    return output_path


def build_app_photo_post(
    hook_text: str,
    output_path: Path,
    selector: str | None = None,
) -> Path:
    """
    Maak een branded foto post: app screenshot + hook tekst overlay.
    Gebruikt in de foto post pipeline als PLAYWRIGHT_APP_URL beschikbaar is.
    """
    import textwrap
    from PIL import Image, ImageDraw, ImageFont

    output_path = Path(output_path)

    # Screenshot maken
    shot_path = screenshot_app(
        path_suffix=f"post_{output_path.stem}",
        selector=selector,
    )

    # Laad screenshot
    img = Image.open(shot_path).convert("RGBA")

    # Resize naar 1080×1080 (square voor Instagram/Facebook)
    size = (1080, 1080)
    # Crop center van de app screenshot
    w, h = img.size
    if w != h:
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = max(0, (h - min_dim) // 3)  # Iets meer naar boven voor UI
        img = img.crop((left, top, left + min_dim, top + min_dim))
    img = img.resize(size, Image.LANCZOS)

    # Donkere gradient overlay onderaan
    from PIL import ImageFilter
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(size[1]):
        alpha = int(200 * max(0, (y / size[1] - 0.4) / 0.6))
        draw_ov.line([(0, y), (size[0], y)], fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, overlay)

    # Tekst overlay
    draw = ImageDraw.Draw(img)
    font_size = 68
    try:
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    except Exception:
        font_bold = ImageFont.load_default()
        font_small = font_bold

    wrapped = textwrap.wrap(hook_text[:120], width=20)
    text_y = size[1] - 60 - (len(wrapped) * (font_size + 14))

    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=font_bold)
        w_text = bbox[2] - bbox[0]
        x = (size[0] - w_text) // 2
        draw.text((x + 3, text_y + 3), line, font=font_bold, fill=(0, 0, 0, 160))
        draw.text((x, text_y), line, font=font_bold, fill=(255, 255, 255, 255))
        text_y += font_size + 14

    # GLP Coach branding
    draw.text((28, 28), "GLP Coach", font=font_small, fill=(255, 255, 255, 200))

    img.convert("RGB").save(str(output_path), "PNG", optimize=True)
    shot_path.unlink(missing_ok=True)

    logger.success(f"[AppRecorder] App foto post klaar: {output_path}")
    return output_path
