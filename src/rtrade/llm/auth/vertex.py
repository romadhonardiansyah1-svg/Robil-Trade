"""Vertex AI credentials via Google ADC / OAuth user creds.

Memberi Gemini & Claude (model 'vertex_ai/...') tanpa API key mentah. Kredensial:
- Service account (env GOOGLE_APPLICATION_CREDENTIALS) untuk server, ATAU
- User OAuth (rtrade auth login --provider google) tersimpan oleh google-auth.
litellm menerima vertex_project & vertex_location; ADC menyuplai bearer otomatis.
A4: credentials_path untuk multi-akun + ADC per-akun helpers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider


@dataclass(frozen=True, slots=True)
class VertexProvider(CredentialProvider):
    project: str
    location: str = "us-central1"
    credentials_path: str = ""  # A4: file ADC per-akun

    @property
    def mode(self) -> str:
        return "vertex"

    async def resolve(self) -> AuthMaterial:
        # litellm + google-auth menangani refresh ADC sendiri; kita cukup
        # meneruskan project/location (+ file kredensial bila per-akun).
        extra: dict[str, Any] = {
            "vertex_project": self.project,
            "vertex_location": self.location,
        }
        if self.credentials_path:
            extra["vertex_credentials"] = self.credentials_path
        return AuthMaterial(
            auth_type="vertex",
            provider_id="google_vertex",
            extra_kwargs=extra,
        )


def has_adc() -> bool:
    """True bila ADC tersedia (env atau file kredensial google default)."""
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return True
    try:
        import google.auth

        google.auth.default()
        return True
    except Exception:
        return False


def adc_dir() -> Path:
    """Direktori ADC per-akun: ~/.rtrade/adc (atau $RTRADE_ADC_DIR)."""
    base = os.environ.get("RTRADE_ADC_DIR")
    path = Path(base) if base else Path.home() / ".rtrade" / "adc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def adc_path_for(account: str) -> Path:
    """Path file ADC untuk satu akun google."""
    return adc_dir() / f"google__{account}.json"


def list_adc_accounts() -> list[str]:
    """Akun google yang punya file ADC tersimpan."""
    return sorted(p.stem.removeprefix("google__") for p in adc_dir().glob("google__*.json"))
