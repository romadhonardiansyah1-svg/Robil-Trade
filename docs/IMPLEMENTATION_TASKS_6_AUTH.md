# IMPLEMENTATION TASKS 6 — PLUGGABLE OAUTH AUTH LAYER (O0–O14)

> Tujuan: dukung **login OAuth multi-provider** untuk LLM (Gemini, Claude, GPT) lewat jalur
> identitas cloud yang SAH — bukan API key mentah. Hasil akhir: `rtrade auth login` (device-code,
> mirip gemini-cli) → token refresh tersimpan terenkripsi → runtime auto-refresh → LLM jalan via
> Vertex AI / Azure OpenAI. API key tetap didukung sebagai mode default (testing pakai Gemini key).
>
> **Batas yang DITEGAKKAN (jangan dilanggar walau diminta):** JANGAN menulis kode yang menyalin/
> replay token langganan konsumen (sesi Claude.ai / ChatGPT) sebagai backend. Guard
> `_FORBIDDEN_KEY_PREFIXES=("sk-ant-oat",)` di `core/config.py` TETAP ADA. OAuth di sini = OAuth2
> resmi ke endpoint yang memang menyediakan akses programatik (Google Cloud/Vertex, Azure AD,
> OAuth-issuing gateway). Token OAuth TIDAK masuk ke field `*_api_key` — ia lewat token store.
>
> Aturan kerja: IMPLEMENTATION_TASKS.md §0. Commit per task. BUKTI Select-String. Test wajib hijau.
> Ini task auth/infra — JANGAN sentuh logika trading (engine/strategi/guardrail).
>
> **NORTH STAR (tidak boleh hilang):** hasil akhir HARUS memungkinkan user MENAMBAH model AI baru
> lewat login OAuth gaya Hermes agent — `rtrade auth login --provider <id>` → token tersimpan →
> model langsung bisa dipakai bot — TANPA menempel API key mentah, dan TANPA mengubah kode. Provider
> baru cukup ditambah lewat manifest `config/oauth_providers.example.yaml` (O8) + route di
> `settings.yaml` (O11). Bila sebuah task terasa bertentangan dengan tujuan ini, hentikan dan catat.
>
> **ALUR & URUTAN (baca sebelum mulai):** O1→O5 membangun fondasi (provider, token store, Vertex,
> Azure). O6 mewiring versi SEDERHANA (global `auth_mode`) — ini batu loncatan. O8→O11 membangun
> lapisan Hermes-style penuh (provider profiles + per-model routing) yang **menggantikan** wiring
> global O6. Lihat catatan supersede di O6 & O11 agar tidak meninggalkan kode mati / dua jalur
> pemilihan model yang bertabrakan.

---

## 0K — KONVENSI KANONIK (resolusi konflik antar-task — BACA & PATUHI SEBELUM MULAI)

Beberapa task menulis hal yang sama dengan bentuk berbeda. Bagian ini adalah **SATU-SATUNYA sumber
kebenaran**. Bila ada potongan kode di task lain yang bentrok dengan sini, IKUTI SINI.

**K1 — `AuthMaterial` didefinisikan SEKALI saja** (di O1, `src/rtrade/llm/auth/base.py`). O14 TIDAK
mendefinisikan ulang; ia hanya MEMAKAI. Bentuk kanonik (semua field baru diberi DEFAULT supaya
konstruksi lama `AuthMaterial(api_key=...)` / `AuthMaterial(extra_kwargs=...)` di O1/O3/O4 tidak pecah):
```python
@dataclass(frozen=True, slots=True)
class AuthMaterial:
    api_key: str | None = None
    bearer_token: str | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)
    auth_type: str = "api_key"      # "api_key" | "cli_oauth" | "vertex" | "azure_ad"
    provider_id: str = ""
    profile_name: str = ""

    def merge_into(self, kwargs: dict[str, Any]) -> None:
        token = self.bearer_token or self.api_key   # LiteLLM memakai 'api_key' sbg slot bearer
        if token:
            kwargs["api_key"] = token
        kwargs.update(self.extra_kwargs)
```

**K2 — SATU file manifest** = `config/oauth_providers.example.yaml` (top-key `oauth_providers:`).
File `ai_auth_providers.example.yaml` yang disebut O14 **DIBATALKAN** — lebur semua field-nya ke
manifest tunggal ini. Loader hanya `provider_profiles.load_provider_profiles()` (O8).

**K3 — ID provider KANONIK** (pakai PERSIS ini di O8/O11/O13/O14):
`google_vertex`, `azure_openai`, `openai_codex`, `xai`, `generic_gateway`, `custom_external`.
DILARANG varian lain (`openai_codex_oauth`, `xai_oauth`, dst). Nama `auth_profile` bebas, tapi
`provider_id` di dalamnya WAJIB salah satu ID kanonik ini.

**K4 — `LLMClient.complete()` SUDAH menerima `model: str` per-panggilan.** JANGAN tambah param `model`
kedua di konstruktor. O11/O14 hanya mengubah SUMBER nilai `model` (dari `resolve_model_auth`) +
menyuntik `credential_provider`. Di `complete()`: bila `self._credential_provider` ada →
`material = await self._credential_provider.resolve(); material.merge_into(kwargs)`; else perilaku
lama. TIDAK ADA cabang "OAuth belum didukung" di runtime.

**K5 — SATU kelas OAuth** = `OAuth2Provider` (O3). `CliOAuthProvider` (O14) adalah pembungkus tipis
di atas `OAuth2Provider` + dukungan `external_command`; JANGAN buat implementasi OAuth kedua terpisah.

**K6 — Semua I/O auth async-aman:** pakai `await asyncio.sleep(...)` (BUKAN `time.sleep`) di coroutine,
dan `httpx.AsyncClient`. JANGAN pernah `logger`-kan token/secret apa pun (selaras S2 redaction).

---

## O0 — Dependency baru (DIIZINKAN khusus untuk milestone ini)

Tambah ke `pyproject.toml` `[project].dependencies`:
```
    "google-auth>=2.35",
    "google-auth-oauthlib>=1.2",
    "cryptography>=43.0",
```
Lalu: `uv lock` → `uv sync`. (Azure AD via `azure-identity` ditambahkan hanya di O5 bila dikerjakan.)
**Commit**: `build: add google-auth + cryptography for OAuth auth layer (O0)`

---

## O1 — Abstraksi CredentialProvider + AuthMaterial

File baru `src/rtrade/llm/auth/__init__.py` (kosong + docstring) dan
`src/rtrade/llm/auth/base.py`:
```python
"""Pluggable credential providers for LLM calls.

Setiap provider menjawab satu pertanyaan: "untuk model ini, bagaimana cara
mengautentikasi panggilan litellm?" — via API key, bearer token OAuth, atau
kredensial Vertex/Azure. Runtime tidak tahu detailnya, hanya memanggil resolve().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class AuthMaterial:
    """Bahan auth yang diteruskan ke litellm.acompletion(**kwargs). Bentuk KANONIK (lihat 0K/K1).

    Semua field punya default → konstruksi `AuthMaterial(api_key=...)` /
    `AuthMaterial(extra_kwargs=...)` tetap valid. O14 TIDAK mendefinisikan ulang kelas ini.
    """

    api_key: str | None = None
    bearer_token: str | None = None
    extra_kwargs: dict[str, Any] = field(default_factory=dict)  # mis. vertex_project, azure_ad_token
    auth_type: str = "api_key"  # "api_key" | "cli_oauth" | "vertex" | "azure_ad"
    provider_id: str = ""
    profile_name: str = ""

    def merge_into(self, kwargs: dict[str, Any]) -> None:
        """Terapkan ke kwargs litellm (in-place). LiteLLM memakai 'api_key' sbg slot bearer."""
        token = self.bearer_token or self.api_key
        if token:
            kwargs["api_key"] = token
        kwargs.update(self.extra_kwargs)


class CredentialProvider(ABC):
    """Sumber kredensial untuk satu model/provider."""

    @property
    @abstractmethod
    def mode(self) -> str:
        """Identifier mode: 'api_key' | 'oauth2' | 'vertex' | 'azure_ad'."""

    @abstractmethod
    async def resolve(self) -> AuthMaterial:
        """Kembalikan AuthMaterial siap pakai (refresh token bila perlu)."""
```
Dan `src/rtrade/llm/auth/api_key.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from rtrade.llm.auth.base import AuthMaterial, CredentialProvider


@dataclass(frozen=True, slots=True)
class ApiKeyProvider(CredentialProvider):
    """Mode default: API key resmi (perilaku lama)."""

    api_key: str

    @property
    def mode(self) -> str:
        return "api_key"

    async def resolve(self) -> AuthMaterial:
        return AuthMaterial(api_key=self.api_key, auth_type="api_key")
```

