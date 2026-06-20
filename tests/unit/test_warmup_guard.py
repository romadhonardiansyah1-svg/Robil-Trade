"""P1-7 (G-07): cold-start warmup guarantee.

The scan pipeline must abstain ("abstain_warmup") until it holds a full warmup
window of bars, instead of emitting signals from under-warmed indicators/regime.

The warmup decision is isolated in the pure helper ``_warmup_deficit`` so it can
be exercised deterministically without a live DB/provider (QA-INT-04 covers the
end-to-end path as an integration test). These tests pin the decision logic.
"""

from __future__ import annotations

from rtrade.core.config import AppConfig
from rtrade.pipeline.scan import _warmup_deficit

_CONFIG_DIR = __import__("pathlib").Path(__file__).resolve().parents[2] / "config"


def test_under_warmup_1h_returns_deficit() -> None:
    deficit = _warmup_deficit(bars_1h=100, bars_4h=0, has_4h=False, warmup_bars=500)
    assert deficit == {"bars_1h": 100, "required": 500}


def test_1h_just_below_threshold_is_deficit() -> None:
    assert _warmup_deficit(bars_1h=499, bars_4h=499, has_4h=True, warmup_bars=500) == {
        "bars_1h": 499,
        "required": 500,
    }


def test_1h_exact_threshold_is_sufficient_when_no_4h() -> None:
    assert _warmup_deficit(bars_1h=500, bars_4h=0, has_4h=False, warmup_bars=500) is None


def test_1h_ok_but_4h_under_returns_4h_deficit() -> None:
    assert _warmup_deficit(bars_1h=500, bars_4h=100, has_4h=True, warmup_bars=500) == {
        "bars_4h": 100,
        "required": 500,
    }


def test_4h_deficit_ignored_when_instrument_has_no_4h() -> None:
    assert _warmup_deficit(bars_1h=500, bars_4h=0, has_4h=False, warmup_bars=500) is None


def test_all_sufficient_returns_none() -> None:
    assert _warmup_deficit(bars_1h=800, bars_4h=600, has_4h=True, warmup_bars=500) is None


def test_1h_checked_before_4h() -> None:
    # When both are short, the 1h deficit is reported (checked first).
    assert _warmup_deficit(bars_1h=10, bars_4h=10, has_4h=True, warmup_bars=500) == {
        "bars_1h": 10,
        "required": 500,
    }


def test_signal_settings_warmup_bars_default_is_full_window() -> None:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    # Default warmup is the full window (500), stricter than the legacy 200 floor.
    assert cfg.settings.signal.warmup_bars >= 500
