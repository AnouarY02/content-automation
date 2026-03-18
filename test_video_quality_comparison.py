"""
Video Quality Comparison Test
=============================
Rendert twee video's:
  1. BEFORE — huidige code (30fps, geen glow, geen font, geen outro)
  2. AFTER  — Devin's upgrade (60fps, glow, Montserrat, branded outro)

Open beide MP4's en vergelijk visueel.
Geen API keys nodig — gebruikt alleen FFmpeg.
"""

import subprocess
import json
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "assets" / "generated" / "test_comparison"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Font paths — escape colon for FFmpeg drawtext filter (C: -> C\:)
_font_bold = (Path(__file__).parent / "assets" / "fonts" / "Montserrat-Bold.ttf").as_posix()
_font_extrabold = (Path(__file__).parent / "assets" / "fonts" / "Montserrat-ExtraBold.ttf").as_posix()
FONT_BOLD = _font_bold.replace(":", "\\:")
FONT_EXTRABOLD = _font_extrabold.replace(":", "\\:")

MUSIC = str(Path(__file__).parent / "assets" / "music" / "cinematic_dark.mp3")

TOTAL = 15
ACCENT = "0x6C63FF"


def esc(text: str) -> str:
    """Escape for FFmpeg drawtext."""
    return (
        text.replace("\\", "\\\\").replace("'", "\u2019")
        .replace(":", "\\:").replace("[", "\\[").replace("]", "\\]")
        .replace(";", "\\;").replace("%", "%%").replace("\n", " ")
    )


