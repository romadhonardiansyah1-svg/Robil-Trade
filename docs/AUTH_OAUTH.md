# RTrade OAuth Authentication Guide

## Overview

RTrade supports multiple authentication modes for LLM API calls:

| Mode | Config value | How it works |
|------|-------------|--------------|
| **API Key** (default) | `api_key` | Uses `gemini_api_key_1` from `.env` — no change needed |
| **Vertex AI** | `vertex` | Google ADC (Application Default Credentials) via OAuth or service account |
| **OAuth2 Gateway** | `oauth2` | Standard OAuth2 client_credentials / device_code with a gateway |
| **Azure AD** | `azure_ad` | Azure Active Directory credentials (requires `azure-identity`) |

## Quick Start

### 1. API Key (default, no changes needed)

```yaml
# config/settings.yaml
llm:
  auth_mode: api_key   # default
```

Set `GEMINI_API_KEY_1` in `.env`.

### 2. Vertex AI (Google Cloud)

```bash
# Login via CLI
python -m rtrade.cli.auth login --provider google

# Or use service account
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
```

```yaml
# config/settings.yaml
llm:
  auth_mode: vertex
  vertex_project: my-gcp-project
  vertex_location: us-central1
```

### 3. OAuth2 Gateway (Enterprise)

```bash
# Set env vars
export RTRADE_OAUTH_TOKEN_URL=https://gateway.corp.com/oauth/token
export RTRADE_OAUTH_CLIENT_ID=rtrade-bot
export RTRADE_OAUTH_CLIENT_SECRET=***
export RTRADE_OAUTH_SCOPES="llm.complete"
```

```yaml
# config/settings.yaml
llm:
  auth_mode: oauth2
```

## CLI Commands

```bash
# Login
python -m rtrade.cli.auth login --provider google
python -m rtrade.cli.auth login --provider generic

# Check status
python -m rtrade.cli.auth status

# List providers
python -m rtrade.cli.auth providers

# Diagnose
python -m rtrade.cli.auth doctor --provider google_vertex

# Logout
python -m rtrade.cli.auth logout --provider google_vertex
```

## Security Rules

1. **Tokens NEVER enter `*_api_key` fields** — they flow through the token store
2. **NEVER log tokens/secrets** — only log `provider_id`, `mode`, `expires_at`
3. **Token files are chmod 0600** (on Linux/macOS)
4. **Optional Fernet encryption** via `RTRADE_TOKEN_KEY` env var
5. **Consumer OAuth tokens are FORBIDDEN** — `sk-ant-oat*` prefix is rejected at config load

## Token Storage

Tokens are stored in `~/.rtrade/tokens/<provider>.json`.

- Set `RTRADE_TOKEN_DIR` to customize location
- Set `RTRADE_TOKEN_KEY` to a Fernet key for encryption at rest
- Generate a key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
