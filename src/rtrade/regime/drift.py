"""River/ADWIN regime concept-drift detector — SHADOW ONLY (PLAN P3-5).

Wraps ``river.drift.ADWIN`` to watch a regime/feature stream for concept drift
(distribution shift). This is **shadow only**: it never gates signals, never
auto-promotes anything, and nothing in the running scan path constructs it by
default. Per ADR-A08 it stays in shadow until proven via the P1 backtest gate.

Dependency handling
-------------------
``river`` is an OPTIONAL dependency (the ``drift`` group in ``pyproject.toml``;
install with ``uv sync --group drift``). It is imported LAZILY inside the detector
constructor — NOT at module top-level — so ``import rtrade.regime.drift`` always
succeeds even when river is not installed. Constructing :class:`RegimeDriftDetector`
without river raises a clear, actionable :class:`ImportError`. Use
:func:`is_available` to check availability without triggering the error.

river is BSD-3-Clause licensed (GI-4 compatible).

Wire-in: intentionally NOT wired into ``scan.py`` / regime selection. Shadow only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    # Type-only import: evaluated by mypy, never at runtime. Keeps the module
    # importable (and mypy --strict clean) without river installed.
    from river.drift import ADWIN

logger = structlog.get_logger(__name__)


def is_available() -> bool:
    """Return True when the optional ``river`` dependency is importable."""
    from importlib.util import find_spec

    return find_spec("river") is not None


@dataclass(frozen=True, slots=True)
class DriftState:
    """Immutable snapshot of the detector's state after an update.

    Attributes:
        n_updates: Total number of values fed to the detector.
        drift_detected: True if the most recent ``update`` flagged drift.
        total_drifts: Cumulative count of drift events observed.
    """

    n_updates: int
    drift_detected: bool
    total_drifts: int


class RegimeDriftDetector:
    """ADWIN-based concept-drift detector over a scalar regime/feature stream.

    SHADOW ONLY (ADR-A08): observes drift for analysis/telemetry; it must never be
    used to gate or alter live signals until proven via the P1 backtest gate.

    Args:
        delta: ADWIN confidence parameter (smaller = fewer false positives).

    Raises:
        ImportError: if the optional ``river`` dependency is not installed.
    """

    def __init__(self, *, delta: float = 0.002) -> None:
        try:
            from river.drift import ADWIN
        except ImportError as exc:  # pragma: no cover - exercised only without river
            raise ImportError(
                "RegimeDriftDetector requires the optional 'river' dependency. "
                "Install the 'drift' extra: uv sync --group drift"
            ) from exc

        self._adwin: ADWIN = ADWIN(delta=delta)
        self._n_updates = 0
        self._total_drifts = 0
        self._last_drift = False

    def update(self, value: float) -> bool:
        """Feed one observation; return True when drift is detected on this step."""
        adwin: Any = self._adwin
        adwin.update(value)
        self._n_updates += 1
        detected = bool(adwin.drift_detected)
        self._last_drift = detected
        if detected:
            self._total_drifts += 1
            logger.info(
                "regime drift detected (shadow)",
                n_updates=self._n_updates,
                total_drifts=self._total_drifts,
            )
        return detected

    @property
    def drift_detected(self) -> bool:
        """True if the most recent :meth:`update` flagged drift."""
        return self._last_drift

    def state(self) -> DriftState:
        """Return an immutable snapshot of the current detector state."""
        return DriftState(
            n_updates=self._n_updates,
            drift_detected=self._last_drift,
            total_drifts=self._total_drifts,
        )