**Test** `tests/unit/test_auth_base.py`: `ApiKeyProvider("k").resolve()` → `api_key=="k"`;
`AuthMaterial(api_key="k").merge_into(d)` mengisi `d["api_key"]`.
**BUKTI**: `Select-String -Path src\rtrade\llm\auth\base.py -Pattern "class CredentialProvider"` = 1.
**Commit**: `feat(auth): CredentialProvider abstraction + ApiKeyProvider (O1)`

---

## O2 — Token store terenkripsi (refresh token aman di disk)

File baru `src/rtrade/llm/auth/token_store.py`:
```python
"""Penyimpanan token OAuth terenkripsi di disk (Fernet).

Lokasi default: ~/.rtrade/tokens/<provider>.json (atau $RTRADE_TOKEN_DIR).
File chmod 0600. Dienkripsi dengan key dari env RTRADE_TOKEN_KEY (Fernet base64).
Jika RTRADE_TOKEN_KEY kosong: simpan plaintext TAPI log peringatan keras + chmod 0600.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredToken:
    access_token: str
    refresh_token: str | None
    expiry_epoch: float  # UTC epoch detik
    scopes: list[str]


def _token_dir() -> Path:
    base = os.environ.get("RTRADE_TOKEN_DIR")
    path = Path(base) if base else Path.home() / ".rtrade" / "tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fernet():  # type: ignore[no-untyped-def]
    key = os.environ.get("RTRADE_TOKEN_KEY", "")
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def save_token(provider: str, token: StoredToken) -> None:
    path = _token_dir() / f"{provider}.json"
    raw = json.dumps(asdict(token)).encode()
    f = _fernet()
    data = f.encrypt(raw) if f is not None else raw
    if f is None:
        logger.warning("RTRADE_TOKEN_KEY kosong — token disimpan plaintext", provider=provider)
    path.write_bytes(data)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def load_token(provider: str) -> StoredToken | None:
    path = _token_dir() / f"{provider}.json"
    if not path.exists():
        return None
    data = path.read_bytes()
    f = _fernet()
    try:
        raw = f.decrypt(data) if f is not None else data
        d = json.loads(raw)
        return StoredToken(**d)
    except Exception as exc:
        logger.error("gagal baca token store", provider=provider, error=str(exc))
        return None
```
`.gitignore` — tambah `.rtrade/` dan `*.token`.
**Test** `tests/unit/test_token_store.py` (set `RTRADE_TOKEN_DIR=tmp_path`, `RTRADE_TOKEN_KEY`=
`Fernet.generate_key().decode()` via monkeypatch.setenv): save→load roundtrip identik;
tanpa key → tetap roundtrip (plaintext) ; file mode user-only (skip cek mode di Windows).
**BUKTI**: `Select-String -Path src\rtrade\llm\auth\token_store.py -Pattern "Fernet|chmod"` >= 2.
**Commit**: `feat(auth): encrypted on-disk OAuth token store (O2)`

---

## O3 — OAuth2Provider (client_credentials + device_code + refresh)

File baru `src/rtrade/llm/auth/oauth2.py`:
```python
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
import time
from dataclasses import dataclass, field

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

    @property
    def mode(self) -> str:
        return "oauth2"

    async def resolve(self) -> AuthMaterial:
        token = load_token(self.provider_id)
        now = time.time()
        if token is not None and token.expiry_epoch - _REFRESH_SKEW > now:
            return AuthMaterial(api_key=token.access_token)
        if token is not None and token.refresh_token:
            token = await self._refresh(token.refresh_token)
        elif self.grant_type == "client_credentials":
            token = await self._client_credentials()
        else:
            raise RuntimeError(
                f"{self.provider_id}: tidak ada token valid. Jalankan `rtrade auth login`."
            )
        save_token(self.provider_id, token)
        return AuthMaterial(api_key=token.access_token)

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
        """Device-code flow interaktif (dipanggil CLI O4). Mencetak URL + kode."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            init = await client.post(
                self.device_auth_url,
                data={"client_id": self.client_id, "scope": " ".join(self.scopes)},
            )
            init.raise_for_status()
            d = init.json()
            verification = d.get("verification_url") or d.get("verification_uri")
            logger.info("buka URL ini & masukkan kode", url=verification, code=d["user_code"])
            interval = float(d.get("interval", 5))
            device_code = d["device_code"]
            while True:
                await asyncio.sleep(interval)  # K6: async-aman, jangan blok event loop
                poll = await client.post(
                    self.token_url,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
                body = poll.json()
                if "access_token" in body:
                    tok = StoredToken(
                        body["access_token"],
                        body.get("refresh_token"),
                        time.time() + float(body.get("expires_in", 3600)),
                        self.scopes,
                    )
                    save_token(self.provider_id, tok)
                    return tok
                if body.get("error") not in ("authorization_pending", "slow_down"):
                    raise RuntimeError(f"device login gagal: {body.get('error')}")
```

**Test** `tests/unit/test_oauth2.py` (respx mock `token_url`): client_credentials → token tersimpan
& `resolve()` return access_token; token belum expiry → tidak ada HTTP call kedua (pakai cache);
expiry lewat + refresh_token → panggil refresh. (Mock `load_token`/`save_token` via
`RTRADE_TOKEN_DIR=tmp_path`.)
**BUKTI**: `Select-String -Path src\rtrade\llm\auth\oauth2.py -Pattern "device_code|refresh_token|client_credentials"` >= 3.
**Commit**: `feat(auth): OAuth2 provider — client-credentials, device-code, refresh (O3)`

---

## O4 — Vertex (Google ADC) provider + CLI login

Ini jalur SAH untuk Gemini DAN Claude via Google Cloud.
File baru `src/rtrade/llm/auth/vertex.py`:
```python
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
```
CLI baru `src/rtrade/cli/auth.py`:
```python
"""Login OAuth: python -m rtrade.cli.auth login --provider google

google  → device/installed-app flow via google-auth-oauthlib (scope cloud-platform),
          simpan refresh token ke ADC default (well-known location google).
generic → OAuth2Provider.device_login() memakai config dari env (token store rtrade).
"""

from __future__ import annotations

import argparse
import asyncio

import structlog

logger = structlog.get_logger(__name__)

_GOOGLE_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _google_login() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    client_secrets = _require_env("GOOGLE_OAUTH_CLIENT_SECRETS")  # path JSON dari GCP console
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=_GOOGLE_SCOPES)
    # CATATAN: run_local_server() butuh browser di mesin ini — akan GAGAL/menggantung di VPS headless.
    # Untuk VPS pakai flow paste_url (O12). Versi O12 menggantikan baris ini.
    creds = flow.run_local_server(port=0)
    # Simpan ke ADC well-known path supaya google-auth & litellm otomatis memakainya.
    import json
    from pathlib import Path

    adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    adc.parent.mkdir(parents=True, exist_ok=True)
    adc.write_text(
        json.dumps(
            {
                "type": "authorized_user",
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "refresh_token": creds.refresh_token,
            }
        ),
        encoding="utf-8",
    )
    logger.info("google login sukses — ADC tersimpan", path=str(adc))


def _require_env(name: str) -> str:
    import os

    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"env {name} wajib diisi (lihat docs/AUTH_OAUTH.md)")
    return val


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login")
    login.add_argument("--provider", required=True, choices=["google", "generic"])
    args = parser.parse_args()
    if args.cmd == "login" and args.provider == "google":
        _google_login()
    elif args.cmd == "login" and args.provider == "generic":
        from rtrade.llm.auth.registry import build_generic_oauth_from_env

        asyncio.run(build_generic_oauth_from_env().device_login())


if __name__ == "__main__":
    main()
```

