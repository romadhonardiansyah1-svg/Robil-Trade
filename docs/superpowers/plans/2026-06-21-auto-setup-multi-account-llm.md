# Rencana Implementasi — Auto-Setup Penuh + Multi-Akun LLM + Fallback Limit 5 Jam

- Tanggal: 2026-06-21
- Desain: `docs/superpowers/specs/2026-06-21-auto-setup-multi-account-llm-design.md`
- Metode: subagent-driven, persona-per-agen, TDD RED→GREEN, satu commit per task,
  Conventional Commits. Gate tiap task: `ruff check` + `mypy --strict` + `pytest` GREEN.
- Branch: `feat/auto-setup-multi-account-llm` (JANGAN kerja di `main`).
- Base: `main` saat ini (remediation sudah live).

## Prinsip
- Pakai infrastruktur yang ADA (`OAuth2Provider.device_login`, `build_scan_pool`, `KeyManager`,
  `account_store_id`, `_cmd_use`). Jangan bangun ulang.
- Setiap perubahan signal-only & fail-closed. Tidak ada nilai rahasia di log.
- Backward-compatible: config lama tanpa blok `llm.pool` tetap valid.

---

## FASE 0 — Persiapan branch
- **T0.1** Buat branch `feat/auto-setup-multi-account-llm` dari `main`. Verifikasi suite hijau
  sebagai baseline (`pytest -q`, `ruff`, `mypy`). Tidak ada perubahan kode.

## FASE 2 — Kesetiaan OAuth per provider (G7/G8/G10) — "maksimalkan"
Persona: *OAuth/identity engineer*. Pelajari & cocokkan flow Hermes per provider.

- **T2A.1 `login_flow` dihormati di dispatch.** Tambah enum/konstanta flow
  (`device_code` | `pkce_loopback` | `paste_url` | `paste_code`) dan buat `_cmd_login`/pustaka
  memilih jalur sesuai `login_flow` profil — bukan selalu `device_login()`.
  - RED: profil `login_flow=device_code` → panggil device_login; `pkce_loopback`/`paste_url` →
    panggil jalur PKCE (mock); `paste_code` → jalur paste. Codex tetap device-code.
  - GREEN: implementasi dispatch.

- **T2A.2 PKCE loopback + manual-paste (xAI/Qwen-style).** Bungkus
  `generate_pkce_pair`/`build_authorize_url`/`exchange_pasted_redirect` jadi alur lengkap:
  listener loopback `127.0.0.1:<port>` bila ada browser; `--manual-paste`/headless → cetak URL +
  terima tempelan (URL penuh / `?code=&state=` / kode telanjang). Verifier/state/nonce sama untuk
  kedua jalur. RED: state-mismatch→error; tempelan 3 bentuk diterima; token tersimpan. GREEN.

- **T2A.3 FIX manifest xAI (G7).** Ubah `xai_oauth` → `login_flow: pkce_loopback`, isi
  authorize-url/token-url/redirect/port via env (`RTRADE_XAI_*`) + default yang DIVERIFIKASI dari
  Hermes/dikonfirmasi user (JANGAN tebak). Tambah field `login_flow` & redirect/port ke
  `OAuthProviderProfile` + loader + validasi. RED: profil xAI tervalidasi sebagai pkce_loopback;
  device_code lama tidak lagi dipakai untuk xAI. GREEN.

- **T2A.4 Deteksi headless + petunjuk SSH.** Deteksi `$SSH_CONNECTION`/tanpa `$DISPLAY`/flag →
  default paste-mode + cetak `ssh -N -L <port>:127.0.0.1:<port> user@host`. Tambah
  `--manual-paste`/`--no-browser` ke `auth login` & wizard. RED: env headless→paste-mode dipilih.
  GREEN. Update `docs/AUTH_OAUTH.md` (matriks flow + SSH tunnel).

## FASE 2B — Jangkauan model luas gaya Hermes (G9)
Persona: *LLM-platform engineer*. (Opsional-tinggi; bisa ditunda bila user ingin scope minimal.)

- **T2B.1 Jalur OpenRouter.** Tambah `OPENROUTER_API_KEY` ke `Secrets`, flavor `openrouter` di
  `pool_builder`/`model_flavor`, pemanggilan litellm (`openrouter/<model>`). RED: key terisi →
  entri pool `openrouter`. GREEN.

- **T2B.2 Custom OpenAI-compatible endpoint.** Tambah `custom_providers` (name, base_url, key_env)
  ke config + auth_profile bawa `base_url`; litellm dipanggil dengan `api_base`. RED: config custom
  → entri pool dengan base_url benar; key precedence aman (tak bocor ke endpoint lain). GREEN.

## FASE 3 — Wizard model AI (Python CLI) gaya `hermes model`
Persona: *CLI/UX + secure-config engineer*.

- **T3.1 Ekstrak pustaka inti (refactor aman).** Pindahkan inti penulisan route dari `_cmd_use`
  ke `llm/auth/routing.py: set_model_route(...)` + helper login bersama. RED: tes pustaka (api_key/
  vertex/cli_oauth + base_url). GREEN; `_cmd_use` memanggil pustaka; regresi CLI lama lulus.

- **T3.2 `rtrade setup wizard` — menu provider multi-entri.** Menu katalog provider (6.1b),
  loop "tambah provider/model lain?", tiap entri → jalur API key atau OAuth (flow benar via Fase 2),
  tulis `auth_profiles` + slot `.env`. RED: input ter-mock membangun banyak entri; idempoten; tolak
  `sk-ant-oat*`. GREEN.

- **T3.3 Pemetaan entri→peran + fallback chain.** Setelah entri terkumpul, petakan ke
  analyst/critic/flagship (default urut) via pustaka T3.1; sisanya tetap di pool. RED: peta peran
  benar di `model_routes`. GREEN.

