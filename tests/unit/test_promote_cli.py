from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import rtrade.cli.promote as promote_mod
from rtrade.persistence.models import BacktestRun


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


def _run(all_passed: bool | None) -> BacktestRun | None:
    if all_passed is None:
        return None
    return BacktestRun(
        strategy="s3_mtf_scalper",
        instrument="XAUUSD",
        is_oos=True,
        metrics={},
        gates={"all_passed": all_passed},
        params={},
    )


def _wire(monkeypatch: pytest.MonkeyPatch, *, run: BacktestRun | None) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(promote_mod, "_get_engine", lambda _url: object())
    monkeypatch.setattr(
        promote_mod, "create_session_factory", lambda _e: _FakeSessionFactory(session)
    )

    class _State:
        def __init__(self, _s: Any) -> None:
            pass

        async def set_state(self, *_a: Any, **_k: Any) -> None:
            return None

    class _Runs:
        def __init__(self, _s: Any) -> None:
            pass

        async def latest_for(
            self, _strategy: str, _instrument: str | None = None
        ) -> BacktestRun | None:
            return run

    monkeypatch.setattr(promote_mod, "StrategyStateRepo", _State)
    monkeypatch.setattr(promote_mod, "BacktestRunRepo", _Runs)
    monkeypatch.setattr(
        promote_mod.AppConfig,
        "load",
        classmethod(lambda _cls: SimpleNamespace(secrets=SimpleNamespace(database_url="x"))),
    )
    return session


def test_unknown_strategy_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "not_real", "--symbol", "XAUUSD"])
    assert exc.value.code == 2


def test_passing_run_promotes_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _wire(monkeypatch, run=_run(True))
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 0
    assert session.committed is True


def test_failing_run_refuses_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=_run(False))
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 1


def test_no_run_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 2


def test_shadow_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--shadow"])
    assert exc.value.code == 0
    assert session.committed is True
