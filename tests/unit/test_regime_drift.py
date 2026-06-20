"""Tests for the River/ADWIN regime drift detector (PLAN P3-5, SHADOW ONLY).

These tests require the optional ``river`` dependency. ``pytest.importorskip`` at
the top SKIPS the whole module cleanly when river is not installed (river is the
optional 'drift' extra and is not installed in the base environment).

The river-free *module-importable* invariant lives in
``test_regime_drift_importable.py`` so it always runs regardless of river.
"""

from __future__ import annotations

import pytest

pytest.importorskip("river")

from rtrade.regime.drift import RegimeDriftDetector


def test_detects_clear_distribution_shift() -> None:
    detector = RegimeDriftDetector()
    # Stationary regime, then an abrupt shift to a very different level.
    drift_flagged = False
    for _ in range(1000):
        if detector.update(0.0):
            drift_flagged = True
    for _ in range(1000):
        if detector.update(10.0):
            drift_flagged = True
    assert drift_flagged is True
    assert detector.state().total_drifts >= 1


def test_no_drift_on_stationary_stream() -> None:
    detector = RegimeDriftDetector()
    flagged = any(detector.update(1.0) for _ in range(2000))
    assert flagged is False
    assert detector.state().total_drifts == 0