**Test** `tests/unit/test_vertex_provider.py`: `VertexProvider("proj","us").resolve()` →
`extra_kwargs["vertex_project"]=="proj"`; `has_adc()` return False saat env bersih (monkeypatch
hapus GOOGLE_APPLICATION_CREDENTIALS + monkeypatch `google.auth.default` raise).
**BUKTI**: `Select-String -Path src\rtrade\cli\auth.py -Pattern "InstalledAppFlow|application_default_credentials"` >= 1.
**Commit**: `feat(auth): Vertex ADC provider + google OAuth login CLI (O4)`

---

## O5 — (Opsional) Azure AD provider untuk GPT

Hanya kerjakan bila user butuh GPT via Azure OpenAI.
1. O0-style: tambah `azure-identity>=1.19` ke deps (uv lock/sync).
2. `src/rtrade/llm/auth/azure_ad.py` — `AzureADProvider(tenant, client_id, client_secret,
   endpoint, scope="https://cognitiveservices.azure.com/.default")` → `resolve()` pakai
   `azure.identity.ClientSecretCredential.get_token(scope)` →
   `AuthMaterial(auth_type="azure_ad", provider_id="azure_openai",
   extra_kwargs={"azure_ad_token": ..., "api_base": endpoint, "api_version": ...})`.
3. **WAJIB** tambahkan classmethod `from_env()` (dipanggil registry O6) — baca env
   `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_OPENAI_ENDPOINT`;
   bila ada yang kosong → `raise ConfigError("Azure OpenAI env belum lengkap: ...")` (pesan jelas,
   bukan KeyError mentah).
**Test**: monkeypatch credential → token dummy → extra_kwargs berisi azure_ad_token;
`from_env()` tanpa env → ConfigError.
**Commit**: `feat(auth): Azure AD provider for Azure OpenAI (O5)`

---

## O6 — Registry + wiring ke LLMClient & config

> **CATATAN SUPERSEDE:** O6 = wiring SEDERHANA berbasis global `llm.auth_mode`, sengaja dibuat dulu
> agar OAuth bisa diuji cepat. Saat O11 dikerjakan, jalur global `auth_mode` ini DIGANTI oleh
> per-model `model_routes`. Maka di O6: JANGAN menghapus parameter `credential_provider` di
> `LLMClient` (O11 memakainya kembali), tapi pemilihan provider global di `scan.py` akan ditimpa
> O11. Tulis kode O6 yang mudah diperluas, bukan dikunci.

1. `config/settings.yaml` `llm:` — per-model auth mode. Tambah field di `LLMSettings`
   (`core/config.py`):
   ```python
   auth_mode: str = Field(default="api_key")  # api_key|oauth2|vertex|azure_ad
   vertex_project: str = Field(default="")
   vertex_location: str = Field(default="us-central1")
   ```
   settings.yaml:
   ```yaml
   llm:
     auth_mode: api_key            # ganti ke 'vertex' untuk Gemini/Claude via Google login
     vertex_project: ""
     vertex_location: us-central1
     # saat auth_mode=vertex, set model jadi: vertex_ai/gemini-2.5-pro, vertex_ai/claude-opus-4-...
   ```
2. `src/rtrade/llm/auth/registry.py`:
   ```python
   from __future__ import annotations

   import os

   from rtrade.core.config import LLMSettings, Secrets
   from rtrade.llm.auth.api_key import ApiKeyProvider
   from rtrade.llm.auth.base import CredentialProvider
   from rtrade.llm.auth.oauth2 import OAuth2Provider
   from rtrade.llm.auth.vertex import VertexProvider


   def build_credential_provider(llm: LLMSettings, secrets: Secrets) -> CredentialProvider:
       mode = llm.auth_mode
       if mode == "vertex":
           return VertexProvider(project=llm.vertex_project, location=llm.vertex_location)
       if mode == "oauth2":
           return build_generic_oauth_from_env()
       if mode == "azure_ad":
           from rtrade.llm.auth.azure_ad import AzureADProvider  # lazy (dep opsional)

           return AzureADProvider.from_env()
       return ApiKeyProvider(api_key=secrets.gemini_api_key_1)


   def _require_env(name: str) -> str:
       val = os.environ.get(name)
       if not val:
           from rtrade.core.errors import ConfigError

           raise ConfigError(f"env {name} wajib diisi untuk OAuth mode (lihat docs/AUTH_OAUTH.md)")
       return val


   def build_generic_oauth_from_env() -> OAuth2Provider:
       return OAuth2Provider(
           provider_id=os.environ.get("RTRADE_OAUTH_PROVIDER_ID", "generic_gateway"),
           token_url=_require_env("RTRADE_OAUTH_TOKEN_URL"),
           client_id=_require_env("RTRADE_OAUTH_CLIENT_ID"),
           client_secret=os.environ.get("RTRADE_OAUTH_CLIENT_SECRET", ""),
           scopes=os.environ.get("RTRADE_OAUTH_SCOPES", "").split(),
           grant_type=os.environ.get("RTRADE_OAUTH_GRANT", "client_credentials"),
           device_auth_url=os.environ.get("RTRADE_OAUTH_DEVICE_URL", ""),
       )
   ```
3. `src/rtrade/llm/client.py` — `LLMClient` terima `credential_provider: CredentialProvider | None`.
   Di `complete()`, SEBELUM memanggil `litellm.acompletion`, ganti blok `if self.api_key:`:
   ```python
   if self._credential_provider is not None:
       material = await self._credential_provider.resolve()
       material.merge_into(kwargs)
   elif self.api_key:
       kwargs["api_key"] = self.api_key
   ```
   (Tambah field dataclass `_credential_provider` + parameter konstruktor; default None →
   perilaku lama PERSIS, semua test LLM lama tetap hijau.)
4. `pipeline/scan.py` — saat membuat `LLMClient(...)` (blok F1 dan coroner di W1), bila
   `cfg.settings.llm.auth_mode != "api_key"`:
   ```python
   from rtrade.llm.auth.registry import build_credential_provider

   client = LLMClient(
       timeout=cfg.settings.llm.timeout_seconds,
       temperature=cfg.settings.llm.temperature,
       credential_provider=build_credential_provider(cfg.settings.llm, cfg.secrets),
   )
   ```
   else tetap `LLMClient(api_key=cfg.secrets.gemini_api_key_1, ...)`.

**Test** `tests/unit/test_auth_registry.py`: `build_credential_provider` mode api_key→ApiKeyProvider,
vertex→VertexProvider(project), default→ApiKeyProvider. `LLMClient` dengan provider mock
(resolve → AuthMaterial(api_key="X")) → kwargs litellm berisi api_key X (monkeypatch
`litellm.acompletion` perekam kwargs).
**BUKTI**:
```powershell
Select-String -Path src\rtrade\llm\client.py -Pattern "credential_provider"   # >= 2
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "build_credential_provider"  # >= 1
```
**Commit**: `feat(auth): credential registry wired into LLMClient + scan (O6)`

---

## O7 — Dokumen AUTH_OAUTH.md + keamanan

Buat `docs/AUTH_OAUTH.md`:
1. **Tabel jalur**: model → platform → mode (Gemini/Claude→Vertex→`vertex`;
   GPT→Azure OpenAI→`azure_ad`; gateway OAuth→`oauth2`; API key→`api_key`).
2. **Setup Vertex (login Google)**:
   - Buat OAuth client (Desktop app) di GCP Console → unduh `client_secrets.json`.
   - `set GOOGLE_OAUTH_CLIENT_SECRETS=...path...` → `python -m rtrade.cli.auth login --provider google`
     → browser → ADC tersimpan.
   - settings.yaml: `auth_mode: vertex`, `vertex_project: <gcp-project>`,
     model `analyst_model: vertex_ai/gemini-2.5-pro`, `flagship_model: vertex_ai/claude-opus-4-...`.
   - Enable Vertex AI API + (untuk Claude) aktifkan model di Model Garden.
