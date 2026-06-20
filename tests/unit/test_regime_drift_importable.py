"""River-free invariant: ``rtrade.regime.drift`` must import without river.

This test does NOT require the optional ``river`` dependency and always runs. It
guards the lazy-import contract: importing the module (and calling
``is_available``) must never require river to be installed.
"""

from __future__ import annotations

import importlib


def test_module_importable_without_river() -> None:
    mod = importlib.import_module("rtrade.regime.drift")
    assert hasattr(mod, "RegimeDriftDetector")
    assert hasattr(mod, "is_available")
    # is_available must be callable and return a bool regardless of river presence.
    assert isinstance(mod.is_available(), bool)
