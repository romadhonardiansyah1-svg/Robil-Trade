# IMPLEMENTATION TASKS 9 — AUTH/POOL DEPLOY FIXES (lanjutan Plan 8)

> **Untuk: agen pelaksana.** Kerjakan **berurutan C1 → C10**. Satu task = satu commit.
> Ditulis berdasarkan pembacaan langsung kode pada HEAD `e034556` (setelah Plan 8 A0–A14 di-commit).
> Status awal sudah HIJAU: `mypy` 0 error, `pytest tests/unit` semua lulus. **Jangan sampai merah.**
> Setiap snippet "GANTI" di bawah dikutip PERSIS dari kode yang ada sekarang — jangan improvisasi.

---

## 0. ATURAN KERJA (BACA DULU, WAJIB)

### 0.1 Perintah & gate
- uv di path absolut: `& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run <cmd>`
- Gate per task (SEMUA wajib hijau sebelum commit):
  ```powershell
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest tests/unit -q
  ```
  **mypy WAJIB dijalankan & 0 error.** **pytest WAJIB semua lulus.**
- BUKTI pakai **PowerShell Select-String** (BUKAN ripgrep — pernah miss file di repo ini).
- Untuk validasi bash: `& "C:\Program Files\Git\bin\bash.exe" -n scripts/setup_vps.sh` harus exit 0.

### 0.2 DILARANG KERAS (keamanan — tidak bisa dinego)
1. **JANGAN** menghapus/melemahkan `_FORBIDDEN_KEY_PREFIXES = ("sk-ant-oat",)` & validator
   `_reject_consumer_oauth` di `src/rtrade/core/config.py`. Saat ini menutup 14 slot key — biarkan.
2. **JANGAN** menghapus/melemahkan `_CONSUMER_TOKEN_SOURCES` & cek-nya di
   `src/rtrade/llm/auth/provider_profiles.py`.
3. **JANGAN** menulis kode yang membaca `~/.codex`, `~/.claude`, cookies/localStorage/session DB
   aplikasi lain.
4. **JANGAN** me-log nilai `access_token`, `refresh_token`, `client_secret`, `api_key`, header Authorization.
5. **JANGAN** menyentuh guardrails (`GR-*`), floor risk, atau logika sinyal.

### 0.3 RUANG LINGKUP PLAN INI (penting — baca)
Plan ini **hanya** memperbaiki **infrastruktur kredensial generik** yang rusak: persistensi token store
di container, `RTRADE_TOKEN_KEY`, routing `auth use`, pembangunan credential pool, parsing device-flow,
dan menu setup. **Semua perbaikan ini berlaku untuk SEMUA provider OAuth** (Vertex ADC, Azure, gateway
enterprise, dst.) — bukan khusus satu provider. Plan ini **TIDAK menambah/mengubah** endpoint atau logika
khusus login akun konsumen; entri manifest `codex_oauth`/`xai_oauth` yang sudah ada **tidak disentuh** di
plan ini (tetap konfigurasi & tanggung jawab operator). Guard di 0.2 tetap aktif.

### 0.4 Konteks temuan (hasil audit — supaya paham "kenapa")
- **F1 (BLOCKER):** `scripts/setup_vps.sh` set `ENV=prod` tapi tidak menulis `RTRADE_TOKEN_KEY`.
  `token_store.save_token` di prod tanpa key → `RuntimeError`. Semua login OAuth gagal simpan token.
- **F2 (BLOCKER):** Container prod `read_only: true` (`docker-compose.prod.yml`). Token store default
  `~/.rtrade/tokens` = `/home/rtrade/.rtrade/tokens` ada di root FS read-only & TIDAK di-mount →
  `mkdir`/`write` gagal. Bahkan kalau bisa nulis, tidak persisten antar-restart. ADC Google idem.
  Volume `./data:/app/data` SUDAH writable+persisten → arahkan token/ADC ke sana via env.
- **F3 (BUG):** `cli/auth.py::_cmd_status` (baris 132-146) versi LAMA — `load_token(pid)` saja, tidak
  membaca akun `provider__akun.json`. Token multi-akun tak terlihat di `auth status`.
- **F4 (BUG):** `cli/auth.py::_cmd_use` (baris 250-273) menulis `model_routes[role].auth_profile` tapi
  TIDAK membuat entri `llm.auth_profiles[...]`. Route jadi menggantung → `resolve_model_auth` raise
  `ConfigError` (ditangkap & dilewati di pool_builder) → role tak pernah dapat kredensial OAuth.
