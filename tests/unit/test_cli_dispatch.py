"""Unified `rtrade` CLI dispatch (PLAN P3-2).

Deterministic, no network, no DB: we assert the usage/exit behaviour for
missing/unknown subcommands and that a known subcommand dispatches to the
right module entry (subcommand main is replaced with a spy).
"""

from __future__ import annotations

import pytest

from rtrade.cli.__main__ import main


def test_missing_subcommand_prints_usage_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage: rtrade" in err
    assert "backfill" in err


def test_unknown_subcommand_prints_usage_and_exits(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["nope"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unknown command 'nope'" in err
    assert "usage: rtrade" in err


def test_known_subcommand_dispatches_to_module_entry(monkeypatch) -> None:
    import sys

    import rtrade.cli.backfill as backfill_mod

    seen: dict[str, list[str]] = {}

    def _spy() -> None:
        # Capture the argv the subcommand's own argparse would see.
        seen["argv"] = list(sys.argv)

    monkeypatch.setattr(backfill_mod, "main", _spy)
    monkeypatch.setattr(sys, "argv", ["rtrade", "backfill", "XAUUSD", "1h"])

    main()

    assert "argv" in seen  # spy was invoked → dispatch reached backfill
    assert seen["argv"][0] == "rtrade backfill"
    assert seen["argv"][1:] == ["XAUUSD", "1h"]
