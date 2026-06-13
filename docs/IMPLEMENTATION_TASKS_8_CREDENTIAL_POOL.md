# IMPLEMENTATION TASKS 8 — CREDENTIAL POOL (Multi-Key + Multi-Akun OAuth + Setup Menu)

> **Untuk: agen pelaksana.** Kerjakan **berurutan A0 → A14**. Satu task = satu commit.
> Dokumen ini ditulis berdasarkan pembacaan langsung kode pada commit `e619664`.
> Setiap potongan kode di sini sudah disesuaikan dengan API yang BENAR-BENAR ada di repo —
> JANGAN improvisasi nama fungsi/field.

---

## 0. ATURAN KERJA (BACA DULU, WAJIB)

### 0.1 Perintah dasar
- Python dijalankan via uv di path absolut:
  `& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run <cmd>`
- Gate per task (SEMUA wajib hijau sebelum commit):
  ```powershell
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest tests/unit -q
  ```
  **mypy WAJIB dijalankan dan WAJIB 0 error.** Di plan-plan sebelumnya kamu dua kali
  lupa mypy dan meninggalkan error. Jangan ulangi.
- BUKTI pakai **PowerShell Select-String** (BUKAN ripgrep/Grep — ripgrep pernah miss file di repo ini).

### 0.2 DILARANG KERAS (keamanan — tidak bisa dinego)
1. **JANGAN** menulis kode yang membaca token dari `~/.codex`, `~/.claude`, `~/.gemini`,
   cookies browser, localStorage, atau session DB aplikasi lain.
2. **JANGAN** menghapus / melemahkan `_FORBIDDEN_KEY_PREFIXES = ("sk-ant-oat",)` di
   `src/rtrade/core/config.py` dan validator `_reject_consumer_oauth`.
3. **JANGAN** me-log nilai `access_token`, `refresh_token`, `client_secret`, `api_key`,
   atau header Authorization. Kalau perlu log identitas key → pakai `_mask()` /
   `_key_id()` yang sudah ada di `src/rtrade/llm/key_manager.py`.
4. **JANGAN** menyentuh guardrails (`GR-*`), floor risk, atau logika sinyal. Plan ini
   murni lapisan kredensial/LLM-auth.
5. **JANGAN** mendefinisikan ulang `AuthMaterial` atau `CredentialProvider`
   (`src/rtrade/llm/auth/base.py` adalah bentuk kanonik — extend, jangan duplikasi).
6. OAuth di sini = **OAuth resmi ke endpoint akses programatik** (Vertex ADC, Azure AD,
   gateway enterprise). BUKAN replay token langganan konsumen.
7. **Codex OAuth yang beredar = login akun ChatGPT konsumen lewat Codex CLI.** Token itu
   terikat akun/langganan ChatGPT dan TIDAK BOLEH diputar ke API bot. Di repo ini statusnya
   harus `capability: disabled_unsupported`, fail-closed, dan tetap ditolak oleh guard
   `sk-ant-oat` / `_reject_consumer_oauth`.
8. **xAI diperlakukan seperti Hermes-style provider yang boleh masuk pool** bila operator
   menyediakan API key resmi atau adapter eksternal yang mengembalikan JSON token standar.
   Jangan samakan xAI dengan Codex consumer OAuth. xAI boleh `api_key` atau
   `external_adapter`; Codex consumer OAuth tetap blocked.

### 0.3 Konteks arsitektur (hasil audit — PENTING untuk paham "kenapa")
- `src/rtrade/llm/key_manager.py` berisi `KeyManager` (rotasi round-robin + cooldown
  Redis + budget). **Saat ini YATIM** — tidak diimpor siapa pun di luar testnya.
  Plan ini MENGAWINKANNYA sebagai mesin cooldown `CredentialPool` (A6).
- `src/rtrade/llm/model_router.py` berisi `resolve_model_auth` (route per-role O11).
  **Saat ini YATIM** — `scan.py` masih membangun client via `_build_cred_provider`
  (auth_mode global). Plan ini mengawinkannya lewat `pool_builder` (A7) + wiring scan (A9).
- `token_store` menyimpan 1 file per `store_id` — `CliOAuthProvider` sudah punya field
  `token_store_id`. Multi-akun = konvensi id `"{provider}__{account}"` (A2-A5).
- `LLMClient.complete()` (`src/rtrade/llm/client.py`) saat ini memegang SATU
  `credential_provider`; retry hanya mengulang kredensial yang sama. A8 menambahkan
  mode pool: gagal 429/auth → kredensial berikutnya.

### 0.4 Definisi "selesai" per task
Task dianggap selesai bila: kode sesuai snippet, BUKTI Select-String keluar persis,
gate 0.1 hijau semua, dan commit dibuat dengan pesan yang ditentukan.

---

## A0 — Provider capability policy: Codex consumer OAuth BLOCKED, xAI Hermes-style ENABLED

**Tujuan:** sebelum membuat pool/fallback, agen wajib mengunci kebijakan provider agar tidak salah
arsitektur:

- `codex_consumer_oauth` / "Codex OAuth" dari Codex CLI = **ChatGPT consumer subscription token**.
  Ini bukan kredensial API backend. Harus fail-closed dan tidak pernah masuk credential pool.
- `openai_api` / `openai_gateway` tetap boleh lewat API key resmi atau OAuth gateway enterprise.
- `xai` harus didukung maksimal seperti Hermes-style: API key resmi + optional external adapter
  (`login_flow: external_command`) yang menghasilkan token JSON. Jika adapter belum dikonfigurasi,
  statusnya `not_configured`, bukan error fatal selama masih ada kredensial lain.

### A0.1 — Manifest final provider capability

**File:** `config/oauth_providers.example.yaml`

GANTI / NORMALISASI blok provider OpenAI/Codex/xAI sehingga minimal berisi struktur ini. Jangan
biarkan `codex_openai` ambiguous.

```yaml
  # -------------------------------------------------------------------------
  # OPENAI / CODEX
  # -------------------------------------------------------------------------
  codex_consumer_oauth:
    label: "Codex CLI / ChatGPT consumer OAuth (BLOCKED)"
    auth_mode: disabled
    capability: disabled_unsupported
    enabled: false
    token_url_env: ""
    client_id_env: ""
    scopes_env: ""
    device_auth_url_env: ""
    note: >
      Codex OAuth yang beredar adalah login akun ChatGPT konsumen lewat Codex CLI.
      Token itu terikat akun/langganan ChatGPT dan tidak boleh diputar menjadi backend API bot.
      Jangan membaca ~/.codex, browser cookies, localStorage, session DB, atau file login tool lain.
      Guard sk-ant-oat dan _reject_consumer_oauth wajib tetap aktif.
    login_flow: blocked
    requires_official_oauth: false
    transport: none
    models: []

  openai_api:
    label: "OpenAI official API key"
    auth_mode: api_key
    capability: api_key
    enabled: true
    token_url_env: ""
    client_id_env: ""
    scopes_env: ""
    device_auth_url_env: ""
    note: "Gunakan OPENAI_API_KEY_1..3; ini jalur resmi untuk API backend."
    login_flow: none
    requires_official_oauth: false
    transport: HTTPS
    models:
      - openai/gpt-4.1
      - openai/gpt-4.1-mini

  openai_gateway:
    label: "OpenAI-compatible OAuth gateway"
    auth_mode: oauth2
    capability: oauth_gateway
    enabled: false
    token_url_env: RTRADE_OPENAI_GATEWAY_TOKEN_URL
    client_id_env: RTRADE_OPENAI_GATEWAY_CLIENT_ID
    scopes_env: RTRADE_OPENAI_GATEWAY_SCOPES
    device_auth_url_env: RTRADE_OPENAI_GATEWAY_DEVICE_URL
    note: "Hanya untuk gateway enterprise/self-hosted yang memang menerbitkan token API."
    login_flow: device_code
    requires_official_oauth: true
    transport: HTTPS
    models_url_env: RTRADE_OPENAI_GATEWAY_MODELS_URL

  # -------------------------------------------------------------------------
  # xAI / GROK
  # -------------------------------------------------------------------------
  xai_api:
    label: "xAI official API key"
    auth_mode: api_key
    capability: api_key
    enabled: true
    token_url_env: ""
    client_id_env: ""
    scopes_env: ""
    device_auth_url_env: ""
    note: "Gunakan XAI_API_KEY_1..3; masuk credential pool sebagai flavor xai."
    login_flow: none
    requires_official_oauth: false
    transport: HTTPS
    models:
      - xai/grok-4
      - xai/grok-3

  xai_hermes:
    label: "xAI Hermes-style external adapter"
    auth_mode: external_command
    capability: external_adapter
    enabled: false
    token_url_env: ""
    client_id_env: ""
    scopes_env: ""
    device_auth_url_env: ""
    note: >
      Jalur untuk adapter lokal/operator yang melakukan login xAI ala Hermes dan mencetak
      JSON token standar ke stdout. Core bot tidak membaca cookie/session/token aplikasi lain.
    login_flow: external_command
    requires_official_oauth: false
    transport: local_process
    external_command:
      - "${RTRADE_XAI_AUTH_ADAPTER_BIN}"
      - "login"
      - "--provider"
      - "xai"
    models_url_env: RTRADE_XAI_MODELS_URL
```

**Catatan penting untuk agen:**
- Jika file sekarang punya blok `codex_openai`, jangan hapus begitu saja bila sudah dipakai test lama.
  Ubah menjadi alias fail-closed:
  ```yaml
  codex_openai:
    label: "Alias lama: Codex consumer OAuth (BLOCKED)"
    auth_mode: disabled
    capability: disabled_unsupported
    enabled: false
    note: "Alias lama; gunakan openai_api/openai_gateway. Jangan aktifkan consumer OAuth."
  ```
- Jika file sekarang punya blok `xai` dengan `capability: disabled_unsupported`, ubah jadi alias ke
  `xai_api` atau `xai_hermes`:
  ```yaml
  xai:
    label: "Alias lama: xAI API key"
    auth_mode: api_key
    capability: api_key
    enabled: true
    note: "Gunakan XAI_API_KEY_1..3; Hermes-style gunakan xai_hermes."
  ```

### A0.2 — Provider profile validator fail-closed

**File:** `src/rtrade/llm/auth/provider_profiles.py`

Tambahkan konstanta:

```python
_BLOCKED_PROVIDER_IDS = {"codex_consumer_oauth", "codex_openai"}
_CONSUMER_TOKEN_SOURCES = (
    ".codex",
    ".claude",
    ".gemini",
    "Cookies",
    "Local Storage",
    "Session Storage",
    "chat.openai.com",
    "chatgpt.com",
    "sk-ant-oat",
)
```

Tambahkan helper:

```python
def is_blocked_consumer_oauth(provider_id: str, profile: OAuthProviderProfile) -> bool:
    """True bila profile merepresentasikan OAuth konsumen yang tidak boleh dipakai bot."""
    if provider_id in _BLOCKED_PROVIDER_IDS:
        return True
    text = " ".join(
        [
            provider_id,
            profile.label,
            profile.note,
            profile.auth_mode,
            profile.capability,
            " ".join(profile.external_command),
        ]
    ).lower()
    return "codex cli" in text or "chatgpt consumer" in text or "consumer oauth" in text
```

Ubah `validate_profile()` supaya menerima `provider_id` opsional. Jika terlalu banyak refactor,
buat fungsi baru dan pakai di semua caller baru:

```python
def validate_provider_profile(provider_id: str, profile: OAuthProviderProfile) -> list[str]:
    issues = validate_profile(profile)
    if is_blocked_consumer_oauth(provider_id, profile):
        if profile.enabled:
            issues.append("codex consumer OAuth wajib disabled_unsupported dan enabled=false")
        if profile.capability != "disabled_unsupported":
            issues.append("codex consumer OAuth wajib capability=disabled_unsupported")
        if profile.auth_mode not in ("disabled", "oauth2"):
            issues.append("codex consumer OAuth tidak boleh punya auth_mode aktif")
    joined = " ".join([profile.note, " ".join(profile.external_command)])
    for needle in _CONSUMER_TOKEN_SOURCES:
        if needle.lower() in joined.lower() and not is_blocked_consumer_oauth(provider_id, profile):
            issues.append(f"profile mengandung sumber token consumer terlarang: {needle}")
    return issues
```

