"""
AY Marketing OS — Verificatie & Test Script
============================================

Voert alles uit om het systeem te verifiëren en klaar te maken:
  1. .env API keys controleren
  2. API-verbindingen testen (Anthropic, ElevenLabs, D-ID, TikTok)
  3. Daypulse app verifiëren in app_registry
  4. Autonomie-instellingen tonen
  5. Test-campagne draaien voor Daypulse (alleen met --run-test)

Gebruik:
  python scripts/verify_and_test.py             <- alleen verificatie
  python scripts/verify_and_test.py --run-test  <- ook test-campagne draaien
  python scripts/verify_and_test.py --skip-api  <- sla API-tests over
"""

import os
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

DAYPULSE_APP_ID = "app_cce58eb7"
OK   = "[OK]  "
FAIL = "[FOUT]"
WARN = "[!]   "
INFO = "[i]   "

def sep(title=""):
    line = "=" * 60
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(line)
    else:
        print(line)


# ─── Stap 1: .env keys ────────────────────────────────────────

def check_env_keys() -> dict:
    sep("Stap 1: .env API Keys controleren")

    required = {
        "ANTHROPIC_API_KEY":   "Claude AI  (script generatie)",
        "ELEVENLABS_API_KEY":  "ElevenLabs (stem / TTS audio)",
        "ELEVENLABS_VOICE_ID": "ElevenLabs stem-ID (Aria/Nour)",
        "DID_API_KEY":         "D-ID        (talking head video)",
        "DID_PRESENTER_URL":   "D-ID presenter foto (Nour)",
    }
    optional = {
        "OPENAI_API_KEY":         "OpenAI      (stock footage fallback)",
        "PEXELS_API_KEY":         "Pexels      (gratis stock footage)",
        "TIKTOK_ACCESS_TOKEN":    "TikTok      (publiceren)",
        "TIKTOK_CLIENT_KEY":      "TikTok      (OAuth client key)",
        "TIKTOK_CLIENT_SECRET":   "TikTok      (OAuth client secret)",
    }

    results = {}
    all_required_ok = True

    print("\n  VERPLICHT:")
    for key, desc in required.items():
        val = os.getenv(key, "")
        ok = bool(val and val != "..." and len(val) > 5)
        results[key] = ok
        if not ok:
            all_required_ok = False
        tag = OK if ok else FAIL
        masked = f"{val[:6]}...{val[-4:]}" if ok and len(val) > 12 else ("ONTBREEKT" if not ok else val)
        print(f"  {tag} {key:<30} {masked:<20}  ({desc})")

    print("\n  OPTIONEEL:")
    for key, desc in optional.items():
        val = os.getenv(key, "")
        ok = bool(val and val != "..." and len(val) > 5)
        results[key] = ok
        tag = OK if ok else WARN
        masked = f"{val[:6]}...{val[-4:]}" if ok and len(val) > 12 else ("niet ingesteld" if not ok else val)
        print(f"  {tag} {key:<30} {masked:<20}  ({desc})")

    print()
    if all_required_ok:
        print(f"  {OK} Alle verplichte keys aanwezig")
    else:
        print(f"  {FAIL} Vul de ontbrekende verplichte keys in .env in:")
        print(f"         {ROOT / '.env'}")

    return results


# ─── Stap 2: API verbindingen ─────────────────────────────────

def test_anthropic() -> bool:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content": "Hi"}],
        )
        print(f"  {OK} Anthropic Claude  — verbonden")
        return True
    except Exception as e:
        print(f"  {FAIL} Anthropic         — {e}")
        return False