3. **Keamanan**:
   - `RTRADE_TOKEN_KEY` = `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`
     → simpan di .env (JANGAN commit). Tanpa ini token store plaintext (peringatan).
   - Token store di `~/.rtrade/tokens` chmod 0600. ADC di `~/.config/gcloud`. Keduanya di
     `.gitignore`.
   - Di VPS: jalankan `rtrade auth login` SEKALI via SSH (X-forward tidak perlu — google-auth
     fallback ke console URL). Atau pakai service account JSON (lebih cocok server):
     `GOOGLE_APPLICATION_CREDENTIALS=/opt/robil-trade/secrets/sa.json` + `auth_mode: vertex`.
4. **Catatan jujur (sertakan apa adanya)**: mode `vertex`/`azure_ad`/`oauth2` adalah OAuth/identitas
   resmi untuk akses programatik. Token langganan konsumen (Claude.ai/ChatGPT) TIDAK didukung dan
   guard `sk-ant-oat` tetap aktif — gunakan Vertex/Bedrock untuk Claude, Azure untuk GPT.
5. **Deploy**: docker-compose service `app`/`bot` perlu mount kredensial:
   `- ./secrets:/app/secrets:ro` dan env `GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/sa.json`
   (atau mount `~/.rtrade` & `~/.config/gcloud`). Tambahkan ke `docker-compose.prod.yml` + `.gitignore secrets/`.

**BUKTI**: `Test-Path docs\AUTH_OAUTH.md` = True;
`Select-String -Path docs\AUTH_OAUTH.md -Pattern "vertex|RTRADE_TOKEN_KEY"` >= 2.
**Commit**: `docs(auth): OAuth/Vertex/Azure setup guide + security (O7)`

---

## O8 — Hermes-style OAuth Agent Broker (provider profiles)

Tujuan: UX satu pintu seperti Hermes agent:
`rtrade auth providers`, `rtrade auth login --provider <id>`, `rtrade auth status`,
`rtrade auth logout --provider <id>`, dan `rtrade auth doctor`.

**Batas keras**: ini BUKAN token harvester. DILARANG membaca token dari `~/.codex`, `~/.claude`,
browser cookies, localStorage, session DB, atau file login tool consumer lain. Profil `codex_openai`,
`xai`, dan provider lain hanya boleh aktif bila ada OAuth/OIDC resmi untuk akses programatik ATAU
gateway OAuth enterprise yang operator miliki sendiri. Jika provider hanya menyediakan API key, CLI
harus menampilkan status `api_key_only`, bukan mencoba OAuth tidak resmi.

1. Tambah contoh manifest `config/oauth_providers.example.yaml`:
   ```yaml
   oauth_providers:
     google_vertex:
       label: "Google Vertex AI"
       auth_mode: vertex
       capability: official_adc
       enabled: true
       login_flow: google_adc
       scopes:
         - "https://www.googleapis.com/auth/cloud-platform"

     azure_openai:
       label: "Azure OpenAI"
       auth_mode: azure_ad
       capability: official_client_credentials
       enabled: true
       tenant_id_env: AZURE_TENANT_ID
       client_id_env: AZURE_CLIENT_ID
       client_secret_env: AZURE_CLIENT_SECRET
       scopes:
         - "https://cognitiveservices.azure.com/.default"

     codex_openai:
       label: "Codex / OpenAI programmatic access"
       auth_mode: oauth2
       capability: requires_official_oauth
       enabled: false
       client_id_env: RTRADE_CODEX_OPENAI_CLIENT_ID
       client_secret_env: RTRADE_CODEX_OPENAI_CLIENT_SECRET
       issuer_metadata_url_env: RTRADE_CODEX_OPENAI_ISSUER_METADATA_URL
       token_url_env: RTRADE_CODEX_OPENAI_TOKEN_URL
       device_auth_url_env: RTRADE_CODEX_OPENAI_DEVICE_URL
       scopes_env: RTRADE_CODEX_OPENAI_SCOPES
       note: "Aktifkan hanya bila endpoint OAuth resmi/gateway enterprise tersedia."

     xai:
       label: "xAI programmatic access"
       auth_mode: oauth2
       capability: requires_official_oauth
       enabled: false
       client_id_env: RTRADE_XAI_CLIENT_ID
       client_secret_env: RTRADE_XAI_CLIENT_SECRET
       issuer_metadata_url_env: RTRADE_XAI_ISSUER_METADATA_URL
       token_url_env: RTRADE_XAI_TOKEN_URL
       device_auth_url_env: RTRADE_XAI_DEVICE_URL
       scopes_env: RTRADE_XAI_SCOPES
       note: "Jika xAI hanya menyediakan API key di akun ini, gunakan mode api_key."

     generic_gateway:
       label: "Enterprise OAuth gateway"
       auth_mode: oauth2
       capability: oauth_gateway
       enabled: true
       token_url_env: RTRADE_OAUTH_TOKEN_URL
       device_auth_url_env: RTRADE_OAUTH_DEVICE_URL
       client_id_env: RTRADE_OAUTH_CLIENT_ID
       client_secret_env: RTRADE_OAUTH_CLIENT_SECRET
       scopes_env: RTRADE_OAUTH_SCOPES
   ```
2. File baru `src/rtrade/llm/auth/provider_profiles.py`:
   - `OAuthProviderProfile` dataclass (`provider_id`, `label`, `auth_mode`, `capability`,
     `enabled`, `login_flow`, env refs, scopes).
   - `load_provider_profiles(path: Path | None) -> dict[str, OAuthProviderProfile]`.
   - `resolve_env_profile(profile) -> ResolvedOAuthProfile` yang mengambil secret HANYA dari env.
   - `validate_profile(profile)`:
     - `token_url`/`device_auth_url`/`issuer_metadata_url` wajib HTTPS bila diisi.
     - secret literal di YAML ditolak; hanya `*_env`.
     - `capability=requires_official_oauth` + endpoint kosong -> `disabled_unsupported`.
     - path/session consumer (`.codex`, `.claude`, `Cookies`, `Local Storage`, `oauth_token`) ditolak.
3. `src/rtrade/llm/auth/registry.py`:
   - Tambah `build_provider_from_profile(provider_id: str, profile_path: Path | None = None)`.
   - `google_vertex` -> `VertexProvider`.
   - `azure_openai` -> `AzureADProvider.from_env()`.
   - `generic_gateway`/provider OAuth resmi -> `OAuth2Provider`.
   - provider disabled/unsupported -> raise `ConfigError` dengan pesan jelas.
4. `src/rtrade/cli/auth.py`:
   - `providers`: print tabel `id | label | auth_mode | capability | enabled | status`.
   - `login --provider <id>`: jalankan flow dari registry profile.
   - `status [--provider <id>]`: cek token store + expiry + scopes tanpa mencetak token.
   - `logout --provider <id>`: hapus token store provider setelah konfirmasi CLI.
   - `doctor --provider <id>`: validasi manifest/env/HTTPS/scopes dan beri next action.

**Test** `tests/unit/test_oauth_provider_profiles.py`:
- manifest valid -> profile terbaca.
- secret literal di YAML ditolak.
- `codex_openai` disabled tanpa endpoint -> `doctor` status `disabled_unsupported`.
- URL non-HTTPS ditolak.
- path berisi `.codex`/cookie/session ditolak.

**BUKTI**:
```powershell
Select-String -Path config\oauth_providers.example.yaml -Pattern "codex_openai|xai|generic_gateway"  # >= 3
Select-String -Path src\rtrade\llm\auth\provider_profiles.py -Pattern "disabled_unsupported|requires_official_oauth|HTTPS"  # >= 3
```
**Commit**: `feat(auth): Hermes-style OAuth provider profiles and auth CLI UX (O8)`

---

## O9 — Adapter Codex/OpenAI, xAI, dan provider lain (tanpa bypass consumer login)

Tujuan: memberi tempat eksplisit untuk provider yang user sebut "login OAuth Codex, xAI, dll." tanpa
mengaburkan batas legal/teknis. Adapter boleh dibuat, tetapi default-nya aman: tidak aktif sampai
endpoint OAuth resmi/gateway enterprise dikonfigurasi.