**Wajib:** caller `doctor`, registry, dan pool builder harus memakai
`validate_provider_profile(provider_id, profile)`, bukan hanya `validate_profile(profile)`.

### A0.3 — Registry dan pool harus fail-closed untuk Codex consumer OAuth

**File:** `src/rtrade/llm/auth/registry.py`

Tambahkan check paling awal di `build_provider_from_profile(provider_id, ...)`:

```python
from rtrade.llm.auth.provider_profiles import (
    is_blocked_consumer_oauth,
    validate_provider_profile,
)

# setelah profile di-load:
issues = validate_provider_profile(provider_id, profile)
if is_blocked_consumer_oauth(provider_id, profile):
    raise ConfigError(
        "codex consumer OAuth diblokir: token Codex CLI/ChatGPT consumer tidak boleh "
        "dipakai sebagai API backend. Gunakan OPENAI_API_KEY_* atau openai_gateway."
    )
if issues:
    raise ConfigError(f"profile {provider_id} invalid: {'; '.join(issues)}")
```

**File:** `src/rtrade/llm/pool_builder.py`

Update `_FLAVOR_BY_PROVIDER_ID`:

```python
_FLAVOR_BY_PROVIDER_ID = {
    "google_vertex": "vertex_ai",
    "azure_openai": "azure",
    "openai_api": "openai",
    "openai_gateway": "openai",
    "generic_gateway": "openai",
    "xai": "xai",
    "xai_api": "xai",
    "xai_hermes": "xai",
}
```

Tambahkan skip hard-block sebelum memasukkan `cli_oauth` profile:

```python
from rtrade.llm.auth.provider_profiles import is_blocked_consumer_oauth, load_provider_profiles

provider_profiles = load_provider_profiles()
...
manifest_profile = provider_profiles.get(pid)
if manifest_profile is not None and is_blocked_consumer_oauth(pid, manifest_profile):
    logger.warning("blocked consumer OAuth profile skipped", provider_id=pid)
    continue
```

**Larangan:** jangan mapping `codex_consumer_oauth` atau `codex_openai` ke flavor `openai`.
Kalau ada route yang menunjuk provider itu, harus error jelas, bukan fallback diam-diam.

### A0.4 — xAI Hermes-style external adapter

**File:** `src/rtrade/llm/auth/cli_oauth.py`

Pastikan provider `external_command` bisa dipakai untuk xAI. Jika belum ada, tambahkan method
internal:

```python
async def _run_external_command(self) -> StoredToken:
    """Jalankan adapter eksternal dan simpan token JSON standar.

    Adapter stdout WAJIB JSON:
    {"access_token":"...", "refresh_token":null, "expires_in":3600, "token_type":"Bearer"}
    """
```

Aturan implementasi:
- Command berasal dari manifest `external_command`; jangan hardcode path.
- Jalankan via `asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)`.
- Timeout 120 detik.
- Jika exit code != 0, error boleh mencetak stderr yang sudah disanitasi, tapi jangan stdout token.
- JSON harus punya `access_token`; `refresh_token` opsional; `expires_in` default 3600.
- Simpan ke token store `token_store_id` / `provider_id`.
- Return `AuthMaterial(auth_type="cli_oauth", provider_id="xai_hermes", bearer_token=...)`.

**Jangan** membuat adapter yang membaca `~/.codex`, browser cookie, atau session DB. Adapter eksternal
boleh dimiliki operator, tetapi core repo hanya menerima JSON stdout.

### A0.5 — Test wajib untuk kebijakan ini

Buat file baru `tests/unit/test_provider_capability_policy.py`:

```python
from __future__ import annotations

import pytest

from rtrade.core.errors import ConfigError
from rtrade.llm.auth.provider_profiles import (
    OAuthProviderProfile,
    is_blocked_consumer_oauth,
    validate_provider_profile,
)


def _profile(**overrides: object) -> OAuthProviderProfile:
    data = {
        "label": "test",
        "auth_mode": "oauth2",
        "capability": "disabled_unsupported",
        "enabled": False,
        "note": "",
    }
    data.update(overrides)
    return OAuthProviderProfile(**data)  # type: ignore[arg-type]


def test_codex_consumer_oauth_is_blocked_by_id() -> None:
    profile = _profile(label="Codex CLI / ChatGPT consumer OAuth")
    assert is_blocked_consumer_oauth("codex_consumer_oauth", profile)
    assert validate_provider_profile("codex_consumer_oauth", profile) == []


def test_codex_consumer_oauth_cannot_be_enabled() -> None:
    profile = _profile(enabled=True, capability="oauth_gateway")
    issues = validate_provider_profile("codex_consumer_oauth", profile)
    assert any("enabled=false" in issue for issue in issues)
    assert any("disabled_unsupported" in issue for issue in issues)


def test_codex_alias_is_blocked_too() -> None:
    profile = _profile(label="OpenAI Codex alias")
    assert is_blocked_consumer_oauth("codex_openai", profile)


def test_xai_hermes_external_adapter_is_allowed() -> None:
    profile = _profile(
        label="xAI Hermes-style external adapter",
        auth_mode="external_command",
        capability="external_adapter",
        enabled=True,
        external_command=["/opt/rtrade/bin/xai-auth", "login"],
    )
    assert not is_blocked_consumer_oauth("xai_hermes", profile)
    assert validate_provider_profile("xai_hermes", profile) == []


def test_non_blocked_profile_rejects_consumer_token_sources() -> None:
    profile = _profile(
        label="bad adapter",
        auth_mode="external_command",
        capability="external_adapter",
        enabled=True,
        external_command=["tool", "--read", "~/.codex/auth.json"],
    )
    issues = validate_provider_profile("xai_hermes", profile)
    assert any(".codex" in issue for issue in issues)
```

Tambahkan test registry:

```python
def test_registry_refuses_codex_consumer_oauth() -> None:
    from rtrade.llm.auth.registry import build_provider_from_profile

    with pytest.raises(ConfigError, match="codex consumer OAuth diblokir"):
        build_provider_from_profile("codex_consumer_oauth")
```

Jika `build_provider_from_profile()` butuh path config temporary, pakai manifest tmp_path dengan
provider `codex_consumer_oauth` seperti snippet A0.1, lalu panggil dengan parameter path yang sudah
ada di signature.

### A0.6 — Bukti dan commit

**BUKTI:**
```powershell
Select-String -Path config\oauth_providers.example.yaml -Pattern "codex_consumer_oauth|disabled_unsupported|xai_hermes|external_adapter"
Select-String -Path src\rtrade\llm\auth\provider_profiles.py -Pattern "is_blocked_consumer_oauth|validate_provider_profile|_CONSUMER_TOKEN_SOURCES"
Select-String -Path src\rtrade\llm\pool_builder.py -Pattern "xai_hermes|blocked consumer OAuth profile skipped"
Select-String -Path tests\unit\test_provider_capability_policy.py -Pattern "codex_consumer_oauth|xai_hermes|\\.codex"
```

**Commit:** `feat(auth): fail-closed Codex consumer OAuth and enable xAI Hermes-style capability (A0)`

---

## A1 — Secrets multi-slot API key + `keys_for()`

**Tujuan:** tiap provider bisa punya BANYAK API key untuk fallback.
Slot: Gemini 5, Anthropic 3, OpenAI 3, xAI 3.

**File:** `src/rtrade/core/config.py`

**Langkah 1.** Di class `Secrets`, GANTI blok field LLM (sekarang baris ±187-190):

```python
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    anthropic_api_key_1: str = ""
    openai_api_key_1: str = ""
```

menjadi:

```python
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""
    gemini_api_key_5: str = ""
    anthropic_api_key_1: str = ""
    anthropic_api_key_2: str = ""
    anthropic_api_key_3: str = ""
    openai_api_key_1: str = ""
    openai_api_key_2: str = ""
    openai_api_key_3: str = ""
    xai_api_key_1: str = ""
    xai_api_key_2: str = ""
    xai_api_key_3: str = ""
```

**Langkah 2.** Perluas daftar field pada decorator `@field_validator` milik
`_reject_consumer_oauth` sehingga SEMUA field di atas tervalidasi:

```python
    @field_validator(
        "gemini_api_key_1",
        "gemini_api_key_2",
        "gemini_api_key_3",
        "gemini_api_key_4",
        "gemini_api_key_5",
        "anthropic_api_key_1",
        "anthropic_api_key_2",
        "anthropic_api_key_3",
        "openai_api_key_1",
        "openai_api_key_2",
        "openai_api_key_3",
        "xai_api_key_1",
        "xai_api_key_2",
        "xai_api_key_3",
    )
```

(Isi fungsi validator JANGAN diubah.)

**Langkah 3.** Tambahkan method di class `Secrets` (setelah validator):

```python
    def keys_for(self, family: str) -> list[str]:
        """Daftar API key non-kosong untuk satu family provider, urut slot.

        family: "gemini" | "anthropic" | "openai" | "xai"
        """
        slots: dict[str, list[str]] = {
            "gemini": [
                self.gemini_api_key_1,
                self.gemini_api_key_2,
                self.gemini_api_key_3,
                self.gemini_api_key_4,
                self.gemini_api_key_5,
            ],
            "anthropic": [
                self.anthropic_api_key_1,
                self.anthropic_api_key_2,
                self.anthropic_api_key_3,
            ],
            "openai": [
                self.openai_api_key_1,
                self.openai_api_key_2,
                self.openai_api_key_3,
            ],
            "xai": [self.xai_api_key_1, self.xai_api_key_2, self.xai_api_key_3],
        }
        return [k for k in slots.get(family, []) if k]
```

**Langkah 4.** Test — TAMBAHKAN di `tests/unit/test_config.py` (jangan hapus test lama):

```python
def test_secrets_keys_for_returns_nonempty_in_slot_order() -> None:
    s = Secrets(
        gemini_api_key_1="AIzaAAA",
        gemini_api_key_3="AIzaCCC",
        anthropic_api_key_2="sk-ant-api-xxx",
        xai_api_key_1="xai-111",
    )
    assert s.keys_for("gemini") == ["AIzaAAA", "AIzaCCC"]
    assert s.keys_for("anthropic") == ["sk-ant-api-xxx"]
    assert s.keys_for("openai") == []
    assert s.keys_for("xai") == ["xai-111"]
    assert s.keys_for("unknown") == []


def test_secrets_rejects_consumer_token_on_new_slots() -> None:
    import pytest

    with pytest.raises(Exception, match="FORBIDDEN"):
        Secrets(gemini_api_key_4="sk-ant-oat-xyz")
    with pytest.raises(Exception, match="FORBIDDEN"):
        Secrets(xai_api_key_2="sk-ant-oat-xyz")
```

(Pastikan `Secrets` sudah diimpor di test file itu; kalau belum: `from rtrade.core.config import Secrets`.)

**BUKTI:**
```powershell
Select-String -Path src\rtrade\core\config.py -Pattern "gemini_api_key_5|xai_api_key_3|def keys_for"
# harus muncul ≥3 baris
```

**Commit:** `feat(auth): multi-slot API keys per provider + Secrets.keys_for (A1)`

---

## A2 — token_store: helper multi-akun

**Tujuan:** konvensi store id `"{provider}__{account}"` + bisa menghitung akun yang ada.
File token: `<provider>.json` (akun "default" gaya lama) dan `<provider>__<akun>.json`.

**File:** `src/rtrade/llm/auth/token_store.py`

**Langkah 1.** Tambahkan di bagian atas file (setelah `logger = ...`):

```python
_ACCOUNT_RE = re.compile(r"^[a-z0-9_]{1,32}$")
```

dan tambahkan `import re` di blok import.

**Langkah 2.** Tambahkan dua fungsi publik (letakkan setelah `_fernet()`):

