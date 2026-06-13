"""Login OAuth: python -m rtrade.cli.auth login --provider google --account utama

google  → device/installed-app flow via google-auth-oauthlib (scope cloud-platform),
          simpan refresh token ke ADC per-akun (& well-known path bila akun default).
generic → OAuth2Provider.device_login() memakai config dari env (token store rtrade).
codex_oauth / xai_oauth → Device Code Flow via manifest (Hermes-style).
A5: multi-akun per provider, subcommand accounts, fallback pool.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import structlog

logger = structlog.get_logger(__name__)

_GOOGLE_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"env {name} wajib diisi (lihat docs/AUTH_OAUTH.md)")
    return val


def _google_login(flow_override: str | None = None, account: str = "default") -> None:
    from rtrade.llm.auth.login_flows import LoginFlow, auto_flow

    flow_kind = auto_flow(flow_override)
    client_secrets = _require_env("GOOGLE_OAUTH_CLIENT_SECRETS")

    from google_auth_oauthlib.flow import InstalledAppFlow

    gflow = InstalledAppFlow.from_client_secrets_file(client_secrets, scopes=_GOOGLE_SCOPES)

    if flow_kind == LoginFlow.LOOPBACK:
        creds = gflow.run_local_server(port=0)
    else:  # PASTE_URL (VPS-friendly)
        gflow.redirect_uri = "http://localhost:1"
        auth_url, _ = gflow.authorization_url(prompt="consent", access_type="offline")
        logger.info(
            "buka URL ini, login, lalu SALIN URL halaman error & tempel di bawah",
            url=auth_url,
        )
        redirect_response = input("Tempel URL redirect lengkap di sini: ").strip()
        gflow.fetch_token(authorization_response=redirect_response)
        creds = gflow.credentials

    # Simpan ADC per-akun; akun 'default' juga ditulis ke well-known path supaya
    # google-auth & litellm lama tetap bekerja tanpa konfigurasi.
    import json

    from rtrade.llm.auth.token_store import account_store_id  # validasi nama akun
    from rtrade.llm.auth.vertex import adc_path_for

    account_store_id("google", account)  # raise ValueError bila nama akun tidak valid
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

        try:
            adc = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
            adc.parent.mkdir(parents=True, exist_ok=True)
            adc.write_text(payload, encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "lewati tulis ADC well-known (FS read-only?) — pakai RTRADE_ADC_DIR",
                error=str(exc),
            )
    logger.info("google login sukses — ADC tersimpan", account=account, path=str(per_account))


def _cmd_login(args: argparse.Namespace) -> None:
    flow = getattr(args, "flow", None)
    account = getattr(args, "account", "default")

    if args.provider == "google":
        _google_login(flow_override=flow, account=account)
    elif args.provider == "generic":
        from rtrade.llm.auth.registry import build_generic_oauth_from_env

        asyncio.run(build_generic_oauth_from_env().device_login())
    else:
        # Hermes-style: load profile and login via appropriate flow
        from rtrade.llm.auth.provider_profiles import load_provider_profiles
        from rtrade.llm.auth.registry import build_provider_from_profile
        from rtrade.llm.auth.token_store import account_store_id

        profiles = load_provider_profiles(None)
        if args.provider not in profiles:
            print(  # noqa: T201
                f"Provider '{args.provider}' tidak ditemukan. "
                f"Tersedia: {', '.join(profiles.keys())}"
            )
            sys.exit(1)
        profile = profiles[args.provider]
        if not profile.enabled:
            print(  # noqa: T201
                f"Provider '{args.provider}' disabled. "
                f"Catatan: {profile.note or 'Aktifkan di oauth_providers.yaml'}"
            )
            sys.exit(1)
        if profile.auth_mode == "external_command":
            print(  # noqa: T201
                f"Provider '{args.provider}' memakai auth_mode=external_command yang belum "
                "didukung jalur login bawaan. Gunakan provider API key / OAuth gateway, "
                "atau sediakan adapter eksternal sesuai docs/AUTH_OAUTH.md."
            )
            sys.exit(1)
        # Build store_id = provider__account
        sid = account_store_id(args.provider, account)
        provider = build_provider_from_profile(args.provider, store_id=sid)
        asyncio.run(provider.device_login())
        print(f"✓ Login berhasil — token tersimpan ({sid})")  # noqa: T201


def _cmd_providers(_args: argparse.Namespace) -> None:
    from rtrade.llm.auth.provider_profiles import load_provider_profiles

    profiles = load_provider_profiles(None)
    print(  # noqa: T201
        f"{'ID':<22} {'Label':<35} {'Mode':<12} {'Capability':<28} {'Enabled'}"
    )
    print("-" * 105)  # noqa: T201
    for pid, p in profiles.items():
        print(  # noqa: T201
            f"{pid:<22} {p.label:<35} {p.auth_mode:<12} {p.capability:<28} {p.enabled}"
        )


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


def _cmd_logout(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.token_store import account_store_id, delete_token

    account = getattr(args, "account", "default")
    sid = account_store_id(args.provider, account)
    if delete_token(sid):
        print(f"Token {sid} dihapus.")  # noqa: T201
    else:
        print(f"Tidak ada token untuk {sid}.")  # noqa: T201


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


def _cmd_doctor(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.provider_profiles import load_provider_profiles, resolve_env_profile

    profiles = load_provider_profiles(None)
    if args.provider not in profiles:
        print(f"Provider '{args.provider}' tidak ditemukan.")  # noqa: T201
        return
    profile = profiles[args.provider]
    print(f"Provider: {profile.label} ({args.provider})")  # noqa: T201
    print(f"  auth_mode: {profile.auth_mode}")  # noqa: T201
    print(f"  capability: {profile.capability}")  # noqa: T201
    print(f"  enabled: {profile.enabled}")  # noqa: T201
    try:
        resolved = resolve_env_profile(profile)
        print(f"  token_url: {'set' if resolved.token_url else 'missing'}")  # noqa: T201
        print(f"  client_id: {'set' if resolved.client_id else 'missing'}")  # noqa: T201
        print(f"  device_auth_url: {'set' if resolved.device_auth_url else 'missing'}")  # noqa: T201
    except Exception as e:
        print(f"  error: {e}")  # noqa: T201


def _all_provider_ids() -> list[str]:
    try:
        from rtrade.llm.auth.provider_profiles import load_provider_profiles

        return list(load_provider_profiles(None).keys())
    except Exception:
        return []


def _cmd_models(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.model_catalog import list_provider_models
    from rtrade.llm.auth.provider_profiles import load_provider_profiles

    profiles = load_provider_profiles(None)
    if args.provider not in profiles:
        print(f"Provider '{args.provider}' tidak ditemukan.")  # noqa: T201
        return
    models = asyncio.run(list_provider_models(profiles[args.provider]))
    print(f"Models for {args.provider}:")  # noqa: T201
    for m in models:
        print(f"  - {m}")  # noqa: T201


def _cmd_use(args: argparse.Namespace) -> None:
    """Set model_routes[role] to use specific provider + model."""
    from pathlib import Path

    import yaml

    from rtrade.llm.auth.model_catalog import list_provider_models
    from rtrade.llm.auth.provider_profiles import load_provider_profiles

    profiles = load_provider_profiles(None)
    if args.provider not in profiles:
        print(f"Provider '{args.provider}' tidak ditemukan.")  # noqa: T201
        sys.exit(1)
    profile = profiles[args.provider]
    if not profile.enabled:
        print(  # noqa: T201
            f"Provider '{args.provider}' disabled. "
            f"Jalankan: rtrade auth login --provider {args.provider}"
        )
        sys.exit(1)

    # Validate model in catalog (unless --force)
    if not getattr(args, "force", False):
        models = asyncio.run(list_provider_models(profile))
        if models and args.model not in models:
            print(  # noqa: T201
                f"Model '{args.model}' tidak ada di katalog {args.provider}. "
                f"Tersedia: {models}. Gunakan --force untuk override."
            )
            sys.exit(1)

    # Determine auth_profile name for this provider
    auth_profile_name = f"{args.provider}_cli_oauth"
    if profile.auth_mode == "vertex":
        auth_profile_name = f"{args.provider}_vertex"
    elif profile.auth_mode == "api_key":
        auth_profile_name = f"{args.provider}_api_key"

    # Update settings.yaml
    settings_path = Path("config") / "settings.yaml"
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    else:
        doc = {}

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

    print(  # noqa: T201
        f"role={args.role} → model={args.model} "
        f"via provider={args.provider} (auth={profile.auth_mode})"
    )


def _cmd_pool(_args: argparse.Namespace) -> None:
    """Tampilkan isi credential pool + status tiap credential."""
    from rtrade.core.config import AppConfig
    from rtrade.llm.pool_builder import build_credential_pool

    try:
        cfg = AppConfig.load()
        pool = build_credential_pool(cfg)
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="rtrade-auth", description="OAuth auth management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Login ke provider")
    login.add_argument("--provider", required=True)
    login.add_argument(
        "--account",
        default="default",
        help="Label akun (multi-akun per provider, mis. 'kerja', 'pribadi')",
    )
    login.add_argument(
        "--flow",
        choices=["loopback", "paste_url", "device_code"],
        default=None,
        help="Login flow (default: auto-detect)",
    )

    sub.add_parser("providers", help="List available OAuth providers")

    status = sub.add_parser("status", help="Check token status")
    status.add_argument("--provider", default=None)

    logout = sub.add_parser("logout", help="Remove stored token")
    logout.add_argument("--provider", required=True)
    logout.add_argument("--account", default="default")

    doctor = sub.add_parser("doctor", help="Diagnose provider config")
    doctor.add_argument("--provider", required=True)

    models = sub.add_parser("models", help="List models for a provider")
    models.add_argument("--provider", required=True)

    use = sub.add_parser("use", help="Set model route for a role")
    use.add_argument("--role", required=True, choices=["analyst", "critic", "backup", "flagship"])
    use.add_argument("--provider", required=True)
    use.add_argument("--model", required=True)
    use.add_argument("--force", action="store_true", help="Skip model catalog validation")
    use.add_argument("--account", default="default", help="Akun OAuth (untuk auth_type cli_oauth)")

    accounts = sub.add_parser("accounts", help="List akun tersimpan per provider")
    accounts.add_argument("--provider", required=True)

    sub.add_parser("pool", help="Tampilkan credential pool + status")

    args = parser.parse_args()
    dispatch = {
        "login": _cmd_login,
        "providers": _cmd_providers,
        "status": _cmd_status,
        "logout": _cmd_logout,
        "doctor": _cmd_doctor,
        "models": _cmd_models,
        "use": _cmd_use,
        "accounts": _cmd_accounts,
        "pool": _cmd_pool,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