- **F5 (BUG):** `_build_llm_client(cfg)` dipanggil per-kandidat (`scan.py:890`) & `build_scan_pool`
  dipanggil **tanpa redis_client** → cooldown in-memory dibangun-ulang tiap kandidat → cooldown rate-limit
  ter-reset terus → key yang kena 429 dicoba lagi berulang. Bukan crash, tapi melemahkan fallback.
- **F6 (ROBUSTNESS):** `oauth2.device_login` membaca `d["user_code"]`/`d["device_code"]` langsung →
  `KeyError` kalau endpoint balas bentuk lain.
- **F7 (ROBUSTNESS):** `_google_login` menulis ke well-known path `~/.config/gcloud/...` untuk akun
  default → gagal di container read-only (uncaught).
- **F8 (BUG):** login provider ber-`auth_mode: external_command` (mis. `xai_hermes`) jatuh ke jalur
  `build_provider_from_profile`→`device_login` dgn `device_auth_url` kosong → error tak jelas.
- **F9 (GAP):** menu multi-provider di `setup_vps.sh` (Plan 8 A11) tidak pernah dikerjakan; `.env` hasil
  setup hanya berisi `GEMINI_API_KEY_1/2`.

### 0.5 Definisi "selesai" per task
Kode sesuai snippet, BUKTI Select-String keluar persis, gate 0.1 hijau, commit dibuat.

---

## C1 — Persisten & writable token/ADC dir di container (F2)

**Tujuan:** token OAuth tersimpan di volume `./data` (writable + persisten), bukan di root FS read-only.

**File:** `docker-compose.prod.yml`

**Langkah 1.** Pada service **`app`**, blok `environment:` (sekarang baris 42-46), TAMBAHKAN dua baris
(setelah `REDIS_URL`):

```yaml
      RTRADE_TOKEN_DIR: /app/data/auth/tokens
      RTRADE_ADC_DIR: /app/data/auth/adc
```

**Langkah 2.** Lakukan hal yang SAMA pada service **`api`** (blok `environment:` baris 85-89) dan service
**`bot`** (blok `environment:` baris 125-129). Ketiga service memakai volume `./data:/app/data` yang sudah
ada, jadi `/app/data/auth/*` otomatis writable & persisten.

**Langkah 3.** Verifikasi tidak ada service lain yang perlu (caddy/db/redis/backup tidak memakai token store).

**Catatan untuk agen:** JANGAN menaruh `RTRADE_TOKEN_DIR`/`RTRADE_ADC_DIR` di `.env` — host CLI di luar
container tidak punya `/app/data`. Cukup di `environment:` compose (khusus container).

**BUKTI:**
```powershell
Select-String -Path docker-compose.prod.yml -Pattern "RTRADE_TOKEN_DIR|RTRADE_ADC_DIR"
# WAJIB muncul 6 baris (app, api, bot × 2 env)
```

**Commit:** `fix(deploy): persist OAuth token/ADC dir on writable volume in prod (C1)`

---

## C2 — `setup_vps.sh`: generate `RTRADE_TOKEN_KEY` + menu multi-provider (F1, F9)

**File:** `scripts/setup_vps.sh`

**Langkah 1 — header usage (baris 5-9).** GANTI komentar cara jalan menjadi (curl|bash tidak bisa
interaktif — stdin ke pipe):

```bash
#  Jalankan di VPS Ubuntu 24.04 (download dulu — JANGAN curl|bash, prompt butuh stdin):
#    curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh -o setup_vps.sh
#    chmod +x setup_vps.sh
#    sudo ./setup_vps.sh
```

**Langkah 2 — GANTI SELURUH fungsi `collect_credentials()`** (saat ini ±baris 266-348) dengan versi di
bawah. Versi ini: (a) generate `RTRADE_TOKEN_KEY`, (b) menu pilih provider, (c) multi-key per provider,
(d) tulis semua slot ke `.env`.

```bash
# ============================================================================
# STEP 5: COLLECT CREDENTIALS — menu provider (C2)
# ============================================================================

# Kumpulkan sampai MAX key untuk satu provider ke array global bernama $2.
collect_keys() {
    local label="$1" max="$3"
    local -n arr_ref="$2"
    arr_ref=()
    echo -e "${BOLD}${label} — masukkan sampai ${max} key (Enter kosong = selesai)${NC}"
    local i key
    for (( i=1; i<=max; i++ )); do
        read -rp "$(echo -e "${CYAN}  Key #${i}:${NC} ")" key
        [[ -z "$key" ]] && break
        if [[ "$key" == sk-ant-oat* ]]; then
            error "Token konsumen (sk-ant-oat...) DILARANG sebagai API key — pakai API key resmi."
            (( i-- )); continue
        fi
        arr_ref+=("$key")
    done
    success "${label}: ${#arr_ref[@]} key tersimpan"
}

collect_credentials() {
    step "5/9 — Credentials & Configuration"

    local DB_PASSWORD AUTH_TOKEN TOKEN_KEY
    DB_PASSWORD=$(openssl rand -hex 24)
    AUTH_TOKEN=$(openssl rand -hex 32)
    # RTRADE_TOKEN_KEY = Fernet key (urlsafe base64, 32 byte). Wajib di prod (C2/F1).
    TOKEN_KEY=$(python3 - <<'PYEOF' 2>/dev/null || openssl rand -base64 32 | tr '+/' '-_'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PYEOF
)
    info "Secrets auto-generated ✓ (termasuk RTRADE_TOKEN_KEY untuk token OAuth)"

    local TWELVEDATA_KEY="" FINNHUB_KEY="" DOMAIN=""
    TELEGRAM_TOKEN=""; TELEGRAM_CHAT=""   # global: dipakai step 6 & 8
    echo ""
    read -rp "$(echo -e "${CYAN}TwelveData API Key:${NC} ")" TWELVEDATA_KEY
    read -rp "$(echo -e "${CYAN}Finnhub API Key (opsional):${NC} ")" FINNHUB_KEY

    GEMINI_KEYS=(); ANTHROPIC_KEYS=(); OPENAI_KEYS=(); XAI_KEYS=()
    WANT_VERTEX=0; WANT_AZURE=0; WANT_GATEWAY=0
    while true; do
        echo ""; divider
        echo -e "${BOLD}Pilih provider LLM (boleh banyak, fallback otomatis):${NC}"
        echo "  1) Gemini        — API key (aistudio.google.com)   [${#GEMINI_KEYS[@]} key]"
        echo "  2) Anthropic     — API key (console.anthropic.com) [${#ANTHROPIC_KEYS[@]} key]"
        echo "  3) OpenAI        — API key (platform.openai.com)   [${#OPENAI_KEYS[@]} key]"
        echo "  4) xAI Grok      — API key (console.x.ai)          [${#XAI_KEYS[@]} key]"
        echo "  5) Google Vertex — OAuth login (multi-akun)        [$( ((WANT_VERTEX)) && echo dipilih || echo - )]"
        echo "  6) Azure OpenAI  — OAuth/AD                        [$( ((WANT_AZURE)) && echo dipilih || echo - )]"
        echo "  7) OAuth gateway — enterprise/self-hosted          [$( ((WANT_GATEWAY)) && echo dipilih || echo - )]"
        echo "  0) Selesai"
        read -rp "$(echo -e "${YELLOW}Pilihan [0-7]:${NC} ")" choice
        case "$choice" in
            1) collect_keys "Gemini" GEMINI_KEYS 5 ;;
            2) collect_keys "Anthropic" ANTHROPIC_KEYS 3 ;;
            3) collect_keys "OpenAI" OPENAI_KEYS 3 ;;
            4) collect_keys "xAI" XAI_KEYS 3 ;;
            5) WANT_VERTEX=1; success "Vertex dipilih — login OAuth dilakukan SETELAH install" ;;
            6) WANT_AZURE=1; success "Azure dipilih — isi AZURE_* env setelah install" ;;
            7) WANT_GATEWAY=1; success "Gateway dipilih — isi RTRADE_OAUTH_* env setelah install" ;;
            0) break ;;
            *) warn "Pilihan tidak dikenal" ;;
        esac
    done

    local total_keys=$(( ${#GEMINI_KEYS[@]} + ${#ANTHROPIC_KEYS[@]} + ${#OPENAI_KEYS[@]} + ${#XAI_KEYS[@]} ))
    if [[ $total_keys -eq 0 && $WANT_VERTEX -eq 0 && $WANT_AZURE -eq 0 && $WANT_GATEWAY -eq 0 ]]; then
        warn "Belum ada kredensial LLM — bot jalan TANPA LLM sampai .env diisi."
    fi

    echo ""; echo -e "${BOLD}--- Telegram ---${NC}"
    read -rp "$(echo -e "${CYAN}Telegram Bot Token:${NC} ")" TELEGRAM_TOKEN
    read -rp "$(echo -e "${CYAN}Telegram Chat ID:${NC} ")" TELEGRAM_CHAT
    echo ""; echo -e "${BOLD}--- Domain (opsional) ---${NC}"
    read -rp "$(echo -e "${CYAN}Domain (kosong = localhost):${NC} ")" DOMAIN

    info "Generating .env file..."
    {
        echo "# Auto-generated by setup_vps.sh on $(date -Iseconds) — JANGAN commit"
        echo "RTRADE_DB_PASSWORD=${DB_PASSWORD}"
        echo "DATABASE_URL=postgresql+asyncpg://rtrade:${DB_PASSWORD}@db:5432/rtrade"
        echo "REDIS_URL=redis://redis:6379/0"
        echo ""
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
        echo "# === OAuth token store (WAJIB di prod — token disimpan terenkripsi) ==="
        echo "RTRADE_TOKEN_KEY=${TOKEN_KEY}"
        echo ""
        echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}"
        echo "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT}"
        echo "API_AUTH_TOKEN=${AUTH_TOKEN}"
        echo "DOMAIN=${DOMAIN:-localhost}"
        echo "ENV=prod"
        echo "LOG_LEVEL=INFO"
    } > "$INSTALL_DIR/.env"

    chmod 600 "$INSTALL_DIR/.env"
    chown "$APP_USER:$APP_USER" "$INSTALL_DIR/.env"
    success ".env file created (permissions: 600, RTRADE_TOKEN_KEY terisi)"
}
```

**Langkah 3 — `verify_and_summary()`**: di dalam, setelah kotak summary, TAMBAHKAN instruksi OAuth
kondisional (var `WANT_*` dideklarasi di `collect_credentials` tanpa `local` → terbaca di sini karena
keduanya dipanggil dari `main` di shell yang sama):

```bash
    if [[ ${WANT_VERTEX:-0} -eq 1 || ${WANT_AZURE:-0} -eq 1 || ${WANT_GATEWAY:-0} -eq 1 ]]; then
        local CEX="docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app"
        echo ""; echo -e "${BOLD}${YELLOW}── Langkah OAuth (provider yang dipilih) ──${NC}"
        [[ ${WANT_VERTEX:-0} -eq 1 ]] && {
            echo "  Vertex (multi-akun): isi GOOGLE_OAUTH_CLIENT_SECRETS di .env, lalu:"
            echo "    $CEX python -m rtrade.cli.auth login --provider google --account utama --flow paste_url"
            echo "    set llm.vertex_project di config/settings.yaml"
        }
        [[ ${WANT_AZURE:-0} -eq 1 ]] && echo "  Azure: isi AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET/OPENAI_ENDPOINT di .env"
        [[ ${WANT_GATEWAY:-0} -eq 1 ]] && {
            echo "  Gateway: isi RTRADE_OAUTH_TOKEN_URL/CLIENT_ID/SCOPES/DEVICE_URL di .env, lalu:"
            echo "    $CEX python -m rtrade.cli.auth login --provider generic_gateway --account utama"
        }
        echo "  Cek: $CEX python -m rtrade.cli.auth status   |   $CEX python -m rtrade.cli.auth pool"
    fi
```

**Catatan bash (jangan dilanggar):**
- `local -n` (nameref) butuh bash ≥4.3 — Ubuntu 24.04 = bash 5.x, aman.
- `GEMINI_KEYS`/`ANTHROPIC_KEYS`/`OPENAI_KEYS`/`XAI_KEYS`/`WANT_*`/`TELEGRAM_TOKEN` sengaja TANPA `local`.
- Validasi sintaks WAJIB: `& "C:\Program Files\Git\bin\bash.exe" -n scripts/setup_vps.sh` exit 0.

**BUKTI:**
```powershell
Select-String -Path scripts\setup_vps.sh -Pattern "RTRADE_TOKEN_KEY=\$\{TOKEN_KEY\}|collect_keys|Pilih provider LLM"
Select-String -Path scripts\setup_vps.sh -Pattern "Gemini API Key .utama."   # WAJIB kosong (prompt lama hilang)
```

**Commit:** `fix(vps): generate RTRADE_TOKEN_KEY + multi-provider credential menu (C2)`

---

## C3 — `_cmd_status` multi-akun (F3)

**File:** `src/rtrade/cli/auth.py`

**GANTI** fungsi `_cmd_status` (saat ini baris 132-146) dengan:

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

**BUKTI:**
```powershell
Select-String -Path src\rtrade\cli\auth.py -Pattern "list_accounts|account_store_id" 
# di _cmd_status harus muncul (selain di _cmd_logout/_cmd_accounts)
```

**Commit:** `fix(cli): auth status lists all stored accounts per provider (C3)`

---

## C4 — `auth use` membuat entri `auth_profiles` (F4)

**Tujuan:** route hasil `auth use` tidak menggantung — provider OAuth/Vertex yang dirutekan benar-benar
masuk pool. **Berlaku generik** untuk semua provider (`vertex`, `cli_oauth`, `api_key`).

**File:** `src/rtrade/cli/auth.py`

**GANTI** blok penulisan settings di `_cmd_use` (saat ini baris 265-273, mulai
`llm = doc.setdefault("llm", {})` s/d `yaml.dump(...)`) menjadi:

```python
    llm = doc.setdefault("llm", {})
    routes = llm.setdefault("model_routes", {})
    profiles_cfg = llm.setdefault("auth_profiles", {})

    # Buat/lengkapi entri auth_profiles supaya route TIDAK menggantung (C4).
    entry: dict[str, object] = {"enabled": True}
    if profile.auth_mode == "vertex":
        entry["auth_type"] = "vertex"
        entry["vertex_project"] = llm.get("vertex_project", "")
    elif profile.auth_mode == "api_key":
        entry["auth_type"] = "api_key"
        # api_key_secret kosong → pool pakai key dari Secrets family (lihat pool_builder).
    else:
        # oauth2 / external_command / subscription → kredensial token store via CLI login.
        entry["auth_type"] = "cli_oauth"
        entry["provider_id"] = args.provider
        entry["account"] = getattr(args, "account", "default")
    # Jangan timpa kunci lain yang mungkin sudah diisi operator manual.
    existing = profiles_cfg.get(auth_profile_name)
    if isinstance(existing, dict):
        existing.update(entry)
    else:
        profiles_cfg[auth_profile_name] = entry

    routes[args.role] = {
        "model": args.model,
        "auth_profile": auth_profile_name,
    }

    with settings_path.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh, default_flow_style=False, allow_unicode=True)
```

**Langkah 2.** Tambahkan argumen `--account` pada parser `use` di `main()` (setelah baris 345 `use.add_argument("--force", ...)`):

```python
    use.add_argument("--account", default="default", help="Akun OAuth (untuk auth_type cli_oauth)")
```

**Catatan:** untuk provider `cli_oauth`, operator tetap harus `auth login --provider <id> --account <acc>`
lebih dulu agar token tersimpan; `auth use` hanya mengaitkan route + profil. Setelah keduanya, kredensial
masuk pool via `pool_builder` blok 3 (sudah ada).

**Langkah 3.** Test — TAMBAHKAN file baru `tests/unit/test_cli_auth_use.py`:

```python
"""auth use: membuat entri auth_profiles, route tidak menggantung (C4)."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path


def _write_min_settings(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text("llm:\n  enabled: false\n", encoding="utf-8")
    (cfg_dir / "oauth_providers.yaml").write_text(
        "oauth_providers:\n"
        "  generic_gateway:\n"
        "    label: gw\n"
        "    auth_mode: oauth2\n"
        "    capability: oauth_gateway\n"
        "    enabled: true\n"
        "    token_url_env: RTRADE_OAUTH_TOKEN_URL\n"
        "    client_id_env: RTRADE_OAUTH_CLIENT_ID\n"
        "    transport: HTTPS\n",
        encoding="utf-8",
    )
    return cfg_dir


def test_use_creates_auth_profile_entry(tmp_path, monkeypatch) -> None:
    import yaml

    cfg_dir = _write_min_settings(tmp_path)
    monkeypatch.chdir(tmp_path)  # _cmd_use membaca config/ relatif CWD

    from rtrade.cli.auth import _cmd_use

    _cmd_use(
        Namespace(
            role="analyst",
            provider="generic_gateway",
            model="openai/gpt-4.1",
            force=True,
            account="default",
        )
    )
    doc = yaml.safe_load((cfg_dir / "settings.yaml").read_text(encoding="utf-8"))
    routes = doc["llm"]["model_routes"]
    profiles = doc["llm"]["auth_profiles"]
    pname = routes["analyst"]["auth_profile"]
    assert pname in profiles  # route TIDAK menggantung
    assert profiles[pname]["auth_type"] == "cli_oauth"
    assert profiles[pname]["provider_id"] == "generic_gateway"
    assert profiles[pname]["enabled"] is True
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\cli\auth.py -Pattern "auth_profiles.*setdefault|profiles_cfg\[auth_profile_name\]"
```

**Commit:** `fix(cli): auth use also writes auth_profiles entry (no dangling route) (C4)`

---

## C5 — Credential pool dibangun SEKALI per proses (F5)

**Tujuan:** cooldown rate-limit bertahan antar-kandidat. Saat ini pool dibangun ulang tiap kandidat →
cooldown reset. Solusi paling aman untuk weak agent: **singleton modul** (tanpa mengubah signature loop).

**File:** `src/rtrade/pipeline/scan.py`

**Langkah 1.** GANTI fungsi `_build_llm_client` (saat ini baris 62-69) menjadi versi cache + redis:

```python
_SCAN_POOL_CACHE: Any = None


def _build_llm_client(cfg: AppConfig) -> Any:
    """LLMClient dengan credential pool singleton (C5).

    Pool dibangun SEKALI per proses lalu dipakai ulang → cooldown rate-limit
    bertahan antar-kandidat & antar-cycle. redis_client diteruskan supaya cooldown
    juga persisten di Redis (lintas proses).
    """
    global _SCAN_POOL_CACHE
    from rtrade.llm.client import LLMClient
    from rtrade.llm.pool_builder import build_scan_pool

    if _SCAN_POOL_CACHE is None:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(cfg.secrets.redis_url)
        except Exception:
            redis_client = None
        _SCAN_POOL_CACHE = build_scan_pool(cfg, redis_client=redis_client)

    return LLMClient(
        timeout=cfg.settings.llm.timeout_seconds,
        temperature=cfg.settings.llm.temperature,
        credential_pool=_SCAN_POOL_CACHE,
    )
```

**Catatan untuk agen:**
- `Any` sudah diimpor di scan.py (dipakai `_build_llm_client` lama). Jika belum, tambah `from typing import Any`.
- JANGAN mengubah pemanggilan `_build_llm_client(cfg)` di baris ±513 & ±890 — cukup ubah fungsinya.
- Konsekuensi yang HARUS ditulis di docstring (sudah): bila operator menambah kredensial baru (login/key),
  pool baru aktif setelah **restart proses**. Itu wajar untuk scheduler VPS.

**Langkah 2.** Test — TAMBAHKAN di `tests/unit/test_pool_builder.py`:

```python
def test_build_scan_pool_accepts_redis_client(monkeypatch, tmp_path) -> None:
    """build_scan_pool menerima redis_client tanpa error (cooldown persisten)."""
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path / "tok"))
    monkeypatch.setenv("RTRADE_ADC_DIR", str(tmp_path / "adc"))
    from rtrade.core.config import AppConfig, Secrets
    from rtrade.llm.pool_builder import build_scan_pool

    cfg = AppConfig.load()
    object.__setattr__(cfg, "secrets", Secrets(gemini_api_key_1="AIza1"))

    class _FakeRedis:  # cukup objek non-None; KeyManager hanya dipakai saat report/acquire async
        pass

    pool = build_scan_pool(cfg, redis_client=_FakeRedis())
    assert pool.size == 1
```

(Bila `AppConfig.load()` butuh CWD repo, biarkan apa adanya — test lain di file ini sudah memakai pola sama.)

**BUKTI:**
```powershell
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "_SCAN_POOL_CACHE|redis_client=redis_client"
# WAJIB: _SCAN_POOL_CACHE ≥3 (global decl + if None + assign), redis_client diteruskan
```

**Commit:** `fix(pipeline): build credential pool once per process + redis cooldown (C5)`

---

## C6 — `device_login` defensif + `_google_login` well-known best-effort (F6, F7)

**File:** `src/rtrade/llm/auth/oauth2.py`

**Langkah 1.** Di `device_login()` (cari `init.json()` / `d["user_code"]`), GANTI bagian parsing awal
respons device-init menjadi defensif. Cari blok:

```python
            init.raise_for_status()
            d = init.json()
            verification = d.get("verification_url") or d.get("verification_uri")
            logger.info("buka URL ini & masukkan kode", url=verification, code=d["user_code"])
            interval = float(d.get("interval", 5))
            device_code = d["device_code"]
```

GANTI menjadi:

```python
            init.raise_for_status()
            d = init.json()
            verification = (
                d.get("verification_url")
                or d.get("verification_uri")
                or d.get("verification_uri_complete")
            )
            user_code = d.get("user_code")
            device_code = d.get("device_code")
            if not device_code:
                raise RuntimeError(
                    f"{self.provider_id}: respons device-init tidak punya 'device_code' "
                    f"(field tersedia: {sorted(d.keys())}). Endpoint mungkin bukan RFC 8628."
                )
            logger.info("buka URL ini & masukkan kode", url=verification, code=user_code)
            interval = float(d.get("interval", 5))
```