```python
def account_store_id(provider: str, account: str = "default") -> str:
    """Store id kanonik untuk (provider, akun). Akun 'default' = file lama tanpa suffix."""
    if not _ACCOUNT_RE.match(account):
        raise ValueError(
            f"nama akun tidak valid: {account!r} (huruf kecil/angka/underscore, maks 32)"
        )
    if account == "default":
        return provider
    return f"{provider}__{account}"


def list_accounts(provider: str) -> list[str]:
    """Daftar akun yang punya token tersimpan untuk satu provider."""
    accounts: list[str] = []
    token_dir = _token_dir()
    if (token_dir / f"{provider}.json").exists():
        accounts.append("default")
    prefix = f"{provider}__"
    for path in sorted(token_dir.glob(f"{prefix}*.json")):
        accounts.append(path.stem[len(prefix) :])
    return accounts
```

**JANGAN** mengubah signature `save_token` / `load_token` / `delete_token` — mereka
sudah menerima store id arbitrer.

**Langkah 3.** Test — TAMBAHKAN di `tests/unit/test_token_store.py`:

```python
def test_account_store_id_default_and_named() -> None:
    from rtrade.llm.auth.token_store import account_store_id

    assert account_store_id("generic_gateway") == "generic_gateway"
    assert account_store_id("generic_gateway", "default") == "generic_gateway"
    assert account_store_id("generic_gateway", "acc2") == "generic_gateway__acc2"


def test_account_store_id_rejects_path_tricks() -> None:
    import pytest

    from rtrade.llm.auth.token_store import account_store_id

    for bad in ("../evil", "a b", "UPPER", "x" * 33, ""):
        with pytest.raises(ValueError):
            account_store_id("p", bad)


def test_list_accounts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    from rtrade.llm.auth.token_store import StoredToken, list_accounts, save_token

    tok = StoredToken(access_token="a", refresh_token=None, expiry_epoch=1.0, scopes=[])
    save_token("gw", tok)
    save_token("gw__kerja", tok)
    save_token("gw__pribadi", tok)
    save_token("lain__x", tok)
    assert list_accounts("gw") == ["default", "kerja", "pribadi"]
    assert list_accounts("lain") == ["x"]
    assert list_accounts("kosong") == []
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\llm\auth\token_store.py -Pattern "def account_store_id|def list_accounts"
```

**Commit:** `feat(auth): token store multi-account helpers (A2)`

---

## A3 — `OAuth2Provider.store_id` (token per akun)

**Tujuan:** satu provider OAuth bisa login >1 akun; token tersimpan di file berbeda.

**File:** `src/rtrade/llm/auth/oauth2.py`

**Langkah 1.** Tambahkan field di dataclass `OAuth2Provider` (SETELAH `device_auth_url`):

```python
    store_id: str = ""  # A3: token store id; kosong = provider_id (akun default)
```

**Langkah 2.** Tambahkan property (setelah `mode`):

```python
    @property
    def _sid(self) -> str:
        return self.store_id or self.provider_id
```

**Langkah 3.** Ganti SEMUA pemakaian store di file ini:
- `resolve()`: `load_token(self.provider_id)` → `load_token(self._sid)`
  dan `save_token(self.provider_id, token)` → `save_token(self._sid, token)`
- `device_login()`: `save_token(self.provider_id, tok)` → `save_token(self._sid, tok)`
- `exchange_pasted_redirect()`: `save_token(self.provider_id, tok)` → `save_token(self._sid, tok)`

Pesan error di `resolve()` JANGAN diubah selain itu.

**Langkah 4.** Test — TAMBAHKAN di `tests/unit/test_oauth2.py`:

```python
def test_oauth2_store_id_separates_accounts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    import time

    from rtrade.llm.auth.oauth2 import OAuth2Provider
    from rtrade.llm.auth.token_store import StoredToken, save_token

    save_token(
        "gw__acc2",
        StoredToken(
            access_token="tok-acc2",
            refresh_token=None,
            expiry_epoch=time.time() + 3600,
            scopes=[],
        ),
    )
    prov = OAuth2Provider(
        provider_id="gw",
        token_url="https://example.invalid/token",
        client_id="cid",
        store_id="gw__acc2",
    )
    import asyncio

    material = asyncio.run(prov.resolve())
    assert material.bearer_token == "tok-acc2"
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\llm\auth\oauth2.py -Pattern "store_id|_sid"
# _sid harus dipakai di resolve, device_login, exchange_pasted_redirect (≥5 baris)
```

**Commit:** `feat(auth): OAuth2Provider per-account store_id (A3)`

---

## A4 — `VertexProvider.credentials_path` (multi-akun Google)

**Tujuan:** tiap akun Google punya file ADC sendiri; litellm menerima
`vertex_credentials` (path file JSON kredensial).

**File:** `src/rtrade/llm/auth/vertex.py`

**Langkah 1.** Ganti dataclass `VertexProvider` menjadi:

```python
@dataclass(frozen=True, slots=True)
class VertexProvider(CredentialProvider):
    project: str
    location: str = "us-central1"
    credentials_path: str = ""  # A4: ADC per-akun; kosong = ADC default environment

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
```

Tambahkan `from typing import Any` di import.

**Langkah 2.** Tambahkan helper direktori ADC per-akun (di akhir file, setelah `has_adc`):

```python
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
```

Tambahkan `from pathlib import Path` di import.

**Langkah 3.** Test — TAMBAHKAN di `tests/unit/test_vertex_provider.py`:

```python
def test_vertex_credentials_path_in_extra_kwargs() -> None:
    import asyncio

    from rtrade.llm.auth.vertex import VertexProvider

    prov = VertexProvider(project="proj-x", credentials_path="/tmp/google__acc1.json")
    material = asyncio.run(prov.resolve())
    assert material.extra_kwargs["vertex_credentials"] == "/tmp/google__acc1.json"
    assert material.extra_kwargs["vertex_project"] == "proj-x"

    prov_default = VertexProvider(project="proj-x")
    material2 = asyncio.run(prov_default.resolve())
    assert "vertex_credentials" not in material2.extra_kwargs


def test_adc_account_helpers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path))
    from rtrade.llm.auth.vertex import adc_path_for, list_adc_accounts

    assert list_adc_accounts() == []
    adc_path_for("kerja").write_text("{}", encoding="utf-8")
    adc_path_for("pribadi").write_text("{}", encoding="utf-8")
    assert list_adc_accounts() == ["kerja", "pribadi"]
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\llm\auth\vertex.py -Pattern "credentials_path|vertex_credentials|def adc_path_for|def list_adc_accounts"
```

**Commit:** `feat(auth): Vertex per-account ADC via credentials_path (A4)`

---

## A5 — CLI auth: `--account` di login/logout/status + subcommand `accounts`

**File:** `src/rtrade/cli/auth.py`

**Langkah 1.** `main()` — tambahkan argumen `--account` (default `"default"`) pada
parser `login` dan `logout`, dan parser baru `accounts`:

```python
    login.add_argument(
        "--account",
        default="default",
        help="Label akun (multi-akun per provider, mis. 'kerja', 'pribadi')",
    )
```

```python
    logout.add_argument("--account", default="default")
```

```python
    accounts = sub.add_parser("accounts", help="List akun tersimpan per provider")
    accounts.add_argument("--provider", required=True)
```

dan daftarkan di dispatch: `"accounts": _cmd_accounts,`

**Langkah 2.** Fungsi baru:

```python
def _cmd_accounts(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.token_store import list_accounts

    accs = list_accounts(args.provider)
    if args.provider in ("google", "google_vertex"):
        from rtrade.llm.auth.vertex import list_adc_accounts

        accs = sorted(set(accs) | set(list_adc_accounts()))
    if not accs:
        print(f"{args.provider}: belum ada akun tersimpan")  # noqa: T201
        return
    for a in accs:
        print(f"{args.provider}: {a}")  # noqa: T201
```

**Langkah 3.** `_google_login` — ganti signature dan penyimpanan ADC menjadi per-akun:

```python
def _google_login(flow_override: str | None = None, account: str = "default") -> None:
```

dan GANTI blok penyimpanan ADC (mulai `import json` sampai `logger.info(...)` di akhir
fungsi) menjadi:

```python
    # Simpan ADC per-akun; akun 'default' juga ditulis ke well-known path supaya
    # google-auth & litellm lama tetap bekerja tanpa konfigurasi.
    import json

    from rtrade.llm.auth.token_store import account_store_id  # validasi nama akun
    from rtrade.llm.auth.vertex import adc_path_for

    account_store_id("google", account)  # raise bila nama akun tidak valid
    payload = json.dumps(
        {
            "type": "authorized_user",
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "refresh_token": creds.refresh_token,
        }
    )
    per_account = adc_path_for(account)
    per_account.write_text(payload, encoding="utf-8")
    if account == "default":
        from pathlib import Path

        adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
        adc.parent.mkdir(parents=True, exist_ok=True)
        adc.write_text(payload, encoding="utf-8")
    logger.info("google login sukses — ADC tersimpan", account=account, path=str(per_account))
```

**Langkah 4.** `_cmd_login` — teruskan akun:
- panggilan google: `_google_login(flow_override=flow, account=args.account)`
- cabang `generic`:
  ```python
    elif args.provider == "generic":
        from rtrade.llm.auth.registry import build_generic_oauth_from_env
        from rtrade.llm.auth.token_store import account_store_id

        prov = build_generic_oauth_from_env()
        prov.store_id = account_store_id(prov.provider_id, args.account)
        asyncio.run(prov.device_login())
  ```
- cabang profil Hermes (else): setelah `provider = build_provider_from_profile(args.provider)`
  tambahkan:
  ```python
        from rtrade.llm.auth.token_store import account_store_id

        provider.store_id = account_store_id(args.provider, args.account)
  ```

(`OAuth2Provider` adalah dataclass non-frozen — assignment field sah.)

**Langkah 5.** `_cmd_logout` — hapus token per akun:

```python
def _cmd_logout(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.token_store import account_store_id, delete_token

    sid = account_store_id(args.provider, getattr(args, "account", "default"))
    if delete_token(sid):
        print(f"Token {sid} dihapus.")  # noqa: T201
    else:
        print(f"Tidak ada token untuk {sid}.")  # noqa: T201
```

**Langkah 6.** `_cmd_status` — tampilkan SEMUA akun per provider:

```python
def _cmd_status(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.token_store import account_store_id, list_accounts, load_token

    providers = [args.provider] if args.provider else _all_provider_ids()
    for pid in providers:
        accs = list_accounts(pid) or ["default"]
        for acc in accs:
            tok = load_token(account_store_id(pid, acc))
            label = f"{pid}[{acc}]"
            if tok is None:
                print(f"{label}: not_logged_in")  # noqa: T201
            else:
                import datetime

                exp = datetime.datetime.fromtimestamp(tok.expiry_epoch, tz=datetime.UTC)
                print(  # noqa: T201
                    f"{label}: logged_in, expires={exp.isoformat()}, scopes={tok.scopes}"
                )
```

**Langkah 7.** Test baru `tests/unit/test_cli_auth_accounts.py`:

```python
"""CLI auth multi-account: parsing & store-id wiring (A5)."""

from __future__ import annotations

import time


def test_logout_uses_account_store_id(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    from argparse import Namespace

    from rtrade.cli.auth import _cmd_logout
    from rtrade.llm.auth.token_store import StoredToken, save_token

    save_token(
        "gw__kerja",
        StoredToken(access_token="t", refresh_token=None, expiry_epoch=1.0, scopes=[]),
    )
    _cmd_logout(Namespace(provider="gw", account="kerja"))
    out = capsys.readouterr().out
    assert "gw__kerja dihapus" in out


def test_status_lists_all_accounts(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    from argparse import Namespace

    from rtrade.cli.auth import _cmd_status
    from rtrade.llm.auth.token_store import StoredToken, save_token

    tok = StoredToken(
        access_token="t", refresh_token=None, expiry_epoch=time.time() + 60, scopes=[]
    )
    save_token("gw", tok)
    save_token("gw__kerja", tok)
    _cmd_status(Namespace(provider="gw"))
    out = capsys.readouterr().out
    assert "gw[default]: logged_in" in out
    assert "gw[kerja]: logged_in" in out
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\cli\auth.py -Pattern "account_store_id|--account|_cmd_accounts|adc_path_for"
# ≥6 baris
```

