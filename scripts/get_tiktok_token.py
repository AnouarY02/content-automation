"""
TikTok OAuth — Haal je access_token op.

Stap 0 (eenmalig in TikTok Developer Console):
  Redirect URI instellen op: https://daypulse-zeta.vercel.app/api/tiktok/callback
  OF een andere URL die je bezit (hoeft niet te werken, alleen geregistreerd te zijn)

Vereisten in .env:
  TIKTOK_CLIENT_KEY=...
  TIKTOK_CLIENT_SECRET=...

Gebruik:
  cd /c/AY-automatisering/content-automation
  python scripts/get_tiktok_token.py
"""

import base64
import hashlib
import os
import re
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI  = "https://daypulse-zeta.vercel.app/api/tiktok/callback"

# TIKTOK_POST_MODE=DIRECT_POST → vraag ook video.publish scope aan
# (vereist goedgekeurd 'Content Posting API' product in TikTok Developer Console)
_post_mode = os.getenv("TIKTOK_POST_MODE", "INBOX").strip().upper()
SCOPE = "user.info.basic,video.upload,video.publish" if _post_mode == "DIRECT_POST" else "user.info.basic,video.upload"


def exchange_code_for_token(code: str, code_verifier: str) -> dict:
    import httpx
    r = httpx.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data={
            "client_key":    CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def write_token_to_env(access_token: str, refresh_token: str = ""):
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8")

    def replace_or_append(text, key, value):
        pattern = rf"^{key}=.*$"
        if re.search(pattern, text, re.MULTILINE):
            return re.sub(pattern, f"{key}={value}", text, flags=re.MULTILINE)
        return text + f"\n{key}={value}"

    content = replace_or_append(content, "TIKTOK_ACCESS_TOKEN", access_token)
    if refresh_token:
        content = replace_or_append(content, "TIKTOK_REFRESH_TOKEN", refresh_token)
    env_path.write_text(content, encoding="utf-8")


def extract_code_from_input(raw: str) -> str:
    """Haal de code op uit een volledige URL of een losse code."""
    raw = raw.strip()
    if "?" in raw or "code=" in raw:
        # Gebruiker plakte de volledige redirect URL
        parsed = urllib.parse.urlparse(raw)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if not code:
            raise ValueError(f"Geen 'code' parameter gevonden in URL: {raw}")
        return code
    # Gebruiker plakte alleen de code
    return raw


def main():
    print("=" * 60)
    print("  TikTok OAuth — Access Token ophalen")
    print("=" * 60)
    print()

    if not CLIENT_KEY or CLIENT_KEY == "...":
        print("[FOUT] TIKTOK_CLIENT_KEY niet ingesteld in .env")
        print("       Ga naar developers.tiktok.com → je app → 'App keys'")
        sys.exit(1)

    if not CLIENT_SECRET or CLIENT_SECRET == "...":
        print("[FOUT] TIKTOK_CLIENT_SECRET niet ingesteld in .env")
        sys.exit(1)

    # PKCE
    code_verifier  = secrets.token_urlsafe(64)[:128]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    # OAuth URL
    params = urllib.parse.urlencode({
        "client_key":            CLIENT_KEY,
        "response_type":         "code",
        "scope":                 SCOPE,
        "redirect_uri":          REDIRECT_URI,
        "state":                 "ay_marketing_os",
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?{params}"

    print("[1/3] Browser openen voor TikTok login...")
    print()
    webbrowser.open(auth_url)

    print("[2/3] Log in op TikTok en geef toestemming.")
    print()
    print("  Na het inloggen word je doorgestuurd naar een pagina.")
    print("  Die pagina hoeft NIET te laden — kopieer gewoon de")
    print("  volledige URL uit je adresbalk en plak hem hieronder.")
    print()
    print("  Voorbeeld URL:")
    print("  https://daypulse-zeta.vercel.app/api/tiktok/callback?code=ABC123&state=ay_marketing_os")
    print()
    raw_input = input("  Plak hier de volledige URL (of alleen de code): ").strip()

    if not raw_input:
        print("[FOUT] Niets ingevoerd.")
        sys.exit(1)

    try:
        auth_code = extract_code_from_input(raw_input)
    except ValueError as e:
        print(f"[FOUT] {e}")
        sys.exit(1)

    print()
    print("[3/3] Access token ophalen bij TikTok...")
    try:
        token_data    = exchange_code_for_token(auth_code, code_verifier)
        access_token  = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")

        if not access_token:
            print(f"[FOUT] Geen access_token in respons: {token_data}")
            sys.exit(1)

        write_token_to_env(access_token, refresh_token)

        print()
        print("=" * 60)
        print("  [OK] TikTok account gekoppeld!")
        print()
        print("  TIKTOK_ACCESS_TOKEN opgeslagen in .env")
        expires_in = token_data.get("expires_in", "?")
        if str(expires_in).isdigit():
            print(f"  Token geldig: ~{int(expires_in) // 86400} dagen")
        print()
        print("  Systeem starten:")
        print("    deployment\\start_autonomous.bat")
        print("=" * 60)

    except Exception as e:
        print(f"[FOUT] Token uitwisseling mislukt: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
