"""Interactive setup wizard: pick MANY providers × models, like `hermes model`.

`python -m rtrade.cli.setup wizard`  → at setup time, choose providers and models,
per provider pick API key (multi-key) or the correct OAuth flow, then map entries
to roles (analyst/critic/flagship).
`python -m rtrade.cli.setup verify`  → load config + build the credential pool and
print a table; exit 0 if the pool has entries, 1 if empty.

Design notes:
- Pure, I/O-free helpers (`upsert_env_var`, `auth_mode_for`, `PROVIDER_CATALOG`) are
  separated from the interactive `wizard`/`verify` for testability.
- `print()`/`input()` are intentional here (interactive CLI) — see the `# noqa: T201`
  pattern in cli/auth.py.
- Secret VALUES are NEVER logged or echoed. Consumer OAuth tokens (`sk-ant-oat*`) are
  rejected at the prompt. Reuses `set_model_route`, `build_scan_pool`, `perform_login`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import sys

from rtrade.cli.auth import perform_login
from rtrade.core.config import AppConfig
from rtrade.core.errors import ConfigError
from rtrade.llm.auth.routing import set_model_route
from rtrade.llm.pool_builder import build_scan_pool

# Consumer-subscription OAuth tokens are forbidden (ToS) — mirror core.config guard.
_FORBIDDEN_KEY_PREFIXES = ("sk-ant-oat",)
_MAX_API_KEYS = 5


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    """One catalog row in the wizard's provider menu."""

    menu_label: str
    kind: str  # "api_key" | "oauth_device" | "oauth_pkce" | "oauth_vertex"
    flavor: str
    suggested_models: list[str] = field(default_factory=list)
    env_prefix: str = ""  # api_key: e.g. "GEMINI_API_KEY"
    provider_id: str = ""  # oauth: manifest provider_id


PROVIDER_CATALOG: list[ProviderEntry] = [
    ProviderEntry(
        menu_label="Gemini",
        kind="api_key",
        flavor="gemini",
        env_prefix="GEMINI_API_KEY",
        suggested_models=["gemini/gemini-2.5-pro", "gemini/gemini-2.5-flash"],
    ),
    ProviderEntry(
        menu_label="Anthropic",
        kind="api_key",
        flavor="anthropic",
        env_prefix="ANTHROPIC_API_KEY",
        suggested_models=["anthropic/claude-sonnet-4-5", "anthropic/claude-opus-4-1"],
    ),
    ProviderEntry(
        menu_label="OpenAI",
        kind="api_key",
        flavor="openai",
        env_prefix="OPENAI_API_KEY",
        suggested_models=["openai/gpt-4.1", "openai/gpt-4.1-mini"],
    ),
    ProviderEntry(
        menu_label="xAI",
        kind="api_key",
        flavor="xai",
        env_prefix="XAI_API_KEY",
        suggested_models=["xai/grok-4", "xai/grok-3"],
    ),
    ProviderEntry(
        menu_label="OpenRouter",
        kind="api_key",
        flavor="openrouter",
        env_prefix="OPENROUTER_API_KEY",
        suggested_models=[
            "openrouter/anthropic/claude-sonnet-4",
            "openrouter/google/gemini-2.5-pro",
            "openrouter/openai/gpt-4.1",
        ],
    ),
    ProviderEntry(
        menu_label="OpenAI Codex (subscription)",
        kind="oauth_device",
        flavor="openai",
        provider_id="codex_oauth",
        suggested_models=["openai/gpt-4.1", "openai/o4-mini"],
    ),
    ProviderEntry(
        menu_label="xAI Grok (SuperGrok)",
        kind="oauth_pkce",
        flavor="xai",
        provider_id="xai_oauth",
        suggested_models=["xai/grok-4"],
    ),
    ProviderEntry(
        menu_label="Google Vertex",
        kind="oauth_vertex",
        flavor="vertex_ai",
        provider_id="google",
        suggested_models=["vertex_ai/gemini-2.5-pro"],
    ),
]

_ROLES = ("analyst", "critic", "flagship")


def upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Set ``KEY=value`` in ``env_path`` without ever logging the value.

    Creates the file when missing. Replaces an existing ``KEY=`` line in place,
    preserving all other lines and their order; otherwise appends ``KEY=value``.
    On non-Windows, tightens permissions to 0600.
    """
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    new_line = f"{key}={value}"
    replaced = False
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"{key}=") or stripped == key:
            lines[idx] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(env_path, 0o600)


def auth_mode_for(kind: str) -> str:
    """Map a catalog ``kind`` to a ``set_model_route`` ``auth_mode``."""
    if kind == "api_key":
        return "api_key"
    if kind == "oauth_vertex":
        return "vertex"
    return "cli_oauth"  # oauth_device / oauth_pkce


def _route_provider_id(entry: ProviderEntry) -> str:
    """Stable provider_id label for the auth_profile name.

    api_key → the flavor (e.g. "gemini" → auth_profile "gemini_api_key"); this keeps
    the profile name aligned with the Secrets family the pool reads. oauth → the
    manifest provider_id.
    """
    return entry.flavor if entry.kind == "api_key" else entry.provider_id


def _prompt(text: str) -> str:
    return input(text).strip()  # interactive CLI


def _pick_model(entry: ProviderEntry) -> str:
    """Show suggested models numbered; allow a manually typed model id."""
    print("\n  Pilih model:")  # noqa: T201
    for i, m in enumerate(entry.suggested_models, start=1):
        print(f"    {i}) {m}")  # noqa: T201
    print("    (atau ketik nama model lengkap, mis. provider/model)")  # noqa: T201
    while True:
        raw = _prompt("  Model> ")
        if not raw:
            if entry.suggested_models:
                return entry.suggested_models[0]
            print("  Model tidak boleh kosong.")  # noqa: T201
            continue
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(entry.suggested_models):
                return entry.suggested_models[n - 1]
            print("  Nomor di luar jangkauan, coba lagi.")  # noqa: T201
            continue
        return raw  # manual entry


def _collect_api_keys(entry: ProviderEntry, env_path: Path) -> int:
    """Prompt for up to N keys (blank = done). Reject sk-ant-oat*. Returns count."""
    written = 0
    slot = 1
    while slot <= _MAX_API_KEYS:
        key = _prompt(f"  {entry.env_prefix}_{slot} (Enter kosong = selesai)> ")
        if not key:
            break
        if any(key.startswith(p) for p in _FORBIDDEN_KEY_PREFIXES):
            # Never echo the value — only the rejection reason.
            print(  # noqa: T201
                "  ✗ Token OAuth konsumen (sk-ant-oat...) DILARANG — pakai API key resmi."
            )
            continue
        upsert_env_var(env_path, f"{entry.env_prefix}_{slot}", key)
        print(f"  ✓ {entry.env_prefix}_{slot} tersimpan")  # noqa: T201
        written += 1
        slot += 1
    return written


def _do_oauth(entry: ProviderEntry) -> list[str]:
    """Login one or more accounts for an OAuth provider. Returns account labels."""
    accounts: list[str] = []
    while True:
        account = _prompt("  Label akun [default]> ") or "default"
        perform_login(entry.provider_id, account, manual_paste=True)
        accounts.append(account)
        again = _prompt("  Tambah akun lain? [y/N]> ").lower()
        if again not in ("y", "yes"):
            break
    return accounts


def _ask_role() -> str:
    while True:
        role = _prompt("  Role untuk entri ini? [analyst/critic/flagship/skip]> ").lower()
        if role in _ROLES or role == "skip":
            return role
        print("  Pilihan tidak valid.")  # noqa: T201


def wizard(env_path: Path, settings_path: Path) -> int:
    """Interactive multi-provider/model setup. Returns 0 on completion."""
    summary: list[str] = []
    print("=== rtrade setup wizard ===")  # noqa: T201
    print("Pilih provider untuk ditambahkan (bisa banyak). 0 untuk selesai.")  # noqa: T201

    while True:
        print("\nProvider:")  # noqa: T201
        for i, p in enumerate(PROVIDER_CATALOG, start=1):
            print(f"  {i}) {p.menu_label}  [{p.kind}]")  # noqa: T201
        print("  0) Selesai")  # noqa: T201
        choice = _prompt("Pilih> ")
        if choice == "0" or choice == "":
            break
        if not choice.isdigit() or not (1 <= int(choice) <= len(PROVIDER_CATALOG)):
            print("Pilihan tidak valid.")  # noqa: T201
            continue
        entry = PROVIDER_CATALOG[int(choice) - 1]

        # Resilience: one provider failure must not crash the whole wizard.
        try:
            model = _pick_model(entry)
            account = "default"
            if entry.kind == "api_key":
                n = _collect_api_keys(entry, env_path)
                if n == 0:
                    print("  (tidak ada key dimasukkan — lewati)")  # noqa: T201
                    continue
                detail = f"{n} API key"
            else:
                accounts = _do_oauth(entry)
                account = accounts[0] if accounts else "default"
                detail = f"OAuth akun: {', '.join(accounts)}"

            role = _ask_role()
            if role != "skip":
                set_model_route(
                    settings_path=settings_path,
                    role=role,
                    provider_id=_route_provider_id(entry),
                    model=model,
                    auth_mode=auth_mode_for(entry.kind),
                    account=account,
                )
                summary.append(f"{entry.menu_label} → {model} ({detail}) → role {role}")
            else:
                summary.append(f"{entry.menu_label} → {model} ({detail}) → (tanpa role)")
        except SystemExit as exc:  # perform_login may abort one provider
            print(f"  ✗ {entry.menu_label} dilewati: {exc}")  # noqa: T201
            continue
        except Exception as exc:  # resilient per-provider loop
            print(f"  ✗ {entry.menu_label} gagal: {exc}")  # noqa: T201
            continue

    print("\n=== Ringkasan entri ===")  # noqa: T201
    if summary:
        for line in summary:
            print(f"  • {line}")  # noqa: T201
    else:
        print("  (tidak ada entri ditambahkan)")  # noqa: T201
    print("\nVerifikasi: python -m rtrade.cli.setup verify")  # noqa: T201
    return 0


def verify(settings_path: Path) -> int:
    """Load config, build the credential pool, print a table. 0 if non-empty else 1."""
    try:
        cfg = AppConfig.load(config_dir=settings_path.parent)
        pool = build_scan_pool(cfg)
    except ConfigError as exc:
        print(f"POOL KOSONG / ERROR: {exc}")  # noqa: T201
        return 1
    except Exception as exc:  # verify must never crash the CLI
        print(f"POOL KOSONG / ERROR: {exc}")  # noqa: T201
        return 1

    if not pool.entries:
        print("POOL KOSONG: tidak ada kredensial LLM. Jalankan wizard dulu.")  # noqa: T201
        return 1

    print(f"\n{'#':<3} {'cred_id':<28} {'flavor':<10} mode")  # noqa: T201
    print("-" * 60)  # noqa: T201
    for i, e in enumerate(pool.entries, start=1):
        print(f"{i:<3} {e.cred_id:<28} {e.flavor:<10} {e.credential.mode}")  # noqa: T201
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="rtrade-setup", description="Setup wizard kredensial LLM (multi-provider/model)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    wiz = sub.add_parser("wizard", help="Setup interaktif provider × model × role")
    wiz.add_argument("--env-file", default=".env", help="Path file .env (default: .env)")
    wiz.add_argument("--settings", default="config/settings.yaml", help="Path settings.yaml")

    ver = sub.add_parser("verify", help="Cek credential pool")
    ver.add_argument("--settings", default="config/settings.yaml", help="Path settings.yaml")

    args = parser.parse_args()
    if args.cmd == "wizard":
        sys.exit(wizard(Path(args.env_file), Path(args.settings)))
    else:  # verify
        sys.exit(verify(Path(args.settings)))


if __name__ == "__main__":
    main()