**Commit:** `feat(auth): CLI multi-account login/logout/status/accounts (A5)`

---

## A6 — `CredentialPool` (mengawinkan KeyManager yatim)

**Tujuan:** pool kredensial terurut dengan rotasi + cooldown. Mesin cooldown =
`KeyManager` yang SUDAH ADA (`src/rtrade/llm/key_manager.py`) — dipakai apa adanya,
"keys" yang dirotasi adalah **cred_id** (string), bukan API key mentah.

**File BARU:** `src/rtrade/llm/auth/pool.py`

```python
"""Credential pool — rotasi + fallback lintas API key & akun OAuth (A6).

Satu pool berisi kredensial terurut (PooledCredential). Saat satu kredensial kena
rate limit / gagal auth, ia masuk cooldown (mesin: KeyManager) dan pemanggil pindah
ke kredensial berikutnya. cred_id yang dirotasi adalah label internal — BUKAN secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from rtrade.llm.auth.base import CredentialProvider
from rtrade.llm.key_manager import AllKeysExhaustedError, KeyManager

logger = structlog.get_logger(__name__)

_POOL_KEY = "llm_pool"


class AllCredentialsExhaustedError(Exception):
    """Semua kredensial di pool sedang cooldown / sudah dicoba."""


@dataclass(frozen=True, slots=True)
class PooledCredential:
    """Satu kredensial siap pakai di dalam pool.

    flavor = prefix model litellm yang diterima kredensial ini:
    "gemini" | "vertex_ai" | "anthropic" | "openai" | "azure" | "xai"
    """

    cred_id: str
    flavor: str
    credential: CredentialProvider


def model_flavor(model: str) -> str:
    """Prefix provider dari nama model litellm ('gemini/x' → 'gemini')."""
    return model.split("/", 1)[0] if "/" in model else ""


def translate_model(model: str, flavor: str) -> str | None:
    """Nama model yang harus dipakai kredensial ber-flavor tsb; None = tak kompatibel.

    Translasi mekanis hanya gemini ↔ vertex_ai (katalog model Google sama).
    """
    prefix = model_flavor(model)
    if not prefix:
        return None
    if prefix == flavor:
        return model
    pair = {prefix, flavor}
    if pair == {"gemini", "vertex_ai"}:
        return f"{flavor}/{model.split('/', 1)[1]}"
    return None


def classify_llm_error(exc: BaseException) -> str:
    """'rate_limit' | 'auth' | 'other' — berbasis nama exception + isi pesan."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if (
        name == "RateLimitError"
        or "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "quota" in msg
    ):
        return "rate_limit"
    if (
        name in ("AuthenticationError", "PermissionDeniedError")
        or "401" in msg
        or "403" in msg
        or "unauthorized" in msg
        or "invalid api key" in msg
        or "belum login" in msg
    ):
        return "auth"
    return "other"


class CredentialPool:
    """Pool kredensial terurut dengan rotasi round-robin + cooldown."""

    def __init__(
        self,
        entries: list[PooledCredential],
        *,
        redis_client: Any | None = None,
        cooldown_seconds: int = 60,
    ) -> None:
        if not entries:
            raise ValueError("CredentialPool tidak boleh kosong")
        ids = [e.cred_id for e in entries]
        if len(set(ids)) != len(ids):
            raise ValueError(f"cred_id duplikat di pool: {ids}")
        self._entries = list(entries)
        self._by_id = {e.cred_id: e for e in entries}
        self._km = KeyManager(
            redis_client,
            {_POOL_KEY: ids},
            cooldown_seconds=cooldown_seconds,
        )

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[PooledCredential]:
        return list(self._entries)

    async def acquire(self, exclude: set[str] | None = None) -> PooledCredential:
        """Kredensial berikutnya yang tidak cooldown dan tidak di-exclude."""
        skip = exclude or set()
        for _ in range(self.size):
            try:
                cid = await self._km.get_next_key(_POOL_KEY)
            except AllKeysExhaustedError as exc:
                raise AllCredentialsExhaustedError(str(exc)) from exc
            if cid not in skip:
                return self._by_id[cid]
        raise AllCredentialsExhaustedError(
            f"semua {self.size} kredensial sudah dicoba di panggilan ini"
        )

    async def report_failure(self, cred_id: str, *, kind: str = "rate_limit") -> None:
        """Tandai kredensial gagal → cooldown. kind hanya untuk logging."""
        logger.warning("credential failure — cooldown", cred_id=cred_id, kind=kind)
        await self._km.report_rate_limit(_POOL_KEY, cred_id)
```

**CATATAN untuk agen:** `KeyManager.report_rate_limit` me-log argumen kedua via
`_mask()` — aman karena yang kita kirim adalah `cred_id` (label, bukan secret).

**Test BARU:** `tests/unit/test_credential_pool.py`

```python
"""CredentialPool: rotasi, cooldown, translate, classify (A6)."""

from __future__ import annotations

import asyncio

import pytest

from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.pool import (
    AllCredentialsExhaustedError,
    CredentialPool,
    PooledCredential,
    classify_llm_error,
    model_flavor,
    translate_model,
)


def _pool(n: int = 3) -> CredentialPool:
    entries = [
        PooledCredential(cred_id=f"k{i}", flavor="gemini", credential=ApiKeyProvider(f"AIza{i}"))
        for i in range(n)
    ]
    return CredentialPool(entries)


def test_acquire_round_robin() -> None:
    pool = _pool(3)

    async def run() -> list[str]:
        return [(await pool.acquire()).cred_id for _ in range(4)]

    assert asyncio.run(run()) == ["k0", "k1", "k2", "k0"]


def test_failure_puts_credential_in_cooldown() -> None:
    pool = _pool(2)

    async def run() -> list[str]:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id)
        return [(await pool.acquire()).cred_id, (await pool.acquire()).cred_id]

    # k0 cooldown → hanya k1 yang muncul
    assert asyncio.run(run()) == ["k1", "k1"]


def test_all_cooldown_raises() -> None:
    pool = _pool(1)

    async def run() -> None:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id)
        await pool.acquire()

    with pytest.raises(AllCredentialsExhaustedError):
        asyncio.run(run())


def test_exclude_skips_tried() -> None:
    pool = _pool(2)

    async def run() -> str:
        return (await pool.acquire(exclude={"k0"})).cred_id

    assert asyncio.run(run()) == "k1"


def test_empty_and_duplicate_rejected() -> None:
    with pytest.raises(ValueError):
        CredentialPool([])
    e = PooledCredential(cred_id="x", flavor="gemini", credential=ApiKeyProvider("k"))
    with pytest.raises(ValueError):
        CredentialPool([e, e])


def test_model_flavor_and_translate() -> None:
    assert model_flavor("gemini/gemini-2.5-pro") == "gemini"
    assert translate_model("gemini/gemini-2.5-pro", "gemini") == "gemini/gemini-2.5-pro"
    assert translate_model("gemini/gemini-2.5-pro", "vertex_ai") == "vertex_ai/gemini-2.5-pro"
    assert translate_model("vertex_ai/gemini-2.5-pro", "gemini") == "gemini/gemini-2.5-pro"
    assert translate_model("gemini/gemini-2.5-pro", "anthropic") is None
    assert translate_model("tanpa-prefix", "gemini") is None


def test_classify_llm_error() -> None:
    class RateLimitError(Exception): ...

    class AuthenticationError(Exception): ...

    assert classify_llm_error(RateLimitError("x")) == "rate_limit"
    assert classify_llm_error(Exception("HTTP 429 too many requests")) == "rate_limit"
    assert classify_llm_error(Exception("RESOURCE_EXHAUSTED: quota")) == "rate_limit"
    assert classify_llm_error(AuthenticationError("bad")) == "auth"
    assert classify_llm_error(Exception("401 Unauthorized")) == "auth"
    assert classify_llm_error(RuntimeError("gw: tidak ada token valid. Belum login")) == "auth"
    assert classify_llm_error(Exception("connection reset")) == "other"
```

**BUKTI (anti-yatim KeyManager):**
```powershell
Select-String -Path src\rtrade\llm\auth\pool.py -Pattern "from rtrade.llm.key_manager import"
# WAJIB 1 baris — KeyManager kini dipakai runtime
```

**Commit:** `feat(auth): CredentialPool with KeyManager cooldown engine (A6)`

---

## A7 — `pool_builder` (auto-pool dari semua kredensial yang ada) + wiring `resolve_model_auth`

**Tujuan:** SATU pool untuk seluruh scan, dibangun OTOMATIS dari: route per-role
(model_routes/auth_profiles) + semua API key terisi + semua akun OAuth tersimpan +
semua akun ADC Vertex. Tanpa konfigurasi tambahan, fallback langsung jalan.

### Langkah 1 — `model_router.py`: tambah dua fungsi publik

**File:** `src/rtrade/llm/model_router.py` — tambahkan di akhir file:

```python
def resolve_role_model(cfg: AppConfig, role: str) -> str:
    """Nama model untuk satu role — dari model_routes bila ada, else field lama."""
    routes = cfg.settings.llm.model_routes
    if routes and role in routes and isinstance(routes[role], dict):
        model = str(routes[role].get("model", ""))
        if model:
            return model
    model_map = {
        "analyst": cfg.settings.llm.analyst_model,
        "critic": cfg.settings.llm.critic_model,
        "backup": cfg.settings.llm.analyst_model,
        "flagship": cfg.settings.llm.flagship_model,
    }
    return model_map.get(role, cfg.settings.llm.analyst_model)


def build_profile_credential(cfg: AppConfig, profile_name: str) -> CredentialProvider:
    """Public wrapper supaya pool_builder bisa membangun provider per auth_profile."""
    profiles = cfg.settings.llm.auth_profiles
    if profile_name not in profiles or not isinstance(profiles[profile_name], dict):
        raise ConfigError(f"auth_profiles.{profile_name} tidak ditemukan/invalid")
    return _build_provider_for_profile(profiles[profile_name], profile_name, cfg)
```

### Langkah 2 — `_build_provider_for_profile`: dukung `account` & `adc_account`

Masih di `model_router.py`, dalam `_build_provider_for_profile`:

- Cabang `cli_oauth` — GANTI menjadi:

```python
    if auth_type == "cli_oauth":
        from rtrade.llm.auth.cli_oauth import CliOAuthProvider
        from rtrade.llm.auth.token_store import account_store_id

        pid = profile.get("provider_id", "")
        account = profile.get("account", "default")
        default_store = account_store_id(pid, account) if pid else ""
        return CliOAuthProvider(
            provider_id=pid,
            token_store_id=profile.get("token_store_id", default_store),
        )
```

- Cabang `vertex` — GANTI menjadi:

```python
    if auth_type == "vertex" or profile.get("credential_provider") == "vertex":
        from rtrade.llm.auth.vertex import VertexProvider, adc_path_for

        adc_account = profile.get("adc_account", "")
        cred_path = str(adc_path_for(adc_account)) if adc_account else ""
        return VertexProvider(
            project=profile.get("vertex_project", cfg.settings.llm.vertex_project),
            location=profile.get("vertex_location", cfg.settings.llm.vertex_location),
            credentials_path=cred_path,
        )
```

### Langkah 3 — File BARU `src/rtrade/llm/pool_builder.py`

