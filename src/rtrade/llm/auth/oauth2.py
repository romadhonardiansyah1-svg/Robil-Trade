"""OAuth2 credential provider — standard flows, bukan token konsumen.

Grant yang didukung:
- client_credentials: server-to-server (mis. gateway OAuth, Azure client creds).
- device_code: login interaktif via CLI (mirip gemini-cli) — disimpan via token_store.
- refresh_token: perpanjang access token otomatis saat hampir kedaluwarsa.

Provider ini TIDAK pernah dipakai untuk endpoint chat konsumen. Token_url/scopes
menunjuk ke layanan yang memang menyediakan akses programatik.
"""

from __future__ import annotations

import asyncio
from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from hashlib import sha256
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider
from rtrade.llm.auth.token_store import StoredToken, load_token, save_token

logger = structlog.get_logger(__name__)

_REFRESH_SKEW = 120.0  # refresh 2 menit sebelum expiry


@dataclass
class OAuth2Provider(CredentialProvider):
    provider_id: str
    token_url: str
    client_id: str
    client_secret: str = ""
    scopes: list[str] = field(default_factory=list)
    grant_type: str = "client_credentials"  # | "device_code"
    device_auth_url: str = ""
    store_id: str = ""  # A3: token store id; kosong = provider_id (akun default)

    @property
    def mode(self) -> str:
        return "oauth2"

    @property
    def _sid(self) -> str:
        """Store id — akun spesifik atau provider_id default."""
        return self.store_id or self.provider_id

    async def resolve(self) -> AuthMaterial:
        token = load_token(self._sid)
        now = time.time()
        if token is not None and token.expiry_epoch - _REFRESH_SKEW > now:
            return AuthMaterial(
                bearer_token=token.access_token,
                auth_type="cli_oauth",
                provider_id=self.provider_id,
            )
        if token is not None and token.refresh_token:
            token = await self._refresh(token.refresh_token)
        elif self.grant_type == "client_credentials":
            token = await self._client_credentials()
        else:
            raise RuntimeError(
                f"{self.provider_id}: tidak ada token valid. Jalankan `rtrade auth login`."
            )
        save_token(self._sid, token)
        return AuthMaterial(
            bearer_token=token.access_token,
            auth_type="cli_oauth",
            provider_id=self.provider_id,
        )

    async def _client_credentials(self) -> StoredToken:
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(self.scopes),
        }
        return await self._token_request(data)

    async def _refresh(self, refresh_token: str) -> StoredToken:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        tok = await self._token_request(data)
        # Sebagian provider tidak mengembalikan refresh_token baru → pakai lama.
        if tok.refresh_token is None:
            tok = StoredToken(tok.access_token, refresh_token, tok.expiry_epoch, tok.scopes)
        return tok

    async def _token_request(self, data: dict[str, str]) -> StoredToken:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self.token_url, data=data)
            resp.raise_for_status()
            body = resp.json()
        return StoredToken(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expiry_epoch=time.time() + float(body.get("expires_in", 3600)),
            scopes=self.scopes,
        )

    async def device_login(self) -> StoredToken:
        """Device-code flow interaktif (Hermes-style). Mencetak URL + kode."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            init_data: dict[str, str] = {"client_id": self.client_id}
            if self.scopes:
                init_data["scope"] = " ".join(self.scopes)

            # Try JSON body first (Codex-style), fallback to form data (RFC 8628)
            headers = {"Content-Type": "application/json"}
            init = await client.post(self.device_auth_url, json=init_data, headers=headers)
            if init.status_code >= 400:
                # Fallback: standard RFC 8628 form-encoded
                init = await client.post(self.device_auth_url, data=init_data)
            init.raise_for_status()
            d = init.json()

            # Codex returns: device_auth_id, user_code, interval, expires_at
            # RFC 8628 returns: device_code, verification_uri, user_code
            verification = (
                d.get("url")
                or d.get("verification_url")
                or d.get("verification_uri")
                or d.get("verification_uri_complete")
            )
            # Codex doesn't return verification URL — use known default
            if not verification and "device_auth_id" in d:
                verification = "https://auth.openai.com/codex/device"

            user_code = d.get("user_code")
            # Codex uses 'device_auth_id' instead of 'device_code'
            device_code = d.get("device_code") or d.get("device_auth_id")
            if not device_code:
                raise RuntimeError(
                    f"{self.provider_id}: respons device-init tidak punya 'device_code' "
                    f"(field tersedia: {sorted(d.keys())}). Endpoint mungkin bukan RFC 8628."
                )
            logger.info("buka URL ini & masukkan kode", url=verification, code=user_code)
            interval = float(d.get("interval", 5))

            # Hermes-style display
            print(f"\n{'=' * 60}")  # noqa: T201
            print(f"  Buka : {verification}")  # noqa: T201
            print(f"  Kode : {user_code}")  # noqa: T201
            print(f"{'=' * 60}")  # noqa: T201
            print("  Menunggu Anda login di browser...\n")  # noqa: T201

            logger.info(
                "device code flow dimulai", url=verification, code=user_code, provider=self._sid
            )

            is_codex = "device_auth_id" in d

            while True:
                await asyncio.sleep(interval)

                if is_codex:
                    # Codex: JSON body with device_auth_id + user_code + client_id
                    codex_poll = {
                        "device_auth_id": device_code,
                        "user_code": user_code,
                        "client_id": self.client_id,
                    }
                    poll = await client.post(
                        self.token_url,
                        json=codex_poll,
                        headers={"Content-Type": "application/json"},
                    )
                else:
                    # RFC 8628: form-encoded, full grant_type
                    rfc_poll: dict[str, str] = {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.client_id,
                    }
                    if self.client_secret:
                        rfc_poll["client_secret"] = self.client_secret
                    poll = await client.post(self.token_url, data=rfc_poll)

                body = poll.json()
                if "access_token" in body:
                    tok = StoredToken(
                        body["access_token"],
                        body.get("refresh_token"),
                        time.time() + float(body.get("expires_in", 3600)),
                        self.scopes,
                    )
                    save_token(self._sid, tok)
                    logger.info("device code login berhasil", provider=self._sid)
                    return tok
                error = body.get("error", "")
                # Codex error can be a dict or string
                error_str = error if isinstance(error, str) else str(error)
                if error_str == "slow_down":
                    interval += 5
                    continue
                if error_str not in ("authorization_pending", ""):
                    raise RuntimeError(f"device login gagal: {body}")

    # --- PKCE paste-URL support (O12) ---

    def build_authorize_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        authorize_url: str = "",
    ) -> str:
        """Build authorization URL with PKCE for paste-URL flow."""
        base = authorize_url or self.device_auth_url.replace("/device/code", "/authorize")
        params = (
            f"?client_id={self.client_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={'+'.join(self.scopes)}"
            f"&state={state}"
            f"&code_challenge={code_challenge}"
            f"&code_challenge_method=S256"
        )
        return base + params

    async def exchange_pasted_redirect(
        self,
        redirect_response: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> StoredToken:
        """Exchange authorization code from pasted redirect URL for token."""
        parsed = urlparse(redirect_response)
        qs = parse_qs(parsed.query)
        if "code" not in qs:
            raise ValueError(
                "URL redirect tidak mengandung parameter 'code'. "
                "Pastikan Anda menyalin URL lengkap dari address bar."
            )
        code = qs["code"][0]
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        }
        tok = await self._token_request(data)
        save_token(self._sid, tok)
        return tok


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = sha256(verifier.encode("ascii")).digest()
    challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge
