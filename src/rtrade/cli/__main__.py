"""Unified ``rtrade`` CLI dispatch entrypoint (PLAN P3-2).

Single console-script that dispatches to the existing subcommand modules
based on the first positional argument (``argv[1]``). The remaining args are
forwarded to the chosen subcommand's own argparse entry by rewriting
``sys.argv`` so each subcommand sees a sensible ``prog`` plus its own args.

Valid subcommands: ``auth``, ``backfill``, ``bot``, ``backtest``, ``promote``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import sys

_USAGE = """\
usage: rtrade <command> [args...]

commands:
  auth      OAuth auth management (login, status, providers, ...)
  backfill  Backfill candle data for an instrument x timeframe
  bot       Run the Telegram delivery bot (polling mode)
  backtest  Run the go-live statistical gate runner (walk-forward + gates)
  promote   Flip a strategy to live after its backtest passes (shadow→live gate)

Run `rtrade <command> --help` for command-specific options.
"""


def _run_auth() -> None:
    # `auth.main()` is synchronous and parses sys.argv via argparse.
    from rtrade.cli.auth import main as auth_main

    auth_main()


def _run_backfill() -> None:
    # `backfill.main()` is synchronous and parses sys.argv via argparse.
    from rtrade.cli.backfill import main as backfill_main

    backfill_main()


def _run_backtest() -> None:
    # `backtest.main()` parses sys.argv via argparse and raises SystemExit
    # with the go-live gate code (0 pass / non-zero fail).
    from rtrade.cli.backtest import main as backtest_main

    backtest_main()


def _run_promote() -> None:
    # `promote.main()` parses sys.argv via argparse and raises SystemExit with
    # the go-live promotion code (0 enabled / 1 gate-fail / 2 no-run-or-unknown).
    from rtrade.cli.promote import main as promote_main

    promote_main()


def _run_bot() -> None:
    # `bot.main()` is a coroutine — run it on a fresh event loop.
    from rtrade.cli.bot import main as bot_main

    asyncio.run(bot_main())


_COMMANDS: dict[str, Callable[[], None]] = {
    "auth": _run_auth,
    "backfill": _run_backfill,
    "backtest": _run_backtest,
    "promote": _run_promote,
    "bot": _run_bot,
}


def main(argv: list[str] | None = None) -> None:
    """Dispatch to a subcommand based on the first positional argument.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Raises:
        SystemExit: with code 2 when the subcommand is missing or unknown.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(_USAGE, file=sys.stderr)  # noqa: T201
        raise SystemExit(2)

    command = args[0]
    handler = _COMMANDS.get(command)
    if handler is None:
        print(f"rtrade: unknown command '{command}'\n", file=sys.stderr)  # noqa: T201
        print(_USAGE, file=sys.stderr)  # noqa: T201
        raise SystemExit(2)

    # Rewrite argv so the subcommand's own argparse sees `prog` + its args.
    sys.argv = [f"rtrade {command}", *args[1:]]
    handler()


if __name__ == "__main__":
    main()