```python
"""Membangun CredentialPool untuk scan dari SEMUA kredensial yang tersedia (A7).

Urutan prioritas (fallback berjalan dari atas ke bawah):
1. Kredensial route per-role (model_routes/auth_profiles — atau legacy gemini key 1).
2. Semua API key terisi (Secrets.keys_for), family model analyst didahulukan.
3. Semua akun OAuth CLI yang tersimpan di token store (per auth_profile cli_oauth).
4. Semua akun ADC Vertex (~/.rtrade/adc) bila llm.vertex_project diisi.

Dedup: API key identik / store id identik hanya masuk sekali.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog

from rtrade.core.config import AppConfig
from rtrade.core.errors import ConfigError
from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.pool import CredentialPool, PooledCredential, model_flavor
from rtrade.llm.model_router import resolve_model_auth, resolve_role_model

logger = structlog.get_logger(__name__)

_ROLES = ("analyst", "critic", "flagship")

# provider_id manifest → flavor model litellm
_FLAVOR_BY_PROVIDER_ID = {
    "google_vertex": "vertex_ai",
    "azure_openai": "azure",
    "openai_api": "openai",
    "openai_gateway": "openai",
    "generic_gateway": "openai",
    "xai": "xai",
    "xai_api": "xai",
    "xai_hermes": "xai",
}


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def build_scan_pool(
    cfg: AppConfig,
    *,
    redis_client: Any | None = None,
) -> CredentialPool:
    """Pool tunggal untuk seluruh pipeline scan (analyst+critic+flagship)."""
    entries: list[PooledCredential] = []
    seen_ids: set[str] = set()
    seen_api_fp: set[str] = set()
    seen_stores: set[str] = set()

    def add(entry: PooledCredential) -> None:
        if entry.cred_id in seen_ids:
            return
        seen_ids.add(entry.cred_id)
        entries.append(entry)

    primary_flavor = model_flavor(resolve_role_model(cfg, "analyst"))

    # --- 1. API keys, family model analyst dulu, sisanya menyusul ---
    families = ["gemini", "anthropic", "openai", "xai"]
    families.sort(key=lambda f: f != primary_flavor)
    for fam in families:
        for i, key in enumerate(cfg.secrets.keys_for(fam), start=1):
            fp = _fingerprint(key)
            if fp in seen_api_fp:
                continue
            seen_api_fp.add(fp)
            add(
                PooledCredential(
                    cred_id=f"{fam}_key_{i}",
                    flavor=fam,
                    credential=ApiKeyProvider(api_key=key),
                )
            )

    # --- 2. Kredensial route per-role (non-api_key saja; api_key sudah tercakup) ---
    for role in _ROLES:
        try:
            ra = resolve_model_auth(cfg, role)
        except ConfigError as exc:
            logger.warning("route auth invalid — dilewati", role=role, error=str(exc))
            continue
        if ra.credential_provider.mode == "api_key":
            continue  # gemini_api_key_1 dkk sudah masuk di blok 1
        add(
            PooledCredential(
                cred_id=f"route_{ra.auth_profile}",
                flavor=model_flavor(ra.model),
                credential=ra.credential_provider,
            )
        )

    # --- 3. Akun OAuth CLI tersimpan, per auth_profile cli_oauth ---
    from rtrade.llm.auth.cli_oauth import CliOAuthProvider
    from rtrade.llm.auth.provider_profiles import (
        is_blocked_consumer_oauth,
        load_provider_profiles,
    )
    from rtrade.llm.auth.token_store import account_store_id, list_accounts

    provider_profiles = load_provider_profiles()
    for pname, prof in cfg.settings.llm.auth_profiles.items():
        if not isinstance(prof, dict) or prof.get("auth_type") != "cli_oauth":
            continue
        if not prof.get("enabled", True):
            continue
        pid = str(prof.get("provider_id", ""))
        if not pid:
            continue
        manifest_profile = provider_profiles.get(pid)
        if manifest_profile is not None and is_blocked_consumer_oauth(pid, manifest_profile):
            logger.warning("blocked consumer OAuth profile skipped", provider_id=pid)
            continue
        flavor = str(prof.get("flavor", "")) or _FLAVOR_BY_PROVIDER_ID.get(pid, "openai")
        for acc in list_accounts(pid):
            store = account_store_id(pid, acc)
            if store in seen_stores:
                continue
            seen_stores.add(store)
            add(
                PooledCredential(
                    cred_id=f"{pname}__{acc}",
                    flavor=flavor,
                    credential=CliOAuthProvider(provider_id=pid, token_store_id=store),
                )
            )

    # --- 4. Akun ADC Vertex (multi-akun Google) ---
    if cfg.settings.llm.vertex_project:
        from rtrade.llm.auth.vertex import VertexProvider, adc_path_for, list_adc_accounts

        for acc in list_adc_accounts():
            add(
                PooledCredential(
                    cred_id=f"vertex__{acc}",
                    flavor="vertex_ai",
                    credential=VertexProvider(
                        project=cfg.settings.llm.vertex_project,
                        location=cfg.settings.llm.vertex_location,
                        credentials_path=str(adc_path_for(acc)),
                    ),
                )
            )

    if not entries:
        raise ConfigError(
            "Tidak ada kredensial LLM. Isi GEMINI_API_KEY_1 (atau key lain) di .env, "
            "atau login OAuth: python -m rtrade.cli.auth login --provider <id>"
        )

    logger.info(
        "credential pool built",
        n=len(entries),
        cred_ids=[e.cred_id for e in entries],
        primary_flavor=primary_flavor,
    )
    return CredentialPool(entries, redis_client=redis_client)
```

**CATATAN:** log `cred_ids` aman — berisi label, bukan secret. JANGAN pernah log key/token.

### Langkah 4 — Test BARU `tests/unit/test_pool_builder.py`

```python
"""pool_builder: auto-pool dari Secrets + token store + ADC (A7)."""

from __future__ import annotations

import pytest

from rtrade.core.errors import ConfigError


def _cfg(monkeypatch, tmp_path, **secrets_overrides):
    """AppConfig minimal via fixture config yang dipakai test_config (load default)."""
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path / "tok"))
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path / "adc"))
    from rtrade.core.config import AppConfig, Secrets

    cfg = AppConfig.load()
    object.__setattr__(cfg, "secrets", Secrets(**secrets_overrides))
    return cfg


def test_pool_multi_gemini_keys(monkeypatch, tmp_path) -> None:
    cfg = _cfg(
        monkeypatch,
        tmp_path,
        gemini_api_key_1="AIza1",
        gemini_api_key_2="AIza2",
        gemini_api_key_3="AIza3",
    )
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert ids[:3] == ["gemini_key_1", "gemini_key_2", "gemini_key_3"]


def test_pool_dedups_identical_keys(monkeypatch, tmp_path) -> None:
    cfg = _cfg(
        monkeypatch,
        tmp_path,
        gemini_api_key_1="AIzaSAMA",
        gemini_api_key_2="AIzaSAMA",
    )
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    assert [e.cred_id for e in pool.entries] == ["gemini_key_1"]


def test_pool_includes_oauth_accounts(monkeypatch, tmp_path) -> None:
    cfg = _cfg(monkeypatch, tmp_path, gemini_api_key_1="AIza1")
    cfg.settings.llm.auth_profiles["gw_oauth"] = {
        "auth_type": "cli_oauth",
        "provider_id": "generic_gateway",
        "enabled": True,
    }
    from rtrade.llm.auth.token_store import StoredToken, save_token

    tok = StoredToken(access_token="t", refresh_token=None, expiry_epoch=1.0, scopes=[])
    save_token("generic_gateway", tok)
    save_token("generic_gateway__cadangan", tok)

    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert "gw_oauth__default" in ids
    assert "gw_oauth__cadangan" in ids
    by_id = {e.cred_id: e for e in pool.entries}
    assert by_id["gw_oauth__default"].flavor == "openai"


def test_pool_includes_vertex_adc_accounts(monkeypatch, tmp_path) -> None:
    cfg = _cfg(monkeypatch, tmp_path, gemini_api_key_1="AIza1")
    cfg.settings.llm.vertex_project = "proj-x"
    from rtrade.llm.auth.vertex import adc_path_for

    adc_path_for("kerja").write_text("{}", encoding="utf-8")
    from rtrade.llm.pool_builder import build_scan_pool

    pool = build_scan_pool(cfg)
    ids = [e.cred_id for e in pool.entries]
    assert "vertex__kerja" in ids
    assert {e.flavor for e in pool.entries if e.cred_id == "vertex__kerja"} == {"vertex_ai"}


def test_pool_empty_raises_config_error(monkeypatch, tmp_path) -> None:
    cfg = _cfg(monkeypatch, tmp_path)  # tanpa key sama sekali
    from rtrade.llm.pool_builder import build_scan_pool

    with pytest.raises(ConfigError):
        build_scan_pool(cfg)
```

**PERHATIAN:** jika `AppConfig.load()` butuh path config / env tertentu di test,
lihat bagaimana `tests/unit/test_config.py` memuat config dan tiru pola yang sama
(JANGAN bikin mekanisme baru). Jika `cfg.settings.llm.auth_profiles` immutable
(pydantic model frozen), gunakan `cfg.settings.llm.auth_profiles.update({...})` —
dict default_factory tetap mutable.

**BUKTI (anti-yatim resolve_model_auth):**
```powershell
Select-String -Path src\rtrade\llm\pool_builder.py -Pattern "from rtrade.llm.model_router import resolve_model_auth, resolve_role_model"
# WAJIB 1 baris
```

**Commit:** `feat(auth): auto credential pool builder, wires model_router (A7)`

---

## A8 — `LLMClient`: mode pool (fallback antar kredensial)

**File:** `src/rtrade/llm/client.py`

**Langkah 1.** Import baru (taruh setelah import `litellm`/`structlog` yang ada):

```python
from rtrade.llm.auth.pool import (
    AllCredentialsExhaustedError,
    CredentialPool,
    classify_llm_error,
    translate_model,
)
```

**Langkah 2.** Field baru di dataclass `LLMClient` (setelah `credential_provider`):

```python
    credential_pool: CredentialPool | None = None
```

**Langkah 3.** GANTI SELURUH method `complete()` dengan versi ini (perilaku lama
dipertahankan bila `credential_pool is None`):

```python
    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> LLMCallResult:
        """Call LLM with optional structured output.

        Bila credential_pool diset: gagal rate-limit/auth pada satu kredensial →
        kredensial itu cooldown dan panggilan pindah ke kredensial berikutnya.
        """
        temp = temperature if temperature is not None else self.temperature
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        base_kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": temp,
            "timeout": self.timeout,
        }
        if response_schema is not None:
            base_kwargs["response_format"] = {"type": "json_object"}
            schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
            messages[0]["content"] += (
                f"\n\nYou MUST respond with valid JSON matching this schema:\n"
                f"```json\n{schema_json}\n```"
            )

        # --- jalur lama (tanpa pool) — TIDAK berubah perilakunya ---
        if self.credential_pool is None:
            kwargs = dict(base_kwargs)
            kwargs["model"] = model
            if self.credential_provider is not None:
                material = await self.credential_provider.resolve()
                material.merge_into(kwargs)
            elif self.api_key:
                kwargs["api_key"] = self.api_key
            return await self._attempt_loop(kwargs, model, response_schema)

        # --- jalur pool: fallback antar kredensial ---
        pool = self.credential_pool
        tried: set[str] = set()
        last_error: Exception | None = None
        while True:
            try:
                cred = await pool.acquire(exclude=tried)
            except AllCredentialsExhaustedError as exc:
                raise LLMUnavailableError(
                    f"semua kredensial gagal/cooldown untuk {model}: {last_error}"
                ) from exc
            tried.add(cred.cred_id)

            actual_model = translate_model(model, cred.flavor)
            if actual_model is None:
                logger.debug(
                    "credential flavor tidak kompatibel — skip",
                    cred_id=cred.cred_id,
                    flavor=cred.flavor,
                    model=model,
                )
                continue

            kwargs = dict(base_kwargs)
            kwargs["model"] = actual_model
            try:
                material = await cred.credential.resolve()
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "credential resolve gagal — cooldown & lanjut",
                    cred_id=cred.cred_id,
                    error=str(exc),
                )
                await pool.report_failure(cred.cred_id, kind="auth")
                continue
            material.merge_into(kwargs)

            try:
                return await self._attempt_loop(kwargs, actual_model, response_schema)
            except LLMUnavailableError as exc:
                cause = exc.__cause__ or exc
                kind = classify_llm_error(cause)
                last_error = exc
                if kind in ("rate_limit", "auth"):
                    logger.warning(
                        "credential kena limit/auth — fallback ke berikutnya",
                        cred_id=cred.cred_id,
                        kind=kind,
                    )
                    await pool.report_failure(cred.cred_id, kind=kind)
                    continue
                raise
```

**Langkah 4.** Tambahkan method privat `_attempt_loop` — ini ADALAH isi loop lama
`complete()` yang dipindah, perilaku identik:

```python
    async def _attempt_loop(
        self,
        kwargs: dict[str, Any],
        model: str,
        response_schema: type[BaseModel] | None,
    ) -> LLMCallResult:
        """Loop retry untuk SATU kredensial (perilaku lama complete())."""
        last_error: Exception | None = None
        attempts = 1 + self.max_retries

        for attempt in range(attempts):
            try:
                start = time.monotonic()
                response = await litellm.acompletion(**kwargs)
                latency = (time.monotonic() - start) * 1000

                content = response.choices[0].message.content or ""
                usage = response.usage or _empty_usage()

                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_tokens = prompt_tokens + completion_tokens

                cost = _estimate_cost(model, prompt_tokens, completion_tokens)

                result = LLMCallResult(
                    content=content,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    latency_ms=latency,
                )

                if response_schema is not None:
                    _validate_json(content, response_schema)

                self._call_count += 1
                self._total_cost += cost
                self._total_tokens += total_tokens

                logger.info(
                    "llm call completed",
                    model=model,
                    attempt=attempt + 1,
                    tokens=total_tokens,
                    cost_usd=f"{cost:.4f}",
                    latency_ms=f"{latency:.0f}",
                )

                return result

            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "llm json parse failed, retrying",
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                continue

            except Exception as exc:
                last_error = exc
                logger.error(
                    "llm call failed",
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < attempts - 1:
                    continue
                raise LLMUnavailableError(f"all LLM attempts failed for {model}: {exc}") from exc

        raise LLMOutputError(f"invalid LLM output after {attempts} attempts: {last_error}")
```

Setelah refactor ini, loop `for attempt in range(attempts)` LAMA di `complete()`
harus SUDAH TIDAK ADA (sudah pindah ke `_attempt_loop`). Pastikan tidak ada kode duplikat.

**Langkah 5.** Test — TAMBAHKAN di `tests/unit/test_llm_client.py`:

```python
def test_pool_fallback_on_rate_limit(monkeypatch) -> None:
    """Kredensial pertama 429 → client otomatis pakai kredensial kedua."""
    import asyncio

    from rtrade.llm.auth.api_key import ApiKeyProvider
    from rtrade.llm.auth.pool import CredentialPool, PooledCredential
    from rtrade.llm.client import LLMClient

    pool = CredentialPool(
        [
            PooledCredential("k1", "gemini", ApiKeyProvider("AIza-limit")),
            PooledCredential("k2", "gemini", ApiKeyProvider("AIza-ok")),
        ]
    )
    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == "AIza-limit":
            raise Exception("HTTP 429 rate limit exceeded")

        class Msg:
            content = '{"ok": true}'

        class Choice:
            message = Msg()

        class Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class Resp:
            choices = [Choice()]
            usage = Usage()

        return Resp()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LLMClient(max_retries=0, credential_pool=pool)
    result = asyncio.run(client.complete("gemini/test-model", "sys", "user"))
    assert result.content == '{"ok": true}'
    assert calls == ["AIza-limit", "AIza-ok"]


def test_pool_all_exhausted_raises_unavailable(monkeypatch) -> None:
    import asyncio

    import pytest

    from rtrade.core.errors import LLMUnavailableError
    from rtrade.llm.auth.api_key import ApiKeyProvider
    from rtrade.llm.auth.pool import CredentialPool, PooledCredential
    from rtrade.llm.client import LLMClient

    pool = CredentialPool(
        [PooledCredential("k1", "gemini", ApiKeyProvider("AIza-1"))]
    )

    async def always_429(**kwargs):
        raise Exception("HTTP 429 rate limit")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", always_429)
    client = LLMClient(max_retries=0, credential_pool=pool)
    with pytest.raises(LLMUnavailableError):
        asyncio.run(client.complete("gemini/test-model", "sys", "user"))


def test_pool_skips_incompatible_flavor(monkeypatch) -> None:
    """Kredensial anthropic di-skip untuk model gemini, tanpa cooldown."""
    import asyncio

    from rtrade.llm.auth.api_key import ApiKeyProvider
    from rtrade.llm.auth.pool import CredentialPool, PooledCredential
    from rtrade.llm.client import LLMClient

    pool = CredentialPool(
        [
            PooledCredential("a1", "anthropic", ApiKeyProvider("sk-ant-api-x")),
            PooledCredential("g1", "gemini", ApiKeyProvider("AIza-ok")),
        ]
    )
    used: list[str] = []

    async def fake_acompletion(**kwargs):
        used.append(kwargs["api_key"])

        class Msg:
            content = "ok"

        class Choice:
            message = Msg()

        class Resp:
            choices = [Choice()]
            usage = None

        return Resp()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LLMClient(max_retries=0, credential_pool=pool)
    asyncio.run(client.complete("gemini/test-model", "sys", "user"))
    assert used == ["AIza-ok"]
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\llm\client.py -Pattern "credential_pool|_attempt_loop|translate_model|classify_llm_error"
# ≥6 baris
```

**Commit:** `feat(llm): LLMClient credential-pool fallback mode (A8)`

---

## A9 — Wiring `scan.py` (DUA situs) + model per-role dari router

**File:** `src/rtrade/pipeline/scan.py`

Ada DUA tempat `LLMClient(` dibangun (cek dengan
`Select-String -Path src\rtrade\pipeline\scan.py -Pattern "LLMClient\("` — saat ini
baris ±515 di jalur coroner dan ±896 di jalur pipeline utama).

**Langkah 1.** Tambahkan helper module-level (letakkan persis di bawah
`_build_cred_provider` yang lama — fungsi lama JANGAN dihapus, masih dipakai fallback
modul lain? → cek: kalau `_build_cred_provider` tidak dipakai lagi setelah task ini,
HAPUS sekalian beserta importnya, lalu pastikan ruff bersih):

```python
def _build_llm_client(cfg: AppConfig) -> Any:
    """LLMClient dengan credential pool (A9). Fallback otomatis antar key/akun."""
    from rtrade.llm.client import LLMClient
    from rtrade.llm.pool_builder import build_scan_pool

    return LLMClient(
        timeout=cfg.settings.llm.timeout_seconds,
        temperature=cfg.settings.llm.temperature,
        credential_pool=build_scan_pool(cfg),
    )
```

**Langkah 2.** Situs pipeline utama (±896) — GANTI:

```python
            client = LLMClient(
                api_key=cfg.secrets.gemini_api_key_1,
                timeout=cfg.settings.llm.timeout_seconds,
                temperature=cfg.settings.llm.temperature,
                credential_provider=_build_cred_provider(cfg),
            )
```

menjadi:

```python
            client = _build_llm_client(cfg)
```

**Langkah 3.** Model per-role dari router (mengaktifkan `rtrade auth use`):
masih di blok yang sama, GANTI argumen model pada `run_llm_pipeline(...)`:

```python
                analyst_model=cfg.settings.llm.analyst_model,
                critic_model=cfg.settings.llm.critic_model,
```

menjadi:

```python
                analyst_model=resolve_role_model(cfg, "analyst"),
                critic_model=resolve_role_model(cfg, "critic"),
```

dan pada blok eskalasi flagship, ganti `cfg.settings.llm.flagship_model` (3 tempat di
sekitar `should_escalate` dan `run_llm_pipeline` eskalasi) dengan variabel:

```python
            flagship_model = resolve_role_model(cfg, "flagship")
```

(deklarasikan sekali sebelum `should_escalate`, lalu pakai `flagship_model` di
`should_escalate(..., flagship_model=flagship_model)` dan kedua argumen
`analyst_model=flagship_model, critic_model=flagship_model`.)

Tambahkan import di bagian import scan.py:

```python
from rtrade.llm.model_router import resolve_role_model
```

**Langkah 4.** Situs coroner (±515) — GANTI:

```python
                            report = await run_coroner(
                                LLMClient(
                                    api_key=cfg.secrets.gemini_api_key_1,
                                    timeout=cfg.settings.llm.timeout_seconds,
                                    credential_provider=_build_cred_provider(cfg),
                                ),
                                model=cfg.settings.llm.analyst_model,
```

menjadi:

```python
                            report = await run_coroner(
                                _build_llm_client(cfg),
                                model=resolve_role_model(cfg, "analyst"),
```

**Langkah 5.** Jika setelah langkah 2 & 4 `_build_cred_provider` tidak dipanggil
lagi di scan.py: hapus fungsinya. Verifikasi:

```powershell
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "_build_cred_provider"
# hasil HARUS kosong setelah penghapusan
```

**Langkah 6.** Smoke test — jalankan seluruh unit test. Path `llm.enabled=false`
TIDAK menyentuh kode baru (pool dibangun lazy di dalam branch llm) → tidak ada
regresi pada test pipeline yang ada.

**BUKTI (anti-yatim — bagian terpenting plan ini):**
```powershell
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "build_scan_pool|_build_llm_client|resolve_role_model"
# WAJIB: build_scan_pool ≥1, _build_llm_client ≥3 (1 def + 2 pemakaian), resolve_role_model ≥4
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "api_key=cfg.secrets.gemini_api_key_1"
# WAJIB kosong
```

**Commit:** `feat(pipeline): scan uses credential pool + per-role model routes (A9)`

---

## A10 — Contoh konfigurasi & env

**Langkah 1.** `.env.prod.example` — GANTI blok `=== LLM ===` menjadi:

```bash
# === LLM ===
# ⚠️ HANYA API key RESMI (console/aistudio). OAuth consumer token DILARANG (§14.2).
# Multi-key per provider = fallback otomatis saat kena rate limit (A1/A6).
GEMINI_API_KEY_1=                       # WAJIB minimal satu kredensial LLM
GEMINI_API_KEY_2=                       # Opsional: fallback
GEMINI_API_KEY_3=
GEMINI_API_KEY_4=
GEMINI_API_KEY_5=
ANTHROPIC_API_KEY_1=                    # Opsional: critic/flagship upgrade
ANTHROPIC_API_KEY_2=
ANTHROPIC_API_KEY_3=
OPENAI_API_KEY_1=                       # Opsional: backup
OPENAI_API_KEY_2=
OPENAI_API_KEY_3=
XAI_API_KEY_1=                          # Opsional: Grok (console.x.ai)
XAI_API_KEY_2=
XAI_API_KEY_3=

# OpenAI/Codex:
# - Codex CLI / ChatGPT consumer OAuth sengaja TIDAK ADA env-nya dan tidak didukung.
# - Untuk OpenAI gunakan OPENAI_API_KEY_1..3 atau gateway enterprise di bawah.
RTRADE_OPENAI_GATEWAY_TOKEN_URL=
RTRADE_OPENAI_GATEWAY_CLIENT_ID=
RTRADE_OPENAI_GATEWAY_SCOPES=
RTRADE_OPENAI_GATEWAY_DEVICE_URL=
RTRADE_OPENAI_GATEWAY_MODELS_URL=

# xAI Hermes-style external adapter (opsional):
# Adapter adalah binary/script milik operator yang mencetak JSON token standar ke stdout.
# Core bot TIDAK membaca cookie/session/token aplikasi lain.
RTRADE_XAI_AUTH_ADAPTER_BIN=
RTRADE_XAI_MODELS_URL=

# OAuth multi-akun (opsional, setara API key):
#   docker compose ... exec app python -m rtrade.cli.auth login --provider google --account utama
#   docker compose ... exec app python -m rtrade.cli.auth login --provider google --account cadangan
# Token tersimpan terenkripsi (RTRADE_TOKEN_KEY wajib di prod):
RTRADE_TOKEN_KEY=                       # generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Langkah 2.** `config/settings.yaml` — tambahkan KOMENTAR dokumentasi di bawah blok
`llm:` (komentar saja, JANGAN mengubah nilai aktif):

```yaml
  # --- Credential pool (A6-A9) ---
  # Pool fallback dibangun OTOMATIS dari semua kredensial yang ada, urutan:
  #   1) API key family model analyst (slot 1..N), 2) API key family lain,
  #   3) akun OAuth CLI tersimpan (auth_profiles cli_oauth), 4) akun ADC Vertex.
  # Kena 429/auth error → kredensial cooldown 60s → otomatis pindah ke berikutnya.
  # Multi-akun OAuth: python -m rtrade.cli.auth login --provider <id> --account <label>