(`user_code` boleh `None` tanpa membuat crash — hanya untuk ditampilkan.)

**File:** `src/rtrade/cli/auth.py`

**Langkah 2.** Di `_google_login`, blok well-known path (saat ini baris 72-77) — bungkus best-effort
supaya tidak crash di container read-only:

```python
    if account == "default":
        from pathlib import Path

        try:
            adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
            adc.parent.mkdir(parents=True, exist_ok=True)
            adc.write_text(payload, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "lewati tulis ADC well-known (FS read-only?) — pakai RTRADE_ADC_DIR",
                error=str(exc),
            )
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\llm\auth\oauth2.py -Pattern "bukan RFC 8628|device_code = d.get"
Select-String -Path src\rtrade\cli\auth.py -Pattern "lewati tulis ADC well-known"
```

**Commit:** `fix(auth): defensive device-code parsing + best-effort ADC well-known write (C6)`

---

## C7 — Login provider `external_command` diberi pesan jelas (F8)

**Tujuan:** `auth login --provider <external_command provider>` tidak crash dengan error obscure
(`device_auth_url` kosong). Beri pesan jelas "belum didukung" (jalur adapter eksternal belum diwujudkan).

**File:** `src/rtrade/cli/auth.py`

Di `_cmd_login`, cabang else (setelah cek `if not profile.enabled:` — saat ini sekitar baris 110, SEBELUM
`sid = account_store_id(...)`), TAMBAHKAN guard:

```python
        if profile.auth_mode == "external_command":
            print(  # noqa: T201
                f"Provider '{args.provider}' memakai auth_mode=external_command yang belum "
                "didukung jalur login bawaan. Gunakan provider API key / OAuth gateway, "
                "atau sediakan adapter eksternal sesuai docs/AUTH_OAUTH.md."
            )
            sys.exit(1)
```

**BUKTI:**
```powershell
Select-String -Path src\rtrade\cli\auth.py -Pattern "auth_mode=external_command yang belum"
```

**Commit:** `fix(cli): clear message for unsupported external_command login (C7)`

---

## C8 — Test integrasi token store di mode prod (F1/F2 regression guard)

**Tujuan:** kunci agar `save_token`+`load_token` round-trip di prod (ENV=prod + RTRADE_TOKEN_KEY) bekerja,
dan gagal jelas tanpa key. Mencegah regресi BLOCKER.

**File baru:** `tests/unit/test_token_store_prod.py`

```python
"""Token store prod-mode round-trip + fail-closed (C8)."""

from __future__ import annotations

import pytest

from rtrade.llm.auth.token_store import StoredToken, load_token, save_token


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_prod_with_key_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("RTRADE_TOKEN_KEY", _key())
    tok = StoredToken(access_token="abc", refresh_token="r", expiry_epoch=1.0, scopes=["s"])
    save_token("codex_oauth__utama", tok)
    got = load_token("codex_oauth__utama")
    assert got is not None
    assert got.access_token == "abc"


def test_prod_without_key_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)
    tok = StoredToken(access_token="abc", refresh_token=None, expiry_epoch=1.0, scopes=[])
    with pytest.raises(RuntimeError, match="RTRADE_TOKEN_KEY wajib di prod"):
        save_token("codex_oauth", tok)
```

**BUKTI:**
```powershell
Select-String -Path tests\unit\test_token_store_prod.py -Pattern "RTRADE_TOKEN_KEY wajib di prod|roundtrip"
```

**Commit:** `test(auth): prod token store roundtrip + fail-closed guard (C8)`

---

## C9 — Dokumentasi

**File:** `docs/AUTH_OAUTH.md` — tambahkan section di akhir:

```markdown
## Deploy OAuth di VPS (penting)

OAuth (Vertex/Azure/gateway/dll.) butuh penyimpanan token persisten + terenkripsi:

1. **`RTRADE_TOKEN_KEY` WAJIB di prod.** Digenerate otomatis oleh `setup_vps.sh`. Tanpa ini,
   `auth login` gagal ("RTRADE_TOKEN_KEY wajib di prod").
2. **Token disimpan di volume persisten** `/app/data/auth/{tokens,adc}` (di-set via
   `RTRADE_TOKEN_DIR`/`RTRADE_ADC_DIR` di `docker-compose.prod.yml`). Container prod `read_only`,
   jadi token TIDAK boleh ke `~/.rtrade` (root FS read-only & non-persisten).
3. **Routing:** `auth login --provider <id> --account <acc>` (simpan token) lalu
   `auth use --role <role> --provider <id> --model <m> --account <acc>` (kaitkan route + buat
   `auth_profiles`). Cek hasil: `auth pool`.
4. Tambah kredensial baru → **restart** container app (pool dibangun sekali per proses).
```