def render_before() -> Path:
    """30fps, default font, simpele fade, geen glow, geen outro."""
    out = OUTPUT_DIR / "BEFORE_30fps_basic.mp4"

    fc = (
        f"color=c=0x0a0d14:size=1080x1920:rate=30:duration={TOTAL}[bg_base];"
        f"color=c=0x1a1f2e:size=1080x960:rate=30:duration={TOTAL},"
        f"format=yuva420p,colorchannelmixer=aa=0.7[grad_bot];"
        f"[bg_base][grad_bot]overlay=0:960[bg];"
        f"color=c={ACCENT}:size=200x3:rate=30:duration={TOTAL}[accent];"
        f"[bg][accent]overlay=(W-200)/2:1340[bg2];"

        # Scene 1: Hook 0-4s
        f"[bg2]drawtext=text='{esc('Wist je dit over AI?')}':fontcolor=black@0.4:fontsize=56:"
        f"x=(w-text_w)/2+2:y=702:enable='between(t,0,4)':"
        f"alpha='if(lt(t,0.2),t/0.2,if(gt(t,3.7),(4-t)/0.3,1))'[sh0];"
        f"[sh0]drawtext=text='{esc('Wist je dit over AI?')}':fontcolor=white:fontsize=56:"
        f"x=(w-text_w)/2:y=700:enable='between(t,0,4)':"
        f"alpha='if(lt(t,0.2),t/0.2,if(gt(t,3.7),(4-t)/0.3,1))'[m0];"

        # Scene 2: Body 4-8s
        f"[m0]drawtext=text='{esc('AI verandert alles in 2026')}':fontcolor=black@0.4:fontsize=42:"
        f"x=(w-text_w)/2+2:y=782:enable='between(t,4,8)':"
        f"alpha='if(lt(t,4.2),(t-4)/0.2,if(gt(t,7.7),(8-t)/0.3,1))'[sh1];"
        f"[sh1]drawtext=text='{esc('AI verandert alles in 2026')}':fontcolor=white:fontsize=42:"
        f"x=(w-text_w)/2:y=780:enable='between(t,4,8)':"
        f"alpha='if(lt(t,4.2),(t-4)/0.2,if(gt(t,7.7),(8-t)/0.3,1))'[m1];"

        # Scene 3: Body 8-12s
        f"[m1]drawtext=text='{esc('Van content tot marketing')}':fontcolor=black@0.4:fontsize=42:"
        f"x=(w-text_w)/2+2:y=782:enable='between(t,8,12)':"
        f"alpha='if(lt(t,8.2),(t-8)/0.2,if(gt(t,11.7),(12-t)/0.3,1))'[sh2];"
        f"[sh2]drawtext=text='{esc('Van content tot marketing')}':fontcolor=white:fontsize=42:"
        f"x=(w-text_w)/2:y=780:enable='between(t,8,12)':"
        f"alpha='if(lt(t,8.2),(t-8)/0.2,if(gt(t,11.7),(12-t)/0.3,1))'[m2];"

        # Scene 4: CTA 12-15s
        f"[m2]drawtext=text='{esc('Probeer het nu gratis!')}':fontcolor=black@0.4:fontsize=42:"
        f"x=(w-text_w)/2+2:y=782:enable='between(t,12,15)':"
        f"alpha='if(lt(t,12.2),(t-12)/0.2,if(gt(t,14.7),(15-t)/0.3,1))'[sh3];"
        f"[sh3]drawtext=text='{esc('Probeer het nu gratis!')}':fontcolor=white:fontsize=42:"
        f"x=(w-text_w)/2:y=780:enable='between(t,12,15)':"
        f"alpha='if(lt(t,12.2),(t-12)/0.2,if(gt(t,14.7),(15-t)/0.3,1))'[m3];"

        # Branding
        f"[m3]drawtext=text='{esc('AY Automatisering')}':fontcolor=white@0.3:fontsize=22:"
        f"x=40:y=h-60[branded];"
        f"[branded]format=yuv420p[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", MUSIC,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "0:a",
        "-c:a", "aac", "-b:a", "128k",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-r", "30", "-t", str(TOTAL), "-shortest", str(out),
    ]
    print(f"\n{'='*60}")
    print(f"  RENDERING: BEFORE (30fps, default font, basic fade)")
    print(f"{'='*60}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(f"FOUT: {r.stderr[-500:]}")
        raise RuntimeError("BEFORE failed")
    print(f"  OK -> {out.name}")
    return out


def render_after() -> Path:
    """60fps, Montserrat, glow, branded outro."""
    main_out = OUTPUT_DIR / "_main.mp4"
    outro_out = OUTPUT_DIR / "_outro.mp4"
    final_out = OUTPUT_DIR / "AFTER_60fps_glow_animated.mp4"

    fb = f"fontfile='{FONT_BOLD}'"
    fxb = f"fontfile='{FONT_EXTRABOLD}'"

    # ── MAIN VIDEO ──
    fc = (
        f"color=c=0x0a0d14:size=1080x1920:rate=60:duration={TOTAL}[bg_base];"
        f"color=c=0x1a1f2e:size=1080x960:rate=60:duration={TOTAL},"
        f"format=yuva420p,colorchannelmixer=aa=0.7[grad_bot];"
        f"[bg_base][grad_bot]overlay=0:960[bg];"
        f"color=c={ACCENT}:size=400x3:rate=60:duration={TOTAL}[accent];"
        f"[bg][accent]overlay=(W-400)/2:1340[bg2];"

        # Scene 1: Hook 0-4s — GLOW + font
        f"[bg2]drawtext={fb}:text='{esc('Wist je dit over AI?')}':"
        f"fontcolor=yellow@0.3:fontsize=60:x=(w-text_w)/2:y=700:"
        f"enable='between(t,0,4)':alpha='if(lt(t,0.3),t/0.3,if(gt(t,3.5),(4-t)/0.5,1))'[g0];"
        f"[g0]drawtext={fb}:text='{esc('Wist je dit over AI?')}':"
        f"fontcolor=black@0.5:fontsize=56:x=(w-text_w)/2+2:y=702:"
        f"enable='between(t,0,4)':alpha='if(lt(t,0.3),t/0.3,if(gt(t,3.5),(4-t)/0.5,1))'[sh0];"
        f"[sh0]drawtext={fb}:text='{esc('Wist je dit over AI?')}':"
        f"fontcolor=white:fontsize=56:x=(w-text_w)/2:y=700:"
        f"enable='between(t,0,4)':alpha='if(lt(t,0.3),t/0.3,if(gt(t,3.5),(4-t)/0.5,1))'[m0];"

        # Scene 2: Body 4-8s — GLOW + font
        f"[m0]drawtext={fb}:text='{esc('AI verandert alles in 2026')}':"
        f"fontcolor=yellow@0.3:fontsize=46:x=(w-text_w)/2:y=780:"
        f"enable='between(t,4,8)':alpha='if(lt(t,4.3),(t-4)/0.3,if(gt(t,7.5),(8-t)/0.5,1))'[g1];"
        f"[g1]drawtext={fb}:text='{esc('AI verandert alles in 2026')}':"
        f"fontcolor=black@0.5:fontsize=42:x=(w-text_w)/2+2:y=782:"
        f"enable='between(t,4,8)':alpha='if(lt(t,4.3),(t-4)/0.3,if(gt(t,7.5),(8-t)/0.5,1))'[sh1];"
        f"[sh1]drawtext={fb}:text='{esc('AI verandert alles in 2026')}':"
        f"fontcolor=white:fontsize=42:x=(w-text_w)/2:y=780:"
        f"enable='between(t,4,8)':alpha='if(lt(t,4.3),(t-4)/0.3,if(gt(t,7.5),(8-t)/0.5,1))'[m1];"

        # Scene 3: Body 8-12s — GLOW + font
        f"[m1]drawtext={fb}:text='{esc('Van content tot marketing')}':"
        f"fontcolor=yellow@0.3:fontsize=46:x=(w-text_w)/2:y=780:"
        f"enable='between(t,8,12)':alpha='if(lt(t,8.3),(t-8)/0.3,if(gt(t,11.5),(12-t)/0.5,1))'[g2];"
        f"[g2]drawtext={fb}:text='{esc('Van content tot marketing')}':"
        f"fontcolor=black@0.5:fontsize=42:x=(w-text_w)/2+2:y=782:"
        f"enable='between(t,8,12)':alpha='if(lt(t,8.3),(t-8)/0.3,if(gt(t,11.5),(12-t)/0.5,1))'[sh2];"
        f"[sh2]drawtext={fb}:text='{esc('Van content tot marketing')}':"
        f"fontcolor=white:fontsize=42:x=(w-text_w)/2:y=780:"
        f"enable='between(t,8,12)':alpha='if(lt(t,8.3),(t-8)/0.3,if(gt(t,11.5),(12-t)/0.5,1))'[m2];"

        # Scene 4: CTA 12-15s — GLOW + font
        f"[m2]drawtext={fb}:text='{esc('Probeer het nu gratis!')}':"
        f"fontcolor=yellow@0.3:fontsize=46:x=(w-text_w)/2:y=780:"
        f"enable='between(t,12,15)':alpha='if(lt(t,12.3),(t-12)/0.3,if(gt(t,14.5),(15-t)/0.5,1))'[g3];"
        f"[g3]drawtext={fb}:text='{esc('Probeer het nu gratis!')}':"
        f"fontcolor=black@0.5:fontsize=42:x=(w-text_w)/2+2:y=782:"
        f"enable='between(t,12,15)':alpha='if(lt(t,12.3),(t-12)/0.3,if(gt(t,14.5),(15-t)/0.5,1))'[sh3];"
        f"[sh3]drawtext={fb}:text='{esc('Probeer het nu gratis!')}':"
        f"fontcolor=white:fontsize=42:x=(w-text_w)/2:y=780:"
        f"enable='between(t,12,15)':alpha='if(lt(t,12.3),(t-12)/0.3,if(gt(t,14.5),(15-t)/0.5,1))'[m3];"

        # Branding
        f"[m3]drawtext={fb}:text='{esc('AY Automatisering')}':"
        f"fontcolor=white@0.4:fontsize=22:x=40:y=h-60[branded];"
        f"[branded]format=yuv420p[out]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", MUSIC,
        "-filter_complex", fc,
        "-map", "[out]", "-map", "0:a",
        "-c:a", "aac", "-b:a", "192k",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-r", "60", "-t", str(TOTAL), "-shortest", str(main_out),
    ]
    print(f"\n{'='*60}")
    print(f"  RENDERING: AFTER — Main (60fps, glow, Montserrat)")
    print(f"{'='*60}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"FOUT: {r.stderr[-800:]}")
        raise RuntimeError("AFTER main failed")
    print(f"  OK -> {main_out.name}")

    # ── BRANDED OUTRO (4s) ──
    odur = 4
    fc_o = (
        f"color=c=0x0a0d14:size=1080x1920:rate=60:duration={odur}[obg];"
        f"color=c=0x1a1f2e:size=1080x960:rate=60:duration={odur},"
        f"format=yuva420p,colorchannelmixer=aa=0.5[ograd];"
        f"[obg][ograd]overlay=0:480[obg2];"
        f"color=c={ACCENT}:size=120x4:rate=60:duration={odur}[oline];"
        f"[obg2][oline]overlay=(W-120)/2:740[obg3];"
        # App name
        f"[obg3]drawtext={fxb}:text='{esc('AY Automatisering')}':"
        f"fontcolor=white:fontsize=64:x=(w-text_w)/2:y=780:"
        f"alpha='min(t/0.8,1)'[oname];"
        # Tagline
        f"[oname]drawtext={fb}:text='{esc('Automatiseer je groei')}':"
        f"fontcolor=white@0.6:fontsize=32:x=(w-text_w)/2:y=870:"
        f"alpha='if(lt(t,0.5),0,min((t-0.5)/0.6,1))'[otag];"
        # CTA
        f"[otag]drawtext={fb}:text='{esc('Link in bio')}':"
        f"fontcolor=white@0.8:fontsize=28:x=(w-text_w)/2:y=1100:"
        f"alpha='if(lt(t,1.0),0,min((t-1.0)/0.5,1))'[octa];"
        f"[octa]format=yuv420p[oout]"
    )

    cmd_o = [
        "ffmpeg", "-y",
        "-filter_complex", fc_o,
        "-map", "[oout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-r", "60", "-t", str(odur), str(outro_out),
    ]
    print(f"  Rendering branded outro...")
    r2 = subprocess.run(cmd_o, capture_output=True, text=True, timeout=60)
    if r2.returncode != 0:
        print(f"FOUT outro: {r2.stderr[-600:]}")
        raise RuntimeError("Outro failed")
    print(f"  OK -> {outro_out.name}")

    # ── CONCAT main + outro ──
    cl = OUTPUT_DIR / "_concat.txt"
    cl.write_text(
        f"file '{main_out.as_posix()}'\nfile '{outro_out.as_posix()}'\n"
    )

    cmd_c = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cl),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-r", "60", "-c:a", "aac", "-b:a", "192k", str(final_out),
    ]
    print(f"  Concatenating main + outro...")
    r3 = subprocess.run(cmd_c, capture_output=True, text=True, timeout=60)
    if r3.returncode != 0:
        print(f"FOUT concat: {r3.stderr[-500:]}")
        raise RuntimeError("Concat failed")

    main_out.unlink(missing_ok=True)
    outro_out.unlink(missing_ok=True)
    cl.unlink(missing_ok=True)
    print(f"  OK -> {final_out.name}")
    return final_out