1. Buat folder `src/rtrade/llm/auth/adapters/`:
   - `__init__.py`
   - `openai_codex.py`
   - `xai.py`
   - `generic_oidc.py`
2. `openai_codex.py`:
   ```python
   """Codex/OpenAI OAuth adapter.

   Adapter ini hanya membangun OAuth2Provider dari endpoint OAuth resmi/gateway enterprise
   yang diberikan lewat manifest/env. DILARANG membaca token Codex CLI, ChatGPT session,
   browser cookie, atau file credential consumer.
   """
   ```
   Implementasi:
   - `build_openai_codex_provider(profile: ResolvedOAuthProfile) -> OAuth2Provider`.
   - Wajib `profile.has_official_oauth_endpoint` atau `profile.capability == "oauth_gateway"`.
   - `provider_id="codex_openai"` untuk token store.
   - Error eksplisit:
     `"codex_openai OAuth belum aktif: isi endpoint OAuth resmi/gateway enterprise, atau gunakan API key resmi."`
3. `xai.py`:
   - `build_xai_provider(profile: ResolvedOAuthProfile) -> OAuth2Provider`.
   - Bila endpoint OAuth tidak tersedia, status `api_key_only`/`disabled_unsupported`.
   - Dilarang fallback diam-diam ke scraping dashboard atau token web.
4. `generic_oidc.py`:
   - Tambah discovery `.well-known/openid-configuration` bila `issuer_metadata_url` diisi.
   - Ambil `token_endpoint` dan `device_authorization_endpoint` dari metadata.
   - Validasi `issuer` exact-match dengan manifest untuk mencegah metadata substitution.
5. Integrasi `registry.py`:
   - `provider_id in {"codex_openai", "openai_codex"}` -> `build_openai_codex_provider`.
   - `provider_id == "xai"` -> `build_xai_provider`.
   - provider lain dengan metadata OIDC -> `generic_oidc`.

**Test** `tests/unit/test_oauth_adapters.py`:
- `codex_openai` tanpa endpoint resmi -> raise `ConfigError`.
- `codex_openai` dengan `oauth_gateway` + token URL HTTPS -> membangun `OAuth2Provider`.
- `xai` tanpa endpoint -> status `api_key_only`/unsupported, tidak membuat HTTP call.
- OIDC discovery metadata issuer mismatch -> ditolak.
- Tidak ada test/fixture yang menyentuh path `~/.codex`, cookie browser, atau session DB.

**BUKTI**:
```powershell
Select-String -Path src\rtrade\llm\auth\adapters\openai_codex.py -Pattern "DILARANG|consumer|gateway"  # >= 3
Select-String -Path src\rtrade\llm\auth\adapters\xai.py -Pattern "api_key_only|disabled_unsupported"  # >= 1
```
**Commit**: `feat(auth): safe Codex/OpenAI and xAI OAuth adapter stubs (O9)`

---

## O10 — Dokumentasi Hermes-style login + rollout aman

1. Update `docs/AUTH_OAUTH.md` dengan bagian **Hermes-style provider login**:
   - `python -m rtrade.cli.auth providers`
   - `python -m rtrade.cli.auth doctor --provider codex_openai`
   - `python -m rtrade.cli.auth login --provider generic_gateway`
   - `python -m rtrade.cli.auth status`
   - `python -m rtrade.cli.auth logout --provider xai`
2. Tambah tabel status provider:
   | Provider | Status awal | Cara legal dipakai |
   |---|---|---|
   | `google_vertex` | enabled | Google ADC / service account |
   | `azure_openai` | enabled bila env Azure lengkap | Azure AD client credentials |
   | `generic_gateway` | enabled bila env gateway lengkap | OAuth gateway enterprise |
   | `codex_openai` | disabled sampai endpoint resmi/gateway diisi | OAuth resmi/gateway enterprise; bukan token Codex CLI/ChatGPT |
   | `xai` | disabled/api_key_only sampai OAuth resmi tersedia | API key resmi atau OAuth resmi bila tersedia |
3. Tambah runbook `docs/runbooks/oauth-provider-onboarding.md`:
   - Cara menambahkan provider baru ke manifest.
   - Checklist legal: endpoint resmi, scopes, grant type, rate limit, revoke path.
   - Checklist security: HTTPS, secret via env, token store terenkripsi, no consumer session import.
   - Checklist test: `doctor`, login sandbox, refresh, logout, audit log redaction.
4. Tambah audit log non-secret:
   - `auth_login_started`, `auth_login_succeeded`, `auth_login_failed`, `auth_logout`.
   - Field boleh: `provider_id`, `capability`, `grant_type`, `expires_at`.
   - Field dilarang: `access_token`, `refresh_token`, `client_secret`, raw authorization header.
5. Tambah final safety check:
   ```powershell
   Select-String -Path src\rtrade\llm\auth\**\*.py -Pattern "\.codex|Cookies|Local Storage|refresh_token" -Context 0,2
   ```
   Hasil yang boleh untuk `refresh_token` hanya di token store/OAuth request resmi; `.codex`,
   `Cookies`, dan `Local Storage` harus 0.

**BUKTI**:
```powershell
Test-Path docs\runbooks\oauth-provider-onboarding.md
Select-String -Path docs\AUTH_OAUTH.md -Pattern "Hermes-style|codex_openai|xai"  # >= 3
```
**Commit**: `docs(auth): provider onboarding guide for Hermes-style OAuth login (O10)`

---

## O11 — Model-provider auth selector (`api_key` vs `cli_oauth`)

Klarifikasi tujuan: yang diinginkan adalah **AI model provider** bisa dipilih cara loginnya:
API key tetap ada, tetapi bot juga bisa memakai token OAuth hasil login CLI bot ini, mirip pola
Hermes. Jadi pemilihan terjadi di level model/provider, bukan hanya global `llm.auth_mode`.

**Definisi penting**: `cli_oauth` berarti token dibuat oleh `python -m rtrade.cli.auth login
--provider <id>` dan disimpan di token store RTrade. Ini TIDAK berarti mengimpor token dari CLI lain
seperti Codex CLI/Claude Code/browser session. External CLI login boleh dijadikan inspirasi UX, bukan
sumber token backend.

> **CATATAN SUPERSEDE & SINKRONISASI (wajib dibaca sebelum mulai O11):**
> 1. **Menggantikan O6, bukan menumpuk.** O11 mengganti pemilihan provider global (`auth_mode`) di
>    `scan.py` dengan `model_routes` per-peran. Saat O11 selesai, jalur global O6 di `scan.py`
>    HARUS dihapus (jangan biarkan dua sumber kebenaran). Field `credential_provider` di `LLMClient`
>    (dari O6) TETAP dipakai — hanya cara MEMILIH-nya yang pindah ke router.
> 2. **Selaraskan dengan blok LLM F1 + cascade F5 yang SUDAH ada.** `scan.py` saat ini memanggil
>    `run_llm_pipeline(..., analyst_model=cfg.settings.llm.analyst_model, critic_model=...)` dan
>    eskalasi flagship via `should_escalate` (F5). O11 mengganti SUMBER `analyst_model/critic_model/
>    flagship_model` menjadi hasil `resolve_model_auth(cfg, role)` (`.model`), dan menyuntikkan
>    `credential_provider` ke `LLMClient` yang sama. JANGAN bikin jalur pemilihan model kedua yang
>    paralel — perluas yang ada. Cascade F5 tetap berlaku: tier-1 pakai route `analyst`/`critic`,
>    tier-2 pakai route `flagship` (tambahkan role `flagship` ke `model_routes`).
> 3. **Backward compatible.** Bila `auth_profiles`/`model_routes` kosong → perilaku lama
>    (`auth_mode: api_key`, model dari `analyst_model/critic_model/flagship_model`) HARUS tetap
>    jalan persis. Semua test LLM lama wajib hijau.

