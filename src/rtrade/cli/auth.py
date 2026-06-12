"""Login OAuth: python -m rtrade.cli.auth login --provider google

google  → device/installed-app flow via google-auth-oauthlib (scope cloud-platform),
          simpan refresh token ke ADC default (well-known location google).
generic → OAuth2Provider.device_login() memakai config dari env (token store rtrade).
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


def _google_login(flow_override: str | None = None) -> None:
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


def _cmd_login(args: argparse.Namespace) -> None:
    flow = getattr(args, "flow", None)
    if args.provider == "google":
        _google_login(flow_override=flow)
    elif args.provider == "generic":
        from rtrade.llm.auth.registry import build_generic_oauth_from_env

        asyncio.run(build_generic_oauth_from_env().device_login())
    else:
        # Hermes-style: load profile and login via appropriate flow
        from rtrade.llm.auth.provider_profiles import load_provider_profiles
        from rtrade.llm.auth.registry import build_provider_from_profile

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
        provider = build_provider_from_profile(args.provider)
        asyncio.run(provider.device_login())


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
    from rtrade.llm.auth.token_store import load_token

    providers = [args.provider] if args.provider else _all_provider_ids()
    for pid in providers:
        tok = load_token(pid)
        if tok is None:
            print(f"{pid}: not_logged_in")  # noqa: T201
        else:
            import datetime

            exp = datetime.datetime.fromtimestamp(tok.expiry_epoch, tz=datetime.UTC)
            print(  # noqa: T201
                f"{pid}: logged_in, expires={exp.isoformat()}, scopes={tok.scopes}"
            )


def _cmd_logout(args: argparse.Namespace) -> None:
    from rtrade.llm.auth.token_store import delete_token

    if delete_token(args.provider):
        print(f"Token {args.provider} dihapus.")  # noqa: T201
    else:
        print(f"Tidak ada token untuk {args.provider}.")  # noqa: T201


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
    except Exception as e:
        print(f"  error: {e}")  # noqa: T201


def _all_provider_ids() -> list[str]:
    try:
        from rtrade.llm.auth.provider_profiles import load_provider_profiles

        return list(load_provider_profiles(None).keys())
    except Exception:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(prog="rtrade-auth", description="OAuth auth management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    login = sub.add_parser("login", help="Login ke provider")
    login.add_argument("--provider", required=True)
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

    doctor = sub.add_parser("doctor", help="Diagnose provider config")
    doctor.add_argument("--provider", required=True)

    args = parser.parse_args()
    dispatch = {
        "login": _cmd_login,
        "providers": _cmd_providers,
        "status": _cmd_status,
        "logout": _cmd_logout,
        "doctor": _cmd_doctor,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