def test_elevenlabs() -> bool:
    try:
        import httpx
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "9BWtsMINqrJLrRacOk9x")
        r = httpx.get(
            f"https://api.elevenlabs.io/v1/voices/{voice_id}",
            headers={"xi-api-key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            name = r.json().get("name", "?")
            print(f"  {OK} ElevenLabs        — stem '{name}' gevonden (ID: {voice_id})")
            return True
        print(f"  {FAIL} ElevenLabs        — HTTP {r.status_code}")
        return False
    except Exception as e:
        print(f"  {FAIL} ElevenLabs        — {e}")
        return False


def test_did() -> bool:
    try:
        import httpx
        api_key = os.getenv("DID_API_KEY", "")
        r = httpx.get(
            "https://api.d-id.com/credits",
            headers={"Authorization": f"Basic {api_key}"},
            timeout=10,
        )
        if r.status_code == 200:
            remaining = r.json().get("remaining", "?")
            print(f"  {OK} D-ID              — verbonden, {remaining} credits resterend")
            return True
        print(f"  {FAIL} D-ID              — HTTP {r.status_code}: {r.text[:80]}")
        return False
    except Exception as e:
        print(f"  {FAIL} D-ID              — {e}")
        return False


def test_tiktok() -> bool:
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    if not token or token == "...":
        print(f"  {WARN} TikTok            — access token niet ingesteld (publiceren uitgeschakeld)")
        return False
    try:
        import httpx
        r = httpx.get(
            "https://open.tiktokapis.com/v2/user/info/",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "display_name"},
            timeout=10,
        )
        if r.status_code == 200:
            name = r.json().get("data", {}).get("user", {}).get("display_name", "?")
            print(f"  {OK} TikTok            — ingelogd als @{name}")
            return True
        print(f"  {WARN} TikTok            — HTTP {r.status_code} (token misschien verlopen)")
        return False
    except Exception as e:
        print(f"  {WARN} TikTok            — {e}")
        return False


def check_api_connections(env_keys: dict) -> dict:
    sep("Stap 2: API Verbindingen testen")
    print()
    results = {}

    if env_keys.get("ANTHROPIC_API_KEY"):
        results["anthropic"] = test_anthropic()
    else:
        print(f"  {WARN} Anthropic         — overgeslagen (key ontbreekt)")
        results["anthropic"] = False

    if env_keys.get("ELEVENLABS_API_KEY"):
        results["elevenlabs"] = test_elevenlabs()
    else:
        print(f"  {WARN} ElevenLabs        — overgeslagen (key ontbreekt)")
        results["elevenlabs"] = False

    if env_keys.get("DID_API_KEY"):
        results["did"] = test_did()
    else:
        print(f"  {WARN} D-ID              — overgeslagen (key ontbreekt)")
        results["did"] = False

    results["tiktok"] = test_tiktok()
    return results


# ─── Stap 3: Daypulse config ──────────────────────────────────

def verify_daypulse() -> bool:
    sep("Stap 3: Daypulse App Configuratie")
    import json

    print()
    registry_path = ROOT / "configs" / "app_registry.json"
    if not registry_path.exists():
        print(f"  {FAIL} app_registry.json niet gevonden: {registry_path}")
        return False

    with open(registry_path, encoding="utf-8") as f:
        registry = json.load(f)

    app = next((a for a in registry["apps"] if a["id"] == DAYPULSE_APP_ID), None)
    if not app:
        print(f"  {FAIL} Daypulse ({DAYPULSE_APP_ID}) niet gevonden in registry")
        return False

    print(f"  {OK} App:      {app['name']} ({app['id']})")
    print(f"  {OK} URL:      {app.get('url', '?')}")
    print(f"  {OK} Doelgroep:{app.get('target_audience', '?')}")
    print(f"  {OK} Kanalen:  {', '.join(app.get('active_channels', []))}")
    print(f"  {OK} Status:   {'actief' if app.get('active') else 'INACTIEF'}")

    memory_path = ROOT / "data" / "brand_memory" / f"{DAYPULSE_APP_ID}.json"
    if memory_path.exists():
        with open(memory_path, encoding="utf-8") as f:
            memory = json.load(f)
        print(f"  {OK} Brand memory aanwezig")
        persona = memory.get("creator_persona", {})
        if persona:
            print(f"  {OK} Persona:  {persona.get('name', '?')}, {persona.get('age', '?')} jaar, {persona.get('city', '?')}")
        tts = memory.get("tts_voice", "")
        if tts:
            print(f"  {OK} TTS stem: {tts}")
    else:
        print(f"  {WARN} Brand memory niet gevonden: {memory_path}")

    return True


# ─── Stap 4: Autonomie-instellingen ──────────────────────────

def check_settings():
    sep("Stap 4: Autonomie Instellingen (.env)")
    print()

    approval   = os.getenv("APPROVAL_REQUIRED", "true")
    threshold  = os.getenv("AUTO_APPROVE_THRESHOLD", "80")
    posts      = os.getenv("DAILY_POSTS_PER_APP", "2")
    experiments= os.getenv("EXPERIMENTS_ENABLED", "false")
    max_cost   = os.getenv("MAX_COST_PER_CAMPAIGN_USD", "1.00")

    print(f"  APPROVAL_REQUIRED          = {approval}")
    print(f"  AUTO_APPROVE_THRESHOLD     = {threshold}  (viral score >= {threshold} -> auto-publish)")
    print(f"  DAILY_POSTS_PER_APP        = {posts}   (posts per app per dag)")
    print(f"  EXPERIMENTS_ENABLED        = {experiments}")
    print(f"  MAX_COST_PER_CAMPAIGN_USD  = ${max_cost}")

    print()
    if approval.lower() == "false":
        print(f"  {OK} Modus: VOLLEDIG AUTONOOM — elke video wordt direct gepubliceerd")
    else:
        print(f"  {INFO} Modus: SEMI-AUTONOOM — viral score >= {threshold} -> auto-publish, lager -> handmatige goedkeuring")

    slots = {"2": "07:00 + 19:00", "3": "07:00 + 13:00 + 19:00"}.get(posts, f"{posts}x per dag")
    print(f"  {INFO} Publicatieschema: {slots} (Amsterdam-tijd)")


# ─── Stap 5: Test-campagne ────────────────────────────────────

def run_test_campaign():
    sep("Stap 5: Test-campagne Daypulse")
    print()
    print(f"  {INFO} Start pipeline voor Daypulse ({DAYPULSE_APP_ID})...")
    print(f"  {WARN} Dit duurt 2-5 minuten en kost ca. $0.10-0.30")
    print()

    try:
        from workflows.campaign_pipeline import run_pipeline

        def on_progress(msg):
            print(f"    {msg}")

        bundle = run_pipeline(
            app_id=DAYPULSE_APP_ID,
            platform="tiktok",
            on_progress=on_progress,
        )

        print()
        print(f"  {OK} Campagne klaar!")
        print(f"  {INFO} ID:          {bundle.id}")
        print(f"  {INFO} Status:      {bundle.status}")
        print(f"  {INFO} Kosten:      ${bundle.total_cost_usd:.4f}")

        if bundle.viral_score:
            score   = bundle.viral_score.get("composite_score", 0)
            verdict = bundle.viral_score.get("verdict", "")
            print(f"  {INFO} Viral score: {score}/100 ({verdict})")

        if bundle.video_path:
            print(f"  {INFO} Video:       {bundle.video_path}")

        return bundle

    except Exception as e:
        print(f"  {FAIL} Pipeline mislukt: {e}")
        import traceback
        traceback.print_exc()
        return None


# ─── Samenvatting ─────────────────────────────────────────────

def print_summary(env_keys: dict, api_results: dict, daypulse_ok: bool):
    sep("Samenvatting")
    print()

    core_ok = (
        env_keys.get("ANTHROPIC_API_KEY") and
        env_keys.get("ELEVENLABS_API_KEY") and
        env_keys.get("DID_API_KEY") and
        daypulse_ok
    )
    tiktok_ok = env_keys.get("TIKTOK_ACCESS_TOKEN") and api_results.get("tiktok")

    if core_ok:
        print(f"  {OK} Content pipeline: KLAAR (script + voice + video)")
    else:
        print(f"  {FAIL} Content pipeline: NIET KLAAR — vul ontbrekende keys in in .env")

    if tiktok_ok:
        print(f"  {OK} TikTok publiceren: KLAAR")
    else:
        print(f"  {WARN} TikTok publiceren: WACHT OP API KEYS")
        print(f"         Stap 1: ga naar https://developers.tiktok.com")
        print(f"         Stap 2: maak een app aan en vraag Content Posting API aan")
        print(f"         Stap 3: vul TIKTOK_ACCESS_TOKEN in .env in")

    print()
    print("  Start het systeem met:")
    print(f"    deployment\\start_autonomous.bat")
    print()
    print("  Of los:")
    print(f"    python workflows/scheduler.py    <- alleen scheduler")
    print(f"    uvicorn backend.main:app         <- alleen API")


# ─── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AY Marketing OS — Verificatie & Test")
    parser.add_argument("--run-test", action="store_true", help="Draai ook een test-campagne voor Daypulse")
    parser.add_argument("--skip-api", action="store_true", help="Sla API verbindingstests over")
    args = parser.parse_args()

    sep()
    print("  AY Marketing OS — Daypulse Autonome Content Pipeline")
    print("  Verificatie & Test Script")
    sep()
    print()

    env_keys    = check_env_keys()
    print()

    if not args.skip_api:
        api_results = check_api_connections(env_keys)
        print()
    else:
        api_results = {}
        print(f"  {WARN} API verbindingstests overgeslagen (--skip-api)")
        print()

    daypulse_ok = verify_daypulse()
    print()

    check_settings()
    print()

    print_summary(env_keys, api_results, daypulse_ok)
    print()

    if args.run_test:
        core_ok = (
            env_keys.get("ANTHROPIC_API_KEY") and
            env_keys.get("ELEVENLABS_API_KEY") and
            env_keys.get("DID_API_KEY") and
            daypulse_ok
        )
        if not core_ok:
            sep()
            print(f"  {FAIL} Test-campagne overgeslagen — pipeline niet klaar (vul API keys in)")
        else:
            run_test_campaign()
        print()


if __name__ == "__main__":
    main()