1. Tambah konsep `auth_profiles` di `config/settings.yaml`:
   ```yaml
   llm:
     # Backward compatible: field lama tetap jalan bila auth_profiles kosong.
     auth_mode: api_key
     analyst_model: trading-analyst
     critic_model: trading-critic
     backup_model: trading-backup

     default_auth_profile: gemini_api_key

     auth_profiles:
       gemini_api_key:
         provider_id: google_ai_studio
         auth_type: api_key
         api_key_secret: gemini_api_key_1

       vertex_cli_oauth:
         provider_id: google_vertex
         auth_type: cli_oauth
         credential_provider: vertex
         login_provider: google_vertex
         vertex_project: "${GOOGLE_CLOUD_PROJECT}"
         vertex_location: us-central1

       codex_openai_cli_oauth:
         provider_id: codex_openai
         auth_type: cli_oauth
         credential_provider: oauth2
         login_provider: codex_openai
         enabled: false
         note: "Aktif hanya bila endpoint OAuth resmi/gateway enterprise tersedia."

       xai_cli_oauth:
         provider_id: xai
         auth_type: cli_oauth
         credential_provider: oauth2
         login_provider: xai
         enabled: false
         note: "Aktif hanya bila OAuth resmi/gateway tersedia; bila tidak, gunakan api_key."

     model_routes:
       analyst:
         model: vertex_ai/gemini-2.5-pro
         auth_profile: vertex_cli_oauth
       critic:
         model: openai/gpt-4.1
         auth_profile: codex_openai_cli_oauth
       backup:
         model: gemini/gemini-2.0-flash
         auth_profile: gemini_api_key
   ```
   Catatan: nama model di atas contoh routing. Implementasi tetap memakai alias LiteLLM yang sudah ada
   bila user memilih `trading-analyst`/`trading-critic`.

2. `core/config.py`:
   - Tambah dataclass/model `LLMAuthProfile`:
     - `provider_id: str`
     - `auth_type: Literal["api_key", "cli_oauth"]`
     - `credential_provider: str = "api_key"` (`api_key|oauth2|vertex|azure_ad`)
     - `login_provider: str = ""`
     - `api_key_secret: str = ""`
     - `enabled: bool = True`
     - `vertex_project`, `vertex_location`
   - Tambah `LLMModelRoute`:
     - `model: str`
     - `auth_profile: str`
   - Tambah validator:
     - route yang menunjuk profile tidak ada -> `ConfigError`.
     - `auth_type=cli_oauth` + `enabled=false` -> route tidak boleh dipakai kecuali eksplisit diaktifkan.
     - `api_key_secret` hanya boleh menunjuk field di `Secrets`, bukan literal API key.

3. `src/rtrade/llm/auth/registry.py`:
   - Tambah `build_credential_provider_for_profile(profile, secrets)`.
   - `auth_type=api_key` -> ambil secret lewat allowlist `Secrets` (`gemini_api_key_1`, dst).
   - `auth_type=cli_oauth` + `credential_provider=vertex` -> `VertexProvider`.
   - `auth_type=cli_oauth` + `credential_provider=oauth2` -> `build_provider_from_profile(profile.login_provider)`.
   - `auth_type=cli_oauth` + token belum ada/expired refresh gagal -> error:
     `Jalankan python -m rtrade.cli.auth login --provider <login_provider>`.

4. `src/rtrade/llm/client.py`:
   - Tambah parameter `model: str` dan `credential_provider`.
   - `complete()` tidak lagi berasumsi satu API key global; ia menerima `ResolvedModelAuth`
     (`model`, `AuthMaterial`) dari router.
   - Tetap backward compatible: jika tidak ada `auth_profiles`, pakai perilaku lama `api_key`.

5. File baru `src/rtrade/llm/model_router.py`:
   ```python
   @dataclass(frozen=True, slots=True)
   class ResolvedModelAuth:
       role: str              # analyst|critic|backup
       model: str
       auth_profile: str
       provider_id: str
       credential_provider: CredentialProvider


   def resolve_model_auth(cfg: AppConfig, role: str) -> ResolvedModelAuth:
       """Pilih model + credential provider berdasarkan llm.model_routes[role]."""
   ```
   Role yang wajib: `analyst`, `critic`, `backup`. Tambahkan helper untuk self-consistency agar
   semua sample analyst memakai route/auth profile yang sama.

6. `pipeline/scan.py`:
   - Saat membuat analyst/critic/coroner client, panggil `resolve_model_auth(cfg, "analyst")`,
     `resolve_model_auth(cfg, "critic")`, dst.
   - Log non-secret:
     `llm_route_selected role=analyst model=... auth_profile=vertex_cli_oauth provider_id=google_vertex`.
   - Jangan pernah log token, API key, refresh token, authorization header.

7. CLI UX:
   - `python -m rtrade.cli.auth providers` menampilkan provider login yang tersedia.
   - `python -m rtrade.cli.auth login --provider xai` membuat token untuk `xai_cli_oauth`
     jika endpoint OAuth resmi/gateway terkonfigurasi.
   - `python -m rtrade.cli.auth use --role analyst --profile vertex_cli_oauth` mengubah route config
     secara aman (opsional; boleh hanya dokumentasi jika edit YAML manual lebih sederhana).
   - `python -m rtrade.cli.auth status --models` menampilkan:
     `role | model | auth_profile | auth_type | login_status | expires_at`.

**Test** `tests/unit/test_model_auth_router.py`:
- route analyst -> `vertex_cli_oauth` menghasilkan `VertexProvider`.
- route backup -> `gemini_api_key` menghasilkan `ApiKeyProvider`.
- route ke profile tidak ada -> error jelas.
- `cli_oauth` disabled tapi dipakai route -> error jelas.
- `api_key_secret` literal seperti `sk-...`/`AIza...` ditolak; harus nama field `Secrets`.
- `status --models` tidak mencetak token/API key.

**BUKTI**:
```powershell
Select-String -Path config\settings.yaml -Pattern "auth_profiles|model_routes|cli_oauth"  # >= 3
Select-String -Path src\rtrade\llm\model_router.py -Pattern "ResolvedModelAuth|resolve_model_auth"  # >= 2
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "llm_route_selected|auth_profile"  # >= 2
```
**Commit**: `feat(auth): route AI models through selectable API-key or CLI-OAuth auth profiles (O11)`

---

## O12 — Login flow engine: loopback + PASTE-URL + DEVICE-CODE (UX gaya Hermes/Codex)

Tujuan: `rtrade auth login --provider <id>` HARUS mendukung 3 gaya login, dipilih otomatis sesuai
lingkungan (ada browser vs headless VPS) atau dipaksa via `--flow`. Inilah inti "login OAuth seperti
Hermes/Codex" yang user minta.

**Tiga flow (semua menyimpan token via token_store O2 / ADC O4):**
1. `loopback` — buka browser, redirect ke `http://localhost:<port>` ditangkap otomatis
   (sudah ada di O4 `run_local_server`). Dipakai bila ada display.
2. `paste_url` — bot CETAK auth URL; user login; browser diarahkan ke halaman localhost yang GAGAL
   dimuat (normal); user SALIN URL lengkap halaman itu; TEMPEL ke prompt bot; bot ekstrak `code` →
   tukar jadi token. **Wajib jadi default di VPS/SSH (tanpa browser).**
3. `device_code` — bot tampilkan `user_code` + `verification_url`; user buka di perangkat lain,
   masukkan kode; bot polling token (sudah ada di O3 `device_login`). Gaya Codex.

1. File baru `src/rtrade/llm/auth/login_flows.py`:
   ```python
   """Tiga gaya login OAuth. Tidak ada yang menyentuh sesi/token tool consumer lain."""

   from __future__ import annotations

   import os
   from enum import StrEnum


   class LoginFlow(StrEnum):
       LOOPBACK = "loopback"
       PASTE_URL = "paste_url"
       DEVICE_CODE = "device_code"


   def auto_flow(preferred: str | None) -> LoginFlow:
       """Pilih flow: --flow eksplisit > headless→paste_url > ada DISPLAY→loopback."""
       if preferred:
           return LoginFlow(preferred)
       headless = not (os.environ.get("DISPLAY") or os.environ.get("BROWSER")) or \
           os.environ.get("SSH_CONNECTION") is not None
       return LoginFlow.PASTE_URL if headless else LoginFlow.LOOPBACK
   ```
