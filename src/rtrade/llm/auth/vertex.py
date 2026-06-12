"""Vertex AI credentials via Google ADC / OAuth user creds.

Memberi Gemini & Claude (model 'vertex_ai/...') tanpa API key mentah. Kredensial:
- Service account (env GOOGLE_APPLICATION_CREDENTIALS) untuk server, ATAU
- User OAuth (rtrade auth login --provider google) tersimpan oleh google-auth.
litellm menerima vertex_project & vertex_location; ADC menyuplai bearer otomatis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider


@dataclass(frozen=True, slots=True)
class VertexProvider(CredentialProvider):
    project: str
    location: str = "us-central1"

    @property
    def mode(self) -> str:
        return "vertex"

    async def resolve(self) -> AuthMaterial:
        # litellm + google-auth menangani refresh ADC sendiri; kita cukup
        # meneruskan project/location. (ADC dibaca dari env/credential file.)
        return AuthMaterial(
            auth_type="vertex",
            provider_id="google_vertex",
            extra_kwargs={
                "vertex_project": self.project,
                "vertex_location": self.location,
            },
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