```

**Langkah 3.** `config/oauth_providers.example.yaml` — di komentar header file
tambahkan satu baris:

```yaml
# Multi-akun: `rtrade auth login --provider <id> --account <label>` — tiap akun jadi
# anggota credential pool dan dipakai fallback otomatis saat akun lain kena limit.
```

**BUKTI:**
```powershell
Select-String -Path .env.prod.example -Pattern "GEMINI_API_KEY_5|XAI_API_KEY_1|RTRADE_XAI_AUTH_ADAPTER_BIN|RTRADE_TOKEN_KEY"
Select-String -Path config\settings.yaml -Pattern "Credential pool"
```

**Commit:** `docs(config): multi-key env slots + pool documentation (A10)`

---

## A11 — `setup_vps.sh`: menu provider interaktif (gaya Hermes)

**File:** `scripts/setup_vps.sh`

**Langkah 1.** Perbaiki header (baris 5-9): cara `curl | bash` TIDAK BISA interaktif
(stdin tersambung ke pipe → semua prompt ke-skip). GANTI komentar usage menjadi:

```bash
#  Jalankan di VPS Ubuntu 24.04 (download dulu — JANGAN curl|bash, prompt butuh stdin):
#    curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh -o setup_vps.sh
#    chmod +x setup_vps.sh
#    sudo ./setup_vps.sh
```

**Langkah 2.** GANTI SELURUH fungsi `collect_credentials()` dengan versi menu di
bawah. Variabel `TELEGRAM_TOKEN` dipakai fungsi lain (`build_and_start`,
`setup_logrotate`) — deklarasinya HARUS tetap tanpa `local` di level yang bisa
diakses (lihat catatan di akhir snippet).

```bash
# ============================================================================
# STEP 5: COLLECT CREDENTIALS — menu provider gaya Hermes (A11)
# ============================================================================

# Kumpulkan sampai MAX key untuk satu provider ke dalam array global bernama $2.
collect_keys() {
    local label="$1" arr_name="$2" max="$3"
    local -n arr_ref="$2"
    arr_ref=()
    echo -e "${BOLD}${label} — masukkan sampai ${max} key (Enter kosong = selesai)${NC}"
    local i key
    for (( i=1; i<=max; i++ )); do
        read -rp "$(echo -e "${CYAN}  Key #${i}:${NC} ")" key
        [[ -z "$key" ]] && break
        if [[ "$key" == sk-ant-oat* ]]; then
            error "Token konsumen (sk-ant-oat...) DILARANG — pakai API key resmi."
            (( i-- ))
            continue
        fi
        arr_ref+=("$key")
    done
    success "${label}: ${#arr_ref[@]} key tersimpan"
}

collect_credentials() {
    step "5/9 — Credentials & Configuration"

    # Auto-generate secrets
    local DB_PASSWORD AUTH_TOKEN TOKEN_KEY
    DB_PASSWORD=$(openssl rand -hex 24)
    AUTH_TOKEN=$(openssl rand -hex 32)
    TOKEN_KEY=$(python3 - <<'PYEOF' 2>/dev/null || openssl rand -base64 32 | tr '+/' '-_'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PYEOF
)
    info "Secrets auto-generated ✓"

    # --- Data providers ---
    local TWELVEDATA_KEY="" FINNHUB_KEY="" DOMAIN=""
    TELEGRAM_TOKEN=""; TELEGRAM_CHAT=""   # global: dipakai step 6 & 8
    echo ""
    read -rp "$(echo -e "${CYAN}TwelveData API Key:${NC} ")" TWELVEDATA_KEY
    read -rp "$(echo -e "${CYAN}Finnhub API Key (opsional):${NC} ")" FINNHUB_KEY

    # --- Menu provider LLM ---
    GEMINI_KEYS=(); ANTHROPIC_KEYS=(); OPENAI_KEYS=(); XAI_KEYS=()
    WANT_VERTEX=0; WANT_AZURE=0; WANT_GATEWAY=0; WANT_XAI_HERMES=0
    while true; do
        echo ""
        divider
        echo -e "${BOLD}Pilih provider LLM (boleh lebih dari satu, fallback otomatis):${NC}"
        echo "  1) Gemini        — API key (aistudio.google.com)   [${#GEMINI_KEYS[@]} key]"
        echo "  2) Anthropic     — API key (console.anthropic.com) [${#ANTHROPIC_KEYS[@]} key]"
        echo "  3) OpenAI        — API key (platform.openai.com)   [${#OPENAI_KEYS[@]} key]"
        echo "  4) xAI Grok      — API key (console.x.ai)          [${#XAI_KEYS[@]} key]"
        echo "  5) Google Vertex — OAuth login (multi-akun)        [$( ((WANT_VERTEX)) && echo dipilih || echo - )]"
        echo "  6) Azure OpenAI  — OAuth/AD                        [$( ((WANT_AZURE)) && echo dipilih || echo - )]"
        echo "  7) OAuth gateway — enterprise/self-hosted          [$( ((WANT_GATEWAY)) && echo dipilih || echo - )]"
        echo "  8) xAI Hermes    — external adapter                [$( ((WANT_XAI_HERMES)) && echo dipilih || echo - )]"
        echo "  0) Selesai"
        read -rp "$(echo -e "${YELLOW}Pilihan [0-8]:${NC} ")" choice
        case "$choice" in
            1) collect_keys "Gemini" GEMINI_KEYS 5 ;;
            2) collect_keys "Anthropic" ANTHROPIC_KEYS 3 ;;
            3) collect_keys "OpenAI" OPENAI_KEYS 3 ;;
            4) collect_keys "xAI" XAI_KEYS 3 ;;
            5) WANT_VERTEX=1; success "Vertex dipilih — login OAuth dilakukan SETELAH install (lihat ringkasan)" ;;
            6) WANT_AZURE=1; success "Azure dipilih — isi AZURE_* env setelah install (lihat ringkasan)" ;;
            7) WANT_GATEWAY=1; success "Gateway dipilih — isi RTRADE_OAUTH_* env setelah install" ;;
            8) WANT_XAI_HERMES=1; success "xAI Hermes-style dipilih — isi RTRADE_XAI_AUTH_ADAPTER_BIN setelah install" ;;
            0) break ;;
            *) warn "Pilihan tidak dikenal" ;;
        esac
    done

    local total_keys=$(( ${#GEMINI_KEYS[@]} + ${#ANTHROPIC_KEYS[@]} + ${#OPENAI_KEYS[@]} + ${#XAI_KEYS[@]} ))
    if [[ $total_keys -eq 0 && $WANT_VERTEX -eq 0 && $WANT_AZURE -eq 0 && $WANT_GATEWAY -eq 0 && $WANT_XAI_HERMES -eq 0 ]]; then
        warn "Belum ada kredensial LLM sama sekali — bot jalan TANPA LLM sampai .env diisi."
    fi

    # --- Telegram ---
    echo ""
    echo -e "${BOLD}--- Telegram ---${NC}"
    read -rp "$(echo -e "${CYAN}Telegram Bot Token:${NC} ")" TELEGRAM_TOKEN
    read -rp "$(echo -e "${CYAN}Telegram Chat ID:${NC} ")" TELEGRAM_CHAT
    echo ""
    echo -e "${BOLD}--- Domain (opsional, untuk TLS auto) ---${NC}"
    read -rp "$(echo -e "${CYAN}Domain (kosong = localhost):${NC} ")" DOMAIN

    # --- Tulis .env ---
    info "Generating .env file..."
    {
        echo "# ============================================================================"
        echo "# Robil Trade — Production Environment"
        echo "# Auto-generated by setup_vps.sh on $(date -Iseconds)"
        echo "# ⚠️  JANGAN commit file ini ke git!"
        echo "# ============================================================================"
        echo ""
        echo "# === Database ==="
        echo "RTRADE_DB_PASSWORD=${DB_PASSWORD}"
        echo "DATABASE_URL=postgresql+asyncpg://rtrade:${DB_PASSWORD}@db:5432/rtrade"
        echo "REDIS_URL=redis://redis:6379/0"
        echo ""
        echo "# === Data Providers ==="
        echo "TWELVEDATA_API_KEY=${TWELVEDATA_KEY}"
        echo "FINNHUB_API_KEY=${FINNHUB_KEY}"
        echo ""
        echo "# === LLM (multi-key = fallback otomatis) ==="
        local i
        for i in "${!GEMINI_KEYS[@]}";    do echo "GEMINI_API_KEY_$((i+1))=${GEMINI_KEYS[$i]}"; done
        for i in "${!ANTHROPIC_KEYS[@]}"; do echo "ANTHROPIC_API_KEY_$((i+1))=${ANTHROPIC_KEYS[$i]}"; done
        for i in "${!OPENAI_KEYS[@]}";    do echo "OPENAI_API_KEY_$((i+1))=${OPENAI_KEYS[$i]}"; done
        for i in "${!XAI_KEYS[@]}";       do echo "XAI_API_KEY_$((i+1))=${XAI_KEYS[$i]}"; done
        echo ""
        echo "# === OAuth token store (terenkripsi, wajib prod) ==="
        echo "RTRADE_TOKEN_KEY=${TOKEN_KEY}"
        echo ""
        echo "# === xAI Hermes-style external adapter (opsional) ==="
        echo "RTRADE_XAI_AUTH_ADAPTER_BIN="
        echo "RTRADE_XAI_MODELS_URL="
        echo ""
        echo "# === Telegram ==="
        echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}"
        echo "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT}"
        echo ""
        echo "# === API & Security ==="
        echo "API_AUTH_TOKEN=${AUTH_TOKEN}"
        echo "DOMAIN=${DOMAIN:-localhost}"
        echo ""
        echo "# === Runtime ==="
        echo "ENV=prod"
        echo "LOG_LEVEL=INFO"
    } > "$INSTALL_DIR/.env"

    chmod 600 "$INSTALL_DIR/.env"
    chown "$APP_USER:$APP_USER" "$INSTALL_DIR/.env"
    success ".env file created (permissions: 600)"
}
```

**Langkah 3.** Di `verify_and_summary()`, SETELAH blok kotak summary, tambahkan
instruksi OAuth kondisional:

```bash
    if [[ ${WANT_VERTEX:-0} -eq 1 || ${WANT_AZURE:-0} -eq 1 || ${WANT_GATEWAY:-0} -eq 1 || ${WANT_XAI_HERMES:-0} -eq 1 ]]; then
        echo ""
        echo -e "${BOLD}${YELLOW}── Langkah OAuth (provider yang Anda pilih) ──${NC}"
        local CEX="docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app"
        if [[ ${WANT_VERTEX:-0} -eq 1 ]]; then
            echo "  Google Vertex (bisa >1 akun, fallback otomatis):"
            echo "    1. Isi GOOGLE_OAUTH_CLIENT_SECRETS di .env (path client_secrets.json)"
            echo "    2. $CEX python -m rtrade.cli.auth login --provider google --account utama --flow paste_url"
            echo "    3. (akun ke-2) ... login --provider google --account cadangan --flow paste_url"
            echo "    4. Set llm.vertex_project di config/settings.yaml"
        fi
        if [[ ${WANT_AZURE:-0} -eq 1 ]]; then
            echo "  Azure OpenAI: isi AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,"
            echo "    AZURE_OPENAI_ENDPOINT di .env lalu enable di config/oauth_providers.yaml"
        fi
        if [[ ${WANT_GATEWAY:-0} -eq 1 ]]; then
            echo "  Gateway: isi RTRADE_OAUTH_TOKEN_URL/CLIENT_ID/SCOPES/DEVICE_URL di .env, lalu:"
            echo "    $CEX python -m rtrade.cli.auth login --provider generic_gateway --account utama"
        fi
        if [[ ${WANT_XAI_HERMES:-0} -eq 1 ]]; then
            echo "  xAI Hermes-style: isi RTRADE_XAI_AUTH_ADAPTER_BIN di .env, lalu:"
            echo "    $CEX python -m rtrade.cli.auth login --provider xai_hermes --account utama"
            echo "    $CEX python -m rtrade.cli.auth models --provider xai_hermes"
        fi
        echo "  Cek semua akun: $CEX python -m rtrade.cli.auth status"
        echo "  Lihat pool:     $CEX python -m rtrade.cli.auth pool"
    fi
```

**Catatan bash untuk agen (jangan dilanggar):**
- `local -n` (nameref) butuh bash ≥4.3 — Ubuntu 24.04 punya bash 5.x, aman.
- `GEMINI_KEYS` dkk, `WANT_*`, `TELEGRAM_TOKEN` sengaja TANPA `local` supaya terbaca
  fungsi lain (`build_and_start` membaca `TELEGRAM_TOKEN`).
- Jangan ubah fungsi lain selain yang diminta.
- Validasi sintaks WAJIB: `bash -n scripts/setup_vps.sh` harus exit 0
  (jalankan via Git Bash di Windows: `& "C:\Program Files\Git\bin\bash.exe" -n scripts/setup_vps.sh`).

**BUKTI:**
```powershell
Select-String -Path scripts\setup_vps.sh -Pattern "collect_keys|WANT_VERTEX|WANT_XAI_HERMES|XAI_API_KEY|RTRADE_XAI_AUTH_ADAPTER_BIN|Pilih provider LLM"
# ≥6 baris
Select-String -Path scripts\setup_vps.sh -Pattern "GEMINI_KEY_1|Gemini API Key .utama."
# WAJIB kosong (prompt lama hilang)
```

**Commit:** `feat(vps): interactive multi-provider credential menu in setup (A11)`

---

## A12 — CLI `auth pool` (visibilitas operator)

**File:** `src/rtrade/cli/auth.py`

**Langkah 1.** Fungsi baru:

```python
def _cmd_pool(_args: argparse.Namespace) -> None:
    """Tampilkan isi credential pool + status tiap kredensial."""
    from rtrade.core.config import AppConfig
    from rtrade.llm.model_router import resolve_role_model
    from rtrade.llm.pool_builder import build_scan_pool

    cfg = AppConfig.load()
    for role in ("analyst", "critic", "flagship"):
        print(f"role {role}: model={resolve_role_model(cfg, role)}")  # noqa: T201
    try:
        pool = build_scan_pool(cfg)
    except Exception as exc:
        print(f"POOL KOSONG / ERROR: {exc}")  # noqa: T201
        sys.exit(1)
    print(f"\n{'#':<3} {'cred_id':<28} {'flavor':<10} {'mode':<10} status")  # noqa: T201
    print("-" * 70)  # noqa: T201
    for i, e in enumerate(pool.entries, start=1):
        status = "ready"
        if e.credential.mode == "cli_oauth":
            from rtrade.llm.auth.token_store import load_token

            sid = getattr(e.credential, "token_store_id", "") or getattr(
                e.credential, "provider_id", ""
            )
            status = "logged_in" if load_token(sid) else "NOT_LOGGED_IN"
        print(  # noqa: T201
            f"{i:<3} {e.cred_id:<28} {e.flavor:<10} {e.credential.mode:<10} {status}"
        )
```

**Langkah 2.** Daftarkan di `main()`:

```python
    sub.add_parser("pool", help="Tampilkan credential pool + status")
```

dan di dispatch: `"pool": _cmd_pool,`

**Langkah 3.** Test — TAMBAHKAN di `tests/unit/test_cli_auth_accounts.py`:

```python
def test_cmd_pool_lists_credentials(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path / "tok"))
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path / "adc"))
    monkeypatch.setenv("GEMINI_API_KEY_1", "AIza-test-1")
    monkeypatch.setenv("GEMINI_API_KEY_2", "AIza-test-2")
    from argparse import Namespace

    from rtrade.cli.auth import _cmd_pool

    _cmd_pool(Namespace())
    out = capsys.readouterr().out
    assert "gemini_key_1" in out
    assert "gemini_key_2" in out
    assert "AIza-test" not in out  # JANGAN pernah cetak key mentah
