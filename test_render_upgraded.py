"""
Test Render — ProVideoProvider v8 Product-Aware.
Product-specifieke demo: app screenshots, phone mockup,
logo watermark, brand colors, niche-specific narrative.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Voeg project root toe aan path
sys.path.insert(0, str(Path(__file__).parent))

from video_engine.providers.pro_video_provider import ProVideoProvider
from video_engine.retention_optimizer import RetentionOptimizer

# ── Product-specifiek script — Dossiertijd health niche ──────────
# Dit script volgt het PROVEN ad-framework:
# Hook (aandacht) → Problem (herkenning) → Demo (product tonen) → CTA (actie)
#
# Het verschil met het oude script: scene type "demo" triggert de phone
# mockup met echte app screenshots. De kijker ZIET de app.

test_script = {
    "video_type": "text_on_screen",
    "full_voiceover_text": (
        "Elke dag hetzelfde verhaal. Bergen papierwerk, eindeloos typen, "
        "en geen tijd voor wat echt belangrijk is. "
        "Daarom hebben wij Dossiertijd gebouwd. "
        "Eén klik en je dossier is compleet. "
        "Probeer het nu gratis via de link in bio."
    ),
    "scenes": [
        {
            "type": "hook",
            "voiceover": "Elke dag hetzelfde verhaal. Bergen papierwerk, eindeloos typen.",
            "on_screen_text": "Ken je dit?",
            "visual_description": "Healthcare worker overwhelmed at desk with papers",
            "visual_search_query": "young woman frustrated office paperwork",
            "duration_sec": 7,
        },
        {
            "type": "problem",
            "voiceover": "En geen tijd voor wat echt belangrijk is.",
            "on_screen_text": "",
            "visual_description": "Person stressed staring at computer screen",
            "visual_search_query": "person stressed computer desk tired",
            "duration_sec": 5,
        },
        {
            "type": "demo",
            "voiceover": "Daarom hebben wij Dossiertijd gebouwd. Eén klik en je dossier is compleet.",
            "on_screen_text": "Dossiertijd",
            "visual_description": "App interface showing automated documentation",
            "visual_search_query": "woman smiling using phone app modern office",
            "demo_pages": ["/"],  # Welke pagina's van de app te tonen
            "demo_page_index": 0,
            "duration_sec": 9,
        },
        {
            "type": "cta",
            "voiceover": "Probeer het nu gratis via de link in bio.",
            "on_screen_text": "Link in bio",
            "visual_description": "Person excited tapping phone screen",
            "visual_search_query": "person excited phone download app",
            "duration_sec": 5,
        },
    ],
}

test_memory = {
    "app_name": "Dossiertijd",
    "niche": "health",
    "url": "https://dossiertijd.nl",
    "visual_style": {
        "gradient": "purple",
        "accent_color": "#6C63FF",
    },
}

output_dir = Path(__file__).parent / "assets" / "generated" / "test_v8_product"
output_dir.mkdir(parents=True, exist_ok=True)

print("\n" + "=" * 60)
print("  VIDEO RENDER v8 -- Product-Aware Demo")
print("  Phone mockup | Logo watermark | Brand colors")
print("  App screenshots | Niche-specific narrative")
print("=" * 60)

provider = ProVideoProvider(voice="aria", tts_speed=1.0)

try:
    video_path = provider.produce(test_script, test_memory, output_dir)
    print(f"\n  Video klaar: {video_path}")
    print(f"  Kosten: ${provider.total_cost_usd:.3f}")

    if video_path and video_path.exists():
        import subprocess
        import json

        size_mb = video_path.stat().st_size / (1024 * 1024)
        info = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        d = json.loads(info.stdout)
        vs = next(s for s in d["streams"] if s["codec_type"] == "video")
        print(f"\n  Video specs:")
        print(f"    {vs['width']}x{vs['height']} @ {vs['r_frame_rate']}fps")
        print(f"    Duur: {float(d['format']['duration']):.1f}s")
        print(f"    Grootte: {size_mb:.1f} MB")

        # Check SRT/VTT
        srt_files = list(output_dir.glob("*.srt")) + list(output_dir.glob("*.vtt"))
        if srt_files:
            print(f"\n  Subtitels: {len(srt_files)} bestanden")
            for sf in srt_files:
                print(f"    {sf.name}")

        # Check app screenshots
        from video_engine.providers.pro_video_provider import APP_ASSETS_DIR
        app_ss = list(APP_ASSETS_DIR.rglob("*.png"))
        if app_ss:
            print(f"\n  App screenshots gecaptured: {len(app_ss)}")
            for ss in app_ss:
                print(f"    {ss.name} ({ss.stat().st_size // 1024}KB)")

        # Retention tracking check
        print(f"\n  Retention tracking...")
        optimizer = RetentionOptimizer()
        records = optimizer._load_records()
        print(f"    Records opgeslagen: {len(records)}")
        if records:
            latest = records[-1]
            print(f"    Laatste: {latest.get('video_id', '?')}")
            print(f"    Hook: '{latest.get('hook_text', '')[:40]}...'")

        # Open de video
        os.startfile(str(video_path))
        print(f"\n  Video geopend!")

except Exception as e:
    print(f"\n  FOUT: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