- **T3.4 `rtrade setup verify`.** `build_scan_pool` + ringkas (`auth pool`); exit≠0 bila kosong.
  RED/GREEN.

## FASE 4 — Cooldown adaptif (fallback limit 5 jam)
*(Tetap seperti rencana awal — lihat Fase 1 lama; dipindah ke sini agar urutan dependensi jelas.)*
- **T4.1 (G6)** Klasifikasi `subscription_limit` vs `rate_limit` vs `auth` vs `other`.
- **T4.2 (G4)** Override durasi cooldown di `KeyManager`/`CredentialPool`.
- **T4.3** Config `llm.pool` (cooldown/auth_cooldown/subscription_cooldown, cap ≤ 21600s).
- **T4.4** Peta kind→cooldown di pipeline (+ hormati `Retry-After`).
- **T4.5** Dokumentasi cooldown adaptif.

## FASE 5 — Backfill otomatis + integrasi `setup_vps.sh`
Persona: *DevOps/automation engineer*.
- **T5.1** Backfill otomatis (fail-soft, idempoten, `--all`).
- **T5.2** Integrasi `setup_vps.sh`: ramping step 5 (non-LLM) → build → migrate →
  `exec app rtrade setup wizard` → backfill → verify (`setup verify`+`auth pool`); petunjuk
  SSH/manual-paste untuk OAuth PKCE. `bash -n` syntax check.

## FASE 6 — Review & finishing
- **T6.1** Review menyeluruh (persona *security/quant reviewer*).
- **T6.2** Gate akhir: `ruff` + `mypy --strict` + `pytest` GREEN.
- **T6.3** Finishing-branch: sajikan opsi (merge/PR/push) — TUNGGU keputusan user.

## FASE 5C — Ops Chat Tingkat 1 (READ-ONLY) — K4
Persona: *Bot/observability engineer*. Risiko rendah (tanpa akses tulis).
- **T5C.1 Pengumpul snapshot read-only.** Fungsi yang merangkai status teredaksi: health, metrics
  ringkas, sinyal terakhir, error/log terbaru, biaya LLM (`get_daily_cost`), status credential pool
  (`auth pool`). RED: snapshot berisi field yang benar + TANPA rahasia (uji redaksi). GREEN.
- **T5C.2 Perintah Telegram/API.** `/status`, `/health`, `/errors`, `/pool`, `/cost`, `/why <id>`,
  `/ask <pertanyaan>`. `/ask` mengirim snapshot read-only sebagai konteks ke LLM dari pool yang ada,
  jawab dalam bahasa Indonesia. TIDAK ada perintah tulis/eksekusi. RED: handler mengembalikan
  ringkasan; `/ask` memanggil LLM dengan konteks teredaksi (mock). GREEN.
- **T5C.3 Guard chat-id.** Hanya `TELEGRAM_CHAT_ID` pemilik yang dilayani (sudah ada pola). Pastikan
  perintah baru ikut guard. RED: chat-id lain ditolak. GREEN.
- Catatan: Tingkat 2 (diagnose+patch) & Tingkat 3 (auto-apply) = DI LUAR SCOPE (spec terpisah, K5).

## Catatan scope (TERKUNCI K1–K6)
- Termasuk: provider luas (OpenRouter+custom), OAuth fidelity (Codex+xAI-FIX+Google-CLI),
  fallback limit-5-jam, wizard, backfill otomatis, setup_vps, **ops chat Tingkat 1 read-only**.
- TIDAK termasuk: self-healing auto-apply; diagnose+patch gated (jadi spec terpisah).

---

## Urutan eksekusi & dependensi
- **Fase 1** (branch) dulu.
- **Fase 2** (OAuth fidelity) + **Fase 4** (cooldown adaptif) mandiri — bisa lebih dulu & berdiri
  sendiri. Fase 4 = inti permintaan "fallback limit 5 jam".
- **Fase 2B** (jangkauan luas/OpenRouter) opsional-tinggi; bergantung tidak ke yang lain. Bisa
  ditunda bila user mau scope minimal dulu.
- **Fase 3** (wizard) bergantung T3.1 (refactor) + Fase 2 (flow OAuth benar) + (opsional) Fase 2B.
- **Fase 5** (`setup_vps.sh`) bergantung Fase 3; T5.1 (backfill) bisa paralel.
- Dispatch implementer **satu per satu**. Investigasi read-only boleh paralel.

## Keputusan untuk user (mempengaruhi cakupan)
- **K1.** Sertakan Fase 2B (OpenRouter + custom endpoint) sekarang? OpenRouter = paling dekat dengan
  "300+ model 1 login" gaya Nous Portal. (Rekomendasi: YA, minimal OpenRouter.)
- **K2.** Provider OAuth langganan yang diprioritaskan: Codex (siap) + xAI (perlu FIX). Tambah
  Google-Gemini-CLI OAuth & Anthropic/Copilot? (default: Codex + xAI dulu.)
- **K3.** Konstanta xAI PKCE (client_id/URL/port 56121): saya isi default dari studi Hermes lalu
  Anda verifikasi, atau Anda kirim nilainya? (default: env-driven + verifikasi.)

## Definition of Done
- User menjalankan satu perintah di VPS, hanya menjawab wizard model di awal; install + secrets +
  build + migrate + wizard (OAuth/API key, multi-akun) + backfill + verify berjalan otomatis.
- Saat akun kena limit langganan (~5 jam), akun itu di-cooldown panjang & pipeline rotasi ke akun
  berikutnya; 429 transien tetap cooldown pendek. Teruji unit.
- `ruff` + `mypy --strict` + `pytest` GREEN. Dokumentasi diperbarui.
