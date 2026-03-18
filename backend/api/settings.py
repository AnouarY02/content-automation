"""
Settings API — systeeminstellingen en integratie-beheer.
"""

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from loguru import logger

router = APIRouter()


@router.get("/tiktok/status")
def tiktok_status():
    """Geef de huidige status van het TikTok token terug."""
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "")
    refresh = os.getenv("TIKTOK_REFRESH_TOKEN", "")
    client_key = os.getenv("TIKTOK_CLIENT_KEY", "")

    return {
        "connected": bool(token and len(token) > 10),
        "has_refresh_token": bool(refresh and len(refresh) > 10),
        "client_key_set": bool(client_key),
        "token_preview": token[:12] + "..." if token else None,
        "checked_at": datetime.utcnow().isoformat(),
    }


@router.post("/tiktok/refresh-token")
def refresh_tiktok_token():
    """
    Vernieuw het TikTok access token via het refresh token.
    Schrijft het nieuwe token automatisch naar .env.
    """
    refresh = os.getenv("TIKTOK_REFRESH_TOKEN", "")
    if not refresh:
        raise HTTPException(
            status_code=400,
            detail="Geen refresh token beschikbaar. Koppel TikTok opnieuw via: python scripts/get_tiktok_token.py",
        )

    try:
        from channels.tiktok.publisher import _refresh_access_token
        new_token = _refresh_access_token()
        if not new_token:
            raise HTTPException(
                status_code=502,
                detail="Token refresh mislukt — TikTok API gaf geen nieuw token terug.",
            )
        logger.success("[Settings API] TikTok token vernieuwd via dashboard")
        return {
            "success": True,
            "message": "TikTok token vernieuwd",
            "token_preview": new_token[:12] + "...",
            "refreshed_at": datetime.utcnow().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Settings API] Token refresh fout: {e}")
        raise HTTPException(status_code=500, detail=str(e))
