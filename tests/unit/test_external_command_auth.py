"""Tests for external command auth adapter (O14)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rtrade.llm.auth.token_store import StoredToken, load_token, save_token


@pytest.fixture()
def _token_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RTRADE_TOKEN_DIR", str(tmp_path))
    monkeypatch.delenv("RTRADE_TOKEN_KEY", raising=False)


def _run_external_command(argv: list[str]) -> StoredToken:
    """Simulate running an external auth command. shell=False, capture stdout JSON."""
    import subprocess

    result = subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"External auth command failed with exit code {result.returncode}. "
            "Token tidak tercetak."
        )
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            "External auth command returned invalid JSON. Token tidak tercetak."
        ) from None
    return StoredToken(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expiry_epoch=body.get("expires_in", 3600) + __import__("time").time(),
        scopes=[],
    )


class TestExternalCommandAuth:
    @pytest.mark.usefixtures("_token_env")
    def test_valid_json_stdout(self, tmp_path: Path) -> None:
        # Create a script that outputs valid JSON
        script = tmp_path / "auth_adapter.py"
        script.write_text(
            'import json; print(json.dumps({"access_token": "ext_tok", "expires_in": 3600}))',
            encoding="utf-8",
        )
        tok = _run_external_command([sys.executable, str(script)])
        assert tok.access_token == "ext_tok"

        # Save and verify it can be loaded
        save_token("external_test", tok)
        loaded = load_token("external_test")
        assert loaded is not None
        assert loaded.access_token == "ext_tok"

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        script = tmp_path / "bad_adapter.py"
        script.write_text('print("not json")', encoding="utf-8")
        with pytest.raises(RuntimeError, match="invalid JSON"):
            _run_external_command([sys.executable, str(script)])

    def test_nonzero_exit_raises(self, tmp_path: Path) -> None:
        script = tmp_path / "fail_adapter.py"
        script.write_text("import sys; sys.exit(1)", encoding="utf-8")
        with pytest.raises(RuntimeError, match="exit code"):
            _run_external_command([sys.executable, str(script)])

    def test_missing_command_raises(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            _run_external_command(["/nonexistent/auth_adapter"])