2. **Google paste-URL** — di `cli/auth.py` `_google_login`, dukung paste:
   ```python
   from rtrade.llm.auth.login_flows import LoginFlow, auto_flow

   flow_kind = auto_flow(args_flow)
   gflow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=_GOOGLE_SCOPES)
   if flow_kind == LoginFlow.LOOPBACK:
       creds = gflow.run_local_server(port=0)
   else:  # PASTE_URL (VPS-friendly)
       gflow.redirect_uri = "http://localhost:1"  # sengaja gagal-muat; user salin URL-nya
       auth_url, _ = gflow.authorization_url(prompt="consent", access_type="offline")
       logger.info("buka URL ini, login, lalu SALIN URL halaman error & tempel di bawah", url=auth_url)
       redirect_response = input("Tempel URL redirect lengkap di sini: ").strip()
       gflow.fetch_token(authorization_response=redirect_response)
       creds = gflow.credentials
   # ...simpan ADC seperti O4...
   ```
   CATATAN: `fetch_token(authorization_response=<URL lengkap>)` menerima URL berisi `?code=...` —
   itulah "salin halaman error lalu tempel" yang user maksud.
3. **Generic OAuth2 paste-URL** (provider OIDC non-google) — tambah di `OAuth2Provider` (O3):
   ```python
   def build_authorize_url(self, redirect_uri: str, state: str, code_challenge: str) -> str: ...
   async def exchange_pasted_redirect(self, redirect_response: str, redirect_uri: str,
                                       code_verifier: str) -> StoredToken: ...
   ```
   Pakai PKCE (`code_challenge`/`code_verifier`, S256). CLI cetak authorize URL → user tempel
   redirect URL → ekstrak `code` (urllib.parse) → tukar di `token_url`.
4. `cli/auth.py` `login` — tambah arg `--flow {loopback,paste_url,device_code}` (default auto).
   `device_code` → `OAuth2Provider.device_login()` (O3). Pilih implementasi sesuai
   `profile.login_flow` (O8) bila ada, kalau tidak `auto_flow`.

**Test** `tests/unit/test_login_flows.py`:
- `auto_flow("device_code")` → DEVICE_CODE; `auto_flow(None)` dengan `SSH_CONNECTION` set →
  PASTE_URL; tanpa SSH + ada DISPLAY → LOOPBACK (monkeypatch env).
- Generic: `build_authorize_url` berisi `code_challenge` & `redirect_uri`;
  `exchange_pasted_redirect("http://localhost/?code=ABC&state=s", ...)` (respx mock token_url) →
  StoredToken tersimpan; URL tanpa `code` → raise jelas.
**BUKTI**:
```powershell
Select-String -Path src\rtrade\llm\auth\login_flows.py -Pattern "paste_url|device_code|loopback"  # >= 3
Select-String -Path src\rtrade\cli\auth.py -Pattern "fetch_token|exchange_pasted_redirect|--flow"  # >= 2
```
**Commit**: `feat(auth): loopback + paste-URL + device-code login flows (O12)`

---

## O13 — Pilih model per provider OAuth (katalog + selektor)

Tujuan (permintaan user): "setiap OAuth bisa pilih model AI sesuai yang disediakan provider".
Setelah login, user bisa LIHAT model yang tersedia dan TENTUKAN model itu untuk peran
analyst/critic/flagship/backup — tanpa edit kode.

1. Manifest `config/oauth_providers.example.yaml` (O8) — tiap provider boleh punya:
   ```yaml
     google_vertex:
       ...
       models:                       # katalog statis (ditampilkan saat memilih)
         - vertex_ai/gemini-2.5-pro
         - vertex_ai/gemini-2.5-flash
         - vertex_ai/claude-opus-4-1
         - vertex_ai/claude-sonnet-4-5
     azure_openai:
       ...
       models_url_env: RTRADE_AZURE_MODELS_URL   # opsional: endpoint /models (OpenAI-compatible)
     generic_gateway:
       ...
       models_url_env: RTRADE_OAUTH_MODELS_URL    # GET /v1/models → {"data":[{"id":...}]}
   ```
2. File baru `src/rtrade/llm/auth/model_catalog.py`:
   ```python
   async def list_provider_models(profile, credential) -> list[str]:
       """Kembalikan daftar model: dari manifest `models:` (statis) digabung dengan
       hasil GET {models_url} bila ada (format OpenAI /v1/models: body['data'][*]['id']).
       Best-effort: endpoint gagal → kembalikan katalog statis saja. Tidak pernah raise
       hanya karena discovery gagal."""
   ```
3. `cli/auth.py` — sub-command baru:
   - `models --provider <id>` → cetak daftar model (statis + discovery), tandai mana yang sudah
     dipakai route mana.
   - `use --role <analyst|critic|flagship|backup> --provider <id> --model <model>` →
     tulis/aktifkan entri di `llm.model_routes[role]` (model + auth_profile yang menunjuk provider
     itu) ke `config/settings.yaml`. Validasi: model harus ada di katalog provider (atau `--force`),
     provider harus enabled & sudah login (cek token store / ADC), auth_profile cocok.
   - Setelah `use`, cetak konfirmasi: `role=analyst → model=... via provider=... (auth=cli_oauth)`.
