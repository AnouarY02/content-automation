"""
Base HTTP Client — alle backend-communicatie gaat hier doorheen.

ONTWERP:
  - Synchrone httpx (wordt altijd aangeroepen vanuit QThread, nooit main thread)
  - Centrale timeout + retry configuratie
  - Typed response wrappers (ApiResponse) zodat views nooit rauwe dicts verwerken
  - Foutclassificatie: NetworkError / ApiError / TimeoutError
  - Geen retry in de client — de UI worker beslist of hij opnieuw probeert

GEBRUIK:
  client = BackendClient.instance()
  resp = client.get("/api/campaigns/")
  if resp.ok:
      campaigns = resp.data
  else:
      show_error(resp.error_message)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ApiResponse:
    """Typed wrapper voor elke backend response."""
    ok: bool
    status_code: int = 200
    data: Any = None
    error_message: str = ""
    error_type: str = ""        # "network" | "timeout" | "api" | "parse"
    raw_response: str = ""

    @property
    def is_network_error(self) -> bool:
        return self.error_type == "network"

    @property
    def is_timeout(self) -> bool:
        return self.error_type == "timeout"

    @property
    def is_server_error(self) -> bool:
        return self.status_code >= 500

    @property
    def is_not_found(self) -> bool:
        return self.status_code == 404


class BackendClient:
    """
    Singleton HTTP client voor de FastAPI backend.
    Configureer via BackendClient.configure(base_url=...).
    """

    _instance: BackendClient | None = None

    DEFAULT_BASE_URL = "http://localhost:8000"
    DEFAULT_TIMEOUT = 15.0       # seconden
    UPLOAD_TIMEOUT = 120.0       # voor video uploads

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.DEFAULT_TIMEOUT,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    @classmethod
    def instance(cls) -> BackendClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def configure(cls, base_url: str) -> None:
        """Herinitialiseer de client met een nieuwe base URL (bijv. vanuit Settings)."""
        cls._instance = cls(base_url=base_url)

    # ──────────────────────────────────────────────
    # HTTP METHODEN
    # ──────────────────────────────────────────────

    def get(self, path: str, params: dict | None = None) -> ApiResponse:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> ApiResponse:
        return self._request("POST", path, json=body)

    def patch(self, path: str, body: dict | None = None) -> ApiResponse:
        return self._request("PATCH", path, json=body)

    def delete(self, path: str) -> ApiResponse:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs) -> ApiResponse:
        """Voer een HTTP-request uit en wrap het resultaat in ApiResponse."""
        url = path if path.startswith("http") else path
        try:
            response = self._client.request(method, url, **kwargs)
            return self._parse_response(response)

        except httpx.TimeoutException as e:
            return ApiResponse(
                ok=False,
                error_message=f"Backend reageert niet (timeout {self.DEFAULT_TIMEOUT}s). Is de server gestart?",
                error_type="timeout",
            )
        except httpx.ConnectError:
            return ApiResponse(
                ok=False,
                error_message=f"Kan geen verbinding maken met {self.base_url}. Is de backend gestart?",
                error_type="network",
            )
        except Exception as e:
            return ApiResponse(
                ok=False,
                error_message=f"Onverwachte fout: {type(e).__name__}: {str(e)[:200]}",
                error_type="network",
            )

    def _parse_response(self, response: httpx.Response) -> ApiResponse:
        """Parse HTTP response naar ApiResponse."""
        raw = response.text
        try:
            data = response.json() if raw else None
        except json.JSONDecodeError:
            data = raw

        if response.is_success:
            return ApiResponse(ok=True, status_code=response.status_code, data=data, raw_response=raw)

        # Fout response
        error_msg = ""
        if isinstance(data, dict):
            error_msg = data.get("detail", data.get("message", str(data)))
        else:
            error_msg = raw[:200] if raw else f"HTTP {response.status_code}"

        return ApiResponse(
            ok=False,
            status_code=response.status_code,
            data=data,
            error_message=error_msg,
            error_type="api",
            raw_response=raw,
        )

    def ping(self) -> bool:
        """Check of de backend bereikbaar is. Geeft True/False terug."""
        resp = self.get("/health")
        return resp.ok

    def close(self) -> None:
        self._client.close()