def probe(path: Path) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(r.stdout) if r.returncode == 0 else {}


def main():
    print("\n" + "=" * 60)
    print("  VIDEO QUALITY COMPARISON TEST")
    print("  BEFORE (huidige) vs AFTER (Devin upgrade)")
    print("=" * 60)

    before = render_before()
    after = render_after()

    print("\n" + "=" * 60)
    print("  RESULTAAT VERGELIJKING")
    print("=" * 60)

    for label, path in [("BEFORE (huidig)", before), ("AFTER (upgrade)", after)]:
        info = probe(path)
        mb = path.stat().st_size / (1024 * 1024)
        vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        dur = info.get("format", {}).get("duration", "?")
        print(f"\n  {label}:")
        print(f"    Bestand:    {path.name}")
        print(f"    Grootte:    {mb:.1f} MB")
        print(f"    Resolutie:  {vs.get('width', '?')}x{vs.get('height', '?')}")
        print(f"    FPS:        {vs.get('r_frame_rate', '?')}")
        print(f"    Duur:       {dur}s")

    print(f"\n  Verbeteringen in AFTER:")
    print(f"    [+] 60fps ipv 30fps — 2x vloeiender")
    print(f"    [+] Montserrat Bold font (was: system default)")
    print(f"    [+] Glow effect achter tekst (subtiele yellow glow)")
    print(f"    [+] Langzamere fade-out (0.5s ipv 0.3s) — minder abrupt")
    print(f"    [+] Bredere accent lijn (400px ipv 200px)")
    print(f"    [+] 4s branded outro eindscherm")
    print(f"    [+] Hogere audio bitrate (192k ipv 128k)")
    print(f"    [+] Lagere CRF (18 ipv 20 = hogere video kwaliteit)")

    print(f"\n  Open deze map om te vergelijken:")
    print(f"    {OUTPUT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