**File:** `.env.prod.example` — pastikan ada catatan token dir (TAMBAHKAN dekat `RTRADE_TOKEN_KEY`):

```bash
# Di container prod, token disimpan di volume: RTRADE_TOKEN_DIR & RTRADE_ADC_DIR
# di-set oleh docker-compose.prod.yml (/app/data/auth/...). Jangan set di sini.
```

**BUKTI:**
```powershell
Select-String -Path docs\AUTH_OAUTH.md -Pattern "Deploy OAuth di VPS|RTRADE_TOKEN_DIR"
```

**Commit:** `docs(auth): VPS OAuth deploy guide (token key + persistent dir) (C9)`

---

## C10 — GATE AKHIR + AUDIT (WAJIB, tempelkan output di laporan)

```powershell
$uv = "C:\Users\Dian Ganteng\.local\bin\uv.exe"
& $uv run ruff check src tests
& $uv run ruff format --check src tests
& $uv run mypy
& $uv run pytest tests/unit -q
& "C:\Program Files\Git\bin\bash.exe" -n scripts/setup_vps.sh
```

**Audit khusus C1–C7:**
```powershell
# C1: token dir persisten di 3 service
Select-String -Path docker-compose.prod.yml -Pattern "RTRADE_TOKEN_DIR|RTRADE_ADC_DIR"   # 6 baris
# C2: token key + menu
Select-String -Path scripts\setup_vps.sh -Pattern "RTRADE_TOKEN_KEY=\$\{TOKEN_KEY\}|Pilih provider LLM"
# C3: status multi-akun
Select-String -Path src\rtrade\cli\auth.py -Pattern "for acc in accs|account_store_id\(pid, acc\)"
# C4: auth use bikin auth_profiles
Select-String -Path src\rtrade\cli\auth.py -Pattern "auth_profiles.*setdefault"
# C5: pool singleton + redis
Select-String -Path src\rtrade\pipeline\scan.py -Pattern "_SCAN_POOL_CACHE"
# C6: device parsing defensif
Select-String -Path src\rtrade\llm\auth\oauth2.py -Pattern "bukan RFC 8628"
# C7: external_command guard
Select-String -Path src\rtrade\cli\auth.py -Pattern "external_command yang belum"
# Guard keamanan UTUH (WAJIB masih ada):
Select-String -Path src\rtrade\core\config.py -Pattern "_FORBIDDEN_KEY_PREFIXES|_reject_consumer_oauth"
Select-String -Path src\rtrade\llm\auth\provider_profiles.py -Pattern "_CONSUMER_TOKEN_SOURCES"
```

**Checklist laporan akhir (isi semua):**
- [ ] ruff check: 0 error
- [ ] ruff format --check: 0 perubahan
- [ ] mypy: 0 error
- [ ] pytest unit: semua lulus (tulis jumlah test, harus ≥ baseline + test baru)
- [ ] bash -n setup_vps.sh: exit 0
- [ ] Audit C1–C7: semua pola muncul
- [ ] Guard `_FORBIDDEN_KEY_PREFIXES`, `_reject_consumer_oauth`, `_CONSUMER_TOKEN_SOURCES` UTUH
- [ ] Tidak ada log yang mencetak token/api_key mentah (cek diff)
- [ ] 9 commit terpisah (C1–C9) + commit gate ini (C10)

**Commit terakhir:** `chore(auth): final gate + audit for deploy fixes (C10)`

---

## Lampiran — Apa yang TIDAK diperbaiki plan ini (sengaja)
- Entri manifest `codex_oauth`/`xai_oauth` (endpoint konsumen) **tidak disentuh** — itu konfigurasi &
  tanggung jawab operator. Plan ini hanya memperbaiki plumbing generik; begitu operator `login` + `use`
  provider mana pun (termasuk yang dia aktifkan sendiri), mekanisme generik di atas otomatis memakainya.
- Tidak ada kode baru yang membaca `~/.codex`/cookie/session app lain. Guard tetap.
- Validasi end-to-end terhadap endpoint OAuth eksternal (apakah benar membalas token) di luar lingkup —
  itu bergantung pada layanan pihak ketiga, bukan bug kode kita.
