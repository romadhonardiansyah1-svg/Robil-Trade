# OAuth Provider Onboarding Runbook

## Cara Menambahkan Provider Baru

### 1. Definisikan di Manifest

Tambah entri di `config/oauth_providers.yaml`:

```yaml
oauth_providers:
  my_new_provider:
    label: "My Provider Name"
    auth_mode: oauth2          # vertex | oauth2 | azure_ad | external_command
    capability: oauth_gateway  # vertex_adc | oauth_gateway | disabled_unsupported | external_adapter
    enabled: false             # false sampai semua checklist selesai
    token_url_env: MY_PROVIDER_TOKEN_URL
    client_id_env: MY_PROVIDER_CLIENT_ID
    scopes_env: MY_PROVIDER_SCOPES
    device_auth_url_env: MY_PROVIDER_DEVICE_URL
    login_flow: device_code    # loopback | paste_url | device_code
    requires_official_oauth: true
    transport: HTTPS
    models:
      - my_provider/model-name
```

### 2. Checklist Legal

- [ ] Endpoint dari OAuth resmi provider (bukan reverse-engineered)
- [ ] Scopes jelas dan minimal
- [ ] Grant type sesuai (client_credentials / device_code / authorization_code)
- [ ] Rate limit didokumentasikan
- [ ] Revoke path tersedia (`/revoke` endpoint)
- [ ] Tidak menggunakan consumer token (ChatGPT session, Codex CLI, browser cookie)

### 3. Checklist Security

- [ ] Transport HTTPS wajib
- [ ] Secret via env var, bukan hardcode
- [ ] Token store terenkripsi (Fernet via `RTRADE_TOKEN_KEY`)
- [ ] Tidak ada consumer session import
- [ ] Token/secret TIDAK masuk ke log (structlog redaction)

### 4. Checklist Test

```bash
# Doctor check
python -m rtrade.cli.auth doctor --provider my_new_provider

# Login di sandbox
python -m rtrade.cli.auth login --provider my_new_provider --flow paste_url

# Refresh test
python -m rtrade.cli.auth status --provider my_new_provider

# Logout + cleanup
python -m rtrade.cli.auth logout --provider my_new_provider

# Audit log: pastikan tidak ada token di log
grep -i "token\|secret\|bearer" logs/rtrade.log
```

### 5. Aktifkan

Setelah semua checklist hijau:

```yaml
  my_new_provider:
    enabled: true   # ✅
```

## Audit Log Events

Events yang dilog (non-secret):

| Event | Fields yang boleh |
|-------|-------------------|
| `auth_login_started` | `provider_id`, `capability`, `grant_type` |
| `auth_login_succeeded` | `provider_id`, `expires_at` |
| `auth_login_failed` | `provider_id`, `error_type` (bukan error detail) |
| `auth_logout` | `provider_id` |

Fields yang **DILARANG** dilog:
- `access_token`, `refresh_token`, `client_secret`
- Raw authorization header
- Cookie values
