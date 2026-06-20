from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from rtrade.core.config import AppConfig, GateProfile, SignalSettings

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _signal(**over: object) -> SignalSettings:
    base: dict[str, object] = {
        "confluence_min_score": 60,
        "confidence_min": 0.55,
        "max_signals_per_day_per_instrument": 3,
        "price_drift_max_pct": 0.5,
        "candle_staleness_factor": 2,
        "edge_quality": {
            "enabled": True,
            "min_score": 65,
            "max_spread_atr": 0.12,
            "min_atr_percentile": 8,
            "max_atr_percentile": 96,
            "max_opposing_wick_ratio": 0.62,
            "max_total_wick_body_ratio": 6,
            "min_body_atr": 0.03,
            "min_volume_ratio": 0.55,
            "volume_window": 20,
            "max_range_expansion_atr": 2.8,
            "max_entry_distance_atr": 1.25,
        },
    }
    base.update(over)
    return SignalSettings.model_validate(base)


def test_default_profile_synthesized_from_globals_when_absent() -> None:
    s = _signal()
    prof = s.profile("default")
    assert prof.confluence_min_score == 60
    assert prof.edge_quality_min_score == 65
    assert prof.confidence_min == pytest.approx(0.55)
    assert prof.max_signals_per_day_per_instrument == 3


def test_unknown_profile_falls_back_to_default() -> None:
    s = _signal()
    assert s.profile("does_not_exist") == s.profile("default")


def test_explicit_profiles_are_preserved() -> None:
    s = _signal(
        profiles={
            "scalping": {
                "confluence_min_score": 50,
                "edge_quality_min_score": 55,
                "confidence_min": 0.50,
                "max_signals_per_day_per_instrument": 10,
            }
        }
    )
    scal = s.profile("scalping")
    assert scal.confluence_min_score == 50
    assert scal.max_signals_per_day_per_instrument == 10
    # default still synthesized from globals.
    assert s.profile("default").confluence_min_score == 60


def test_gate_profile_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError):
        GateProfile(
            confluence_min_score=120,  # > 100
            edge_quality_min_score=55,
            confidence_min=0.5,
            max_signals_per_day_per_instrument=10,
        )


def test_shipped_config_has_default_and_scalping_profiles() -> None:
    cfg = AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)
    sig = cfg.settings.signal
    assert sig.profile("default").confluence_min_score == sig.confluence_min_score
    scal = sig.profile("scalping")
    assert scal.confluence_min_score < sig.profile("default").confluence_min_score
    assert scal.max_signals_per_day_per_instrument >= sig.max_signals_per_day_per_instrument