4. Integrasi dengan O11 `resolve_model_auth`: route hasil `use` langsung dipakai runtime —
   tidak ada perubahan kode lain yang diperlukan. (Inilah "tambah model via OAuth lalu langsung
   dipakai bot" yang jadi north-star.)
5. Guardrail tetap: `vertex_ai/...` butuh ADC (O4), `openai/...`/`azure/...` butuh profile Azure/OAuth.
   `use` menolak kombinasi model×provider yang tidak mungkin diautentikasi (mis. model vertex tapi
   provider azure) dengan pesan jelas.

**Test** `tests/unit/test_model_catalog.py` + `test_auth_use_cmd.py`:
- `list_provider_models` katalog statis saja (tanpa models_url) → daftar manifest.
- dengan models_url (respx mock `/v1/models` → `{"data":[{"id":"gpt-x"}]}`) → gabungan + dedup.
- discovery error → tetap kembalikan katalog statis (tidak raise).
- `use --role analyst --provider google_vertex --model vertex_ai/gemini-2.5-pro` → settings.yaml
  `model_routes.analyst.model` terisi + `auth_profile` menunjuk profil vertex.
- `use` dengan model di luar katalog tanpa `--force` → error; provider belum login → error
  "jalankan rtrade auth login --provider ...".
**BUKTI**:
```powershell
Select-String -Path src\rtrade\llm\auth\model_catalog.py -Pattern "list_provider_models|models_url"  # >= 2
Select-String -Path src\rtrade\cli\auth.py -Pattern "def .*models|\"use\"|--role"  # >= 2
Select-String -Path config\oauth_providers.example.yaml -Pattern "models:|models_url_env"  # >= 2
```
**Commit**: `feat(auth): per-provider model catalog + `auth use` role selector (O13)`

---

## O14 — Koreksi final: OAuth CLI harus setara API key di runtime

> **PENYELARASAN dengan 0K (wajib):** contoh di O14 di bawah memakai nama lama
> (`ai_auth_providers.example.yaml`, `openai_codex_oauth`, `xai_oauth`). IKUTI 0K, bukan contoh ini:
> manifest tunggal = `config/oauth_providers.example.yaml` (K2); `provider_id` kanonik = `openai_codex`
> / `xai` / `custom_external` (K3, BUKAN varian `*_oauth`); `AuthMaterial` tidak didefinisikan ulang
> (K1). Nama `auth_profile` (mis. `openai_codex_cli_oauth`) bebas — yang dikunci hanya `provider_id`.

Ini revisi eksplisit atas O8-O13: OAuth tidak boleh berhenti sebagai "doctor/status/stub".
Untuk bot, `api_key` dan `cli_oauth` harus punya kedudukan sama sebagai **auth profile**. Bedanya
hanya cara mendapatkan kredensial:

- `api_key`: secret dibaca dari `Secrets`.
- `cli_oauth`: token dibuat/refresh oleh `rtrade auth login --provider <id>`.
- Setelah resolve, keduanya menjadi `AuthMaterial` dan masuk ke `LLMClient` lewat jalur yang sama.

### Target UX

```powershell
python -m rtrade.cli.auth providers
python -m rtrade.cli.auth login --provider openai_codex_oauth --flow paste_url
python -m rtrade.cli.auth login --provider xai_oauth --flow device_code
python -m rtrade.cli.auth models --provider xai_oauth
python -m rtrade.cli.auth use --role analyst --provider xai_oauth --model xai/grok-...
python -m rtrade.cli.auth status --models
```

`status --models` wajib menampilkan:

```text
role      model            auth_profile      auth_type   provider          login_status
analyst   xai/grok-...     xai_cli_oauth     cli_oauth   xai_oauth         logged_in
backup    gemini/...       gemini_api_key    api_key     google_ai_studio  ready
```

### Config final yang diharapkan

`config/settings.yaml`:

```yaml
llm:
  default_auth_profile: gemini_api_key

  auth_profiles:
    gemini_api_key:
      auth_type: api_key
      provider_id: google_ai_studio
      api_key_secret: gemini_api_key_1

    openai_codex_cli_oauth:
      auth_type: cli_oauth
      provider_id: openai_codex_oauth
      token_store_id: openai_codex_oauth

    xai_cli_oauth:
      auth_type: cli_oauth
      provider_id: xai_oauth
      token_store_id: xai_oauth

    custom_hermes_cli_oauth:
      auth_type: cli_oauth
      provider_id: custom_hermes_like
      token_store_id: custom_hermes_like

  model_routes:
    analyst:
      model: xai/grok-...
      auth_profile: xai_cli_oauth
    critic:
      model: openai/gpt-...
      auth_profile: openai_codex_cli_oauth
    backup:
      model: gemini/gemini-2.0-flash
      auth_profile: gemini_api_key
```

`config/ai_auth_providers.example.yaml`:

```yaml
ai_auth_providers:
  openai_codex_oauth:
    label: "OpenAI/Codex OAuth"
    credential_provider: cli_oauth
    login_flow: paste_url
    authorization_url_env: RTRADE_OPENAI_OAUTH_AUTHORIZE_URL
    token_url_env: RTRADE_OPENAI_OAUTH_TOKEN_URL
    client_id_env: RTRADE_OPENAI_OAUTH_CLIENT_ID
    scopes_env: RTRADE_OPENAI_OAUTH_SCOPES
    models:
      - openai/gpt-...

  xai_oauth:
    label: "xAI OAuth"
    credential_provider: cli_oauth
    login_flow: device_code
    device_auth_url_env: RTRADE_XAI_OAUTH_DEVICE_URL
    token_url_env: RTRADE_XAI_OAUTH_TOKEN_URL
    client_id_env: RTRADE_XAI_OAUTH_CLIENT_ID
    scopes_env: RTRADE_XAI_OAUTH_SCOPES
    models_url_env: RTRADE_XAI_MODELS_URL

  custom_hermes_like:
    label: "Custom Hermes-like adapter"
    credential_provider: cli_oauth
    login_flow: external_command
    external_command:
      - "${RTRADE_AUTH_ADAPTER_BIN}"
      - "login"
      - "--provider"
      - "custom"
```

### Implementasi wajib

1. `AuthMaterial` — **JANGAN definisikan ulang di sini.** Pakai bentuk KANONIK dari 0K/K1 (sudah
   punya `auth_type`/`provider_id`/`profile_name`/`bearer_token` dengan default + `merge_into` yang
   memetakan bearer→`kwargs["api_key"]`). O14 cukup MEMAKAI-nya. Log internal tetap menyebut
   `cli_oauth`, bukan "API key".

2. `CliOAuthProvider.resolve()`:
   - load token dari token store.
   - refresh jika hampir expired.
   - jika tidak ada token, raise:
     `Belum login: jalankan python -m rtrade.cli.auth login --provider <provider_id>`.
   - return `AuthMaterial(auth_type="cli_oauth", bearer_token=access_token, ...)`.

3. `ApiKeyProvider.resolve()`:
   - return `AuthMaterial(auth_type="api_key", api_key=secret, ...)`.

4. `LLMClient.complete()`:
   - tidak tahu sumber auth.
   - selalu panggil `credential_provider.resolve()`.
   - selalu `material.merge_into(kwargs)`.
   - tidak ada cabang runtime "OAuth belum supported".

5. `model_router.py`:
   - `resolve_model_auth(cfg, role)` membaca `model_routes[role].auth_profile`.
   - auth profile menentukan provider.
   - provider menentukan cara login.
   - hasilnya langsung dipakai analyst/critic/backup.

6. Budget/cooldown/fallback:
   - identity bukan lagi `api_key`, tapi `auth_identity = f"{auth_type}:{provider_id}:{profile_name}"`.
   - 429 pada OAuth profile cooldown seperti API key.
   - 401/403 pada OAuth profile -> status `login_expired` + fallback backup bila ada.
   - refresh gagal tidak mematikan bot bila backup route tersedia.

7. `external_command` (adapter provider non-standar/Hermes-like) — **WAJIB aman:**
   - command mengembalikan JSON standar di STDOUT:
     ```json
     {"access_token":"...", "refresh_token":"...", "expires_in":3600, "token_type":"Bearer"}
     ```
   - Jalankan dengan `subprocess.run(argv, shell=False, capture_output=True, text=True, timeout=120)`
     — **`shell=False` wajib** (cegah shell injection); validasi `argv[0]` ada & executable dulu.
   - **JANGAN kirim secret via argv** (terlihat di `ps`/process list) — lewat env/stdin saja.
   - **JANGAN pernah `logger`-kan stdout adapter** (berisi token). Log hanya
     `provider_id` + `exit_code` + `expires_at`.
   - stdout bukan-JSON / exit≠0 → `ConfigError` jelas (tanpa mencetak isi stdout).
   - core bot memperlakukan token hasil ini sama seperti API key di runtime (lewat `AuthMaterial`).

### Test wajib

`tests/unit/test_auth_runtime_parity.py`:
- API key profile -> `litellm.acompletion` menerima `api_key`.
- CLI OAuth profile -> `litellm.acompletion` menerima token di `api_key`/bearer slot yang sama.
- `LLMClient` tidak bercabang berdasarkan provider tertentu.
- 429 cooldown bekerja untuk `cli_oauth`.
- 401/403 mengubah status profile menjadi `login_expired`.
- `status --models` tidak pernah mencetak token.

`tests/unit/test_external_command_auth.py`:
- stdout JSON valid -> token tersimpan -> runtime bisa resolve.
- stdout invalid / exit non-zero -> error jelas, token tidak tercetak.
- command missing -> `doctor` gagal dengan pesan actionable.

**BUKTI**:
```powershell
Select-String -Path src\rtrade\llm\auth\base.py -Pattern "auth_type|bearer_token|profile_name"  # >= 3
Select-String -Path src\rtrade\llm\auth\cli_oauth.py -Pattern "class CliOAuthProvider|external_command|resolve"  # >= 3
Select-String -Path src\rtrade\llm\client.py -Pattern "credential_provider.resolve|merge_into"  # >= 2
Select-String -Path src\rtrade\llm\model_router.py -Pattern "auth_profile|resolve_model_auth"  # >= 2
```
**Commit**: `feat(auth): make CLI OAuth a first-class LLM auth profile equal to API keys (O14)`

---

## CHECKLIST AKHIR
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
```
Verifikasi fungsional (butuh kredensial nyata — opsional, oleh USER):
`set GOOGLE_OAUTH_CLIENT_SECRETS=...; python -m rtrade.cli.auth login --provider google` →
set `auth_mode: vertex` + model `vertex_ai/...` → `llm.enabled: true` → scan manual → cek log
panggilan model sukses tanpa API key mentah.

> Default tetap `auth_mode: api_key` (Gemini key untuk testing). OAuth aktif hanya saat user
> mengubah config — tidak ada perubahan perilaku sampai itu dilakukan.