```

(Bila `AppConfig.load()` di test butuh env/file lain, tiru pola test config yang ada.
Assertion `"AIza-test" not in out` adalah guard keamanan — WAJIB lulus.)

**BUKTI:**
```powershell
Select-String -Path src\rtrade\cli\auth.py -Pattern "_cmd_pool|\"pool\""
```

**Commit:** `feat(cli): auth pool inspection command (A12)`

---

## A13 — Dokumentasi

**Langkah 1.** `docs/AUTH_OAUTH.md` — tambahkan section di akhir file:

```markdown
## Multi-Key & Multi-Akun (Credential Pool)

Kedudukan OAuth = API key: keduanya anggota pool yang sama, tinggal pilih.

### Banyak API key per provider (fallback)
Isi slot di `.env`: `GEMINI_API_KEY_1..5`, `ANTHROPIC_API_KEY_1..3`,
`OPENAI_API_KEY_1..3`, `XAI_API_KEY_1..3`. Saat satu key kena 429, key itu cooldown
60 detik dan panggilan otomatis pindah ke key berikutnya. Tidak perlu konfigurasi lain.

### Banyak akun OAuth per provider (fallback)
```bash
python -m rtrade.cli.auth login --provider google --account utama
python -m rtrade.cli.auth login --provider google --account cadangan
python -m rtrade.cli.auth login --provider generic_gateway --account kerja
python -m rtrade.cli.auth accounts --provider google     # daftar akun
python -m rtrade.cli.auth status                          # status semua token
python -m rtrade.cli.auth pool                            # isi pool + urutan fallback
python -m rtrade.cli.auth logout --provider google --account cadangan
```
Setiap akun = file token terenkripsi terpisah (`~/.rtrade/tokens/<provider>__<akun>.json`,
ADC Google di `~/.rtrade/adc/google__<akun>.json`). Akun yang kena limit otomatis
cooldown dan akun lain mengambil alih.

### Urutan fallback pool (otomatis)
1. API key se-family dengan model analyst (slot 1 → N)
2. API key family lain
3. Kredensial route `model_routes`/`auth_profiles` (non api-key)
4. Akun OAuth CLI tersimpan
5. Akun ADC Vertex (`llm.vertex_project` wajib diisi)

Model otomatis diterjemahkan antar flavor Google (`gemini/x` ↔ `vertex_ai/x`).
Kredensial yang tidak kompatibel dengan model yang diminta dilewati tanpa penalti.

### Provider policy: Codex OAuth vs xAI Hermes-style
- **Codex consumer OAuth BLOCKED**: yang beredar sebagai "Codex OAuth" adalah login akun
  ChatGPT konsumen lewat Codex CLI. Token ini terikat langganan/user session dan tidak boleh
  diputar ke API bot. Manifest wajib `codex_consumer_oauth.capability=disabled_unsupported`,
  `enabled=false`, dan registry wajib raise bila route/pool mencoba memakainya.
- **OpenAI backend yang boleh**: `OPENAI_API_KEY_1..3` atau `openai_gateway` yang menerbitkan
  token API lewat gateway enterprise/self-hosted.
- **xAI Hermes-style BOLEH**: xAI masuk pool lewat `XAI_API_KEY_1..3` atau provider
  `xai_hermes` (`external_adapter`) yang mencetak JSON token standar. Core bot hanya membaca
  JSON stdout adapter dan tidak membaca cookie/session/token aplikasi lain.
```

**Langkah 2.** `README.md` — di section setup/VPS (cari dengan
`Select-String -Path README.md -Pattern "setup_vps"`), tambahkan satu paragraf:

```markdown
Saat setup VPS, STEP 5 menampilkan menu semua provider LLM (Gemini/Anthropic/OpenAI/
xAI via API key; xAI Hermes-style via external adapter; Vertex/Azure/gateway via OAuth).
Codex CLI/ChatGPT consumer OAuth sengaja diblokir. Tiap provider boleh diisi banyak
key dan banyak akun OAuth — semuanya masuk credential pool dengan fallback otomatis
saat kena rate limit. Detail: docs/AUTH_OAUTH.md §Multi-Key & Multi-Akun.
```

**BUKTI:**
```powershell
Select-String -Path docs\AUTH_OAUTH.md -Pattern "Multi-Key & Multi-Akun|credential pool|Codex consumer OAuth|xAI Hermes"
Select-String -Path README.md -Pattern "credential pool|Codex CLI|xAI Hermes"
```

**Commit:** `docs(auth): multi-key & multi-account credential pool guide (A13)`

---

## A14 — GATE AKHIR + AUDIT ANTI-YATIM (WAJIB, JANGAN DILEWATI)

Jalankan SEMUA dan tempelkan outputnya di laporanmu:

```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format --check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest tests/unit -q
& "C:\Program Files\Git\bin\bash.exe" -n scripts/setup_vps.sh
```

**Audit anti-yatim** — setiap modul baru/lama HARUS diimpor runtime (bukan cuma test):

```powershell
# 1. KeyManager dipakai pool:
Select-String -Path src\rtrade\llm\auth\pool.py -Pattern "key_manager import"
# 2. pool dipakai client & builder:
Select-String -Path src\rtrade\llm\client.py,src\rtrade\llm\pool_builder.py -Pattern "auth.pool import|auth\.pool"
# 3. pool_builder dipakai scan & CLI:
Select-String -Path src\rtrade\pipeline\scan.py,src\rtrade\cli\auth.py -Pattern "pool_builder"
# 4. resolve_model_auth & resolve_role_model dipakai builder/scan:
Select-String -Path src\rtrade\llm\pool_builder.py,src\rtrade\pipeline\scan.py -Pattern "resolve_model_auth|resolve_role_model"
# 5. Jalur lama yang HARUS hilang:
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "api_key=cfg.secrets.gemini_api_key_1|_build_cred_provider"
# (baris 5 WAJIB kosong)
# 6. Codex consumer OAuth fail-closed + xAI Hermes-style enabled:
Select-String -Path config\oauth_providers.example.yaml,src\rtrade\llm\auth\provider_profiles.py,src\rtrade\llm\pool_builder.py -Pattern "codex_consumer_oauth|disabled_unsupported|is_blocked_consumer_oauth|xai_hermes"
# WAJIB: codex_consumer_oauth + disabled_unsupported + is_blocked_consumer_oauth + xai_hermes muncul.
#      Tidak boleh ada mapping codex_consumer_oauth/codex_openai ke flavor openai di pool_builder.
```

**Checklist laporan akhir (isi semua):**
- [ ] ruff check: 0 error
- [ ] ruff format --check: 0 perubahan
- [ ] mypy: 0 error  ← JANGAN klaim selesai tanpa menjalankan ini
- [ ] pytest unit: semua pass (tulis jumlah test)
- [ ] bash -n setup_vps.sh: exit 0
- [ ] Audit anti-yatim poin 1-4: semua ada; poin 5: kosong
- [ ] Audit provider policy poin 6: Codex consumer OAuth blocked; xAI Hermes-style ada
- [ ] Tidak ada `print`/`logger` yang mencetak api_key/token mentah (cek diff sendiri)
- [ ] `_FORBIDDEN_KEY_PREFIXES` & `_reject_consumer_oauth` utuh
- [ ] 15 commit terpisah sesuai pesan yang ditentukan (A0-A14)

**Commit terakhir:** `chore(auth): final gate + anti-orphan audit for credential pool (A14)`
