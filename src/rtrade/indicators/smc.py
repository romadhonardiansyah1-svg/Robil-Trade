"""Smart-Money-Concepts / ICT detectors — pure functions, no I/O (PLAN SP-3 §8).

Detects the structures the S4 SMC scalper trades on, over an OHLC(V) DataFrame
with the same conventions as indicators/structure.py (datetime index of bar-open
timestamps; lowercase open/high/low/close columns). Integer bar positions are
returned as *_idx fields.

Detectors (pinned, deterministic definitions):
- fair_value_gaps:  3-bar imbalance (bullish low[i] > high[i-2]; bearish high[i] < low[i-2]).
- market_structure: BOS (continuation break of the latest swing in trend direction)
                    and CHoCH (first counter-trend break, flips the trend).
- order_blocks:     last opposing candle before a structure break.
- liquidity_sweeps: wick beyond a prior swing high/low that closes back inside.

Concepts adapted (ported, not imported) from the MIT-licensed projects
`joshyattridge/smart-money-concepts` and
`LesterCS/Decoding-Institutional-Order-Flow-in-Python-like-ICT`.
The detectors are re-implemented here for strict typing, determinism, and test
control. Original works are MIT licensed; attribution retained per their terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from rtrade.indicators.structure import SwingPoint, detect_swing_points


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """A 3-bar imbalance zone (top > bottom). start/end are bar positions."""

    start_idx: int
    end_idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class OrderBlock:
    """Last opposing candle before a structure break; zone = [bottom, top]."""

    idx: int
    top: float
    bottom: float
    direction: Literal["bullish", "bearish"]


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A wick beyond a prior swing level that closes back inside it."""

    idx: int
    level: float
    side: Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class StructureEvent:
    """A break of structure (BOS) or change of character (CHoCH)."""

    idx: int
    kind: Literal["BOS", "CHoCH"]
    direction: Literal["bullish", "bearish"]


def fair_value_gaps(df: pd.DataFrame) -> list[FairValueGap]:
    """Detect 3-bar fair value gaps (imbalances).

    Bullish: low[i] > high[i-2] (price jumped up, leaving an unfilled gap).
    Bearish: high[i] < low[i-2] (price dropped, leaving an unfilled gap).
    The zone spans bars [i-2 .. i]; `top` is the higher edge, `bottom` the lower.
    """
    if len(df) < 3:
        return []

    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values

    gaps: list[FairValueGap] = []
    for i in range(2, len(df)):
        if lows[i] > highs[i - 2]:
            gaps.append(
                FairValueGap(
                    start_idx=i - 2,
                    end_idx=i,
                    top=float(lows[i]),
                    bottom=float(highs[i - 2]),
                    direction="bullish",
                )
            )
        elif highs[i] < lows[i - 2]:
            gaps.append(
                FairValueGap(
                    start_idx=i - 2,
                    end_idx=i,
                    top=float(lows[i - 2]),
                    bottom=float(highs[i]),
                    direction="bearish",
                )
            )

    return gaps


def _swings_by_confirmation(df: pd.DataFrame, swing_lookback: int) -> dict[int, list[SwingPoint]]:
    """Group fractal swings by the bar at which they become confirmed.

    A swing at bar `j` is only known after `swing_lookback` further bars, i.e. it
    becomes actionable at bar `j + swing_lookback` (no look-ahead).
    """
    confirmations: dict[int, list[SwingPoint]] = {}
    for sp in detect_swing_points(df, left=swing_lookback, right=swing_lookback):
        confirmations.setdefault(sp.index + swing_lookback, []).append(sp)
    return confirmations


def market_structure(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[StructureEvent]:
    """Detect BOS (continuation) and CHoCH (first counter-trend) structure breaks.

    Tracks the latest confirmed swing high/low. A close above the active swing
    high is a bullish break; a close below the active swing low is bearish. A
    break aligned with the current trend (or the first break, when no trend is
    set yet) is a BOS; the first break against the trend is a CHoCH and flips it.
    """
    closes = df["close"].astype(float).values
    confirmations = _swings_by_confirmation(df, swing_lookback)

    events: list[StructureEvent] = []
    trend: Literal["bullish", "bearish"] | None = None
    ref_high: float | None = None
    ref_low: float | None = None

    for i in range(len(df)):
        for sp in confirmations.get(i, []):
            if sp.is_high:
                ref_high = sp.price
            else:
                ref_low = sp.price

        close = float(closes[i])
        if ref_high is not None and close > ref_high:
            if trend in (None, "bullish"):
                events.append(StructureEvent(idx=i, kind="BOS", direction="bullish"))
            else:
                events.append(StructureEvent(idx=i, kind="CHoCH", direction="bullish"))
            trend = "bullish"
            ref_high = None
        elif ref_low is not None and close < ref_low:
            if trend in (None, "bearish"):
                events.append(StructureEvent(idx=i, kind="BOS", direction="bearish"))
            else:
                events.append(StructureEvent(idx=i, kind="CHoCH", direction="bearish"))
            trend = "bearish"
            ref_low = None

    return events


def order_blocks(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[OrderBlock]:
    """Detect order blocks: the last opposing candle before each structure break.

    Bullish break -> most recent down candle (close < open) before the break bar.
    Bearish break -> most recent up candle (close > open) before the break bar.
    The block's price zone is that candle's [low, high].
    """
    opens = df["open"].astype(float).values
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    closes = df["close"].astype(float).values

    blocks: list[OrderBlock] = []
    for event in market_structure(df, swing_lookback=swing_lookback):
        j = event.idx - 1
        if event.direction == "bullish":
            while j >= 0 and not closes[j] < opens[j]:
                j -= 1
            if j >= 0:
                blocks.append(
                    OrderBlock(
                        idx=j,
                        top=float(highs[j]),
                        bottom=float(lows[j]),
                        direction="bullish",
                    )
                )
        else:
            while j >= 0 and not closes[j] > opens[j]:
                j -= 1
            if j >= 0:
                blocks.append(
                    OrderBlock(
                        idx=j,
                        top=float(highs[j]),
                        bottom=float(lows[j]),
                        direction="bearish",
                    )
                )

    return blocks


def liquidity_sweeps(df: pd.DataFrame, *, swing_lookback: int = 5) -> list[LiquiditySweep]:
    """Detect liquidity sweeps: a wick beyond a prior swing that closes back inside.

    High-side: high[i] > swing_high and close[i] < swing_high (stop-run above, then
    rejection). Low-side: low[i] < swing_low and close[i] > swing_low. The swept
    level is consumed so it does not re-trigger.
    """
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    closes = df["close"].astype(float).values
    confirmations = _swings_by_confirmation(df, swing_lookback)

    sweeps: list[LiquiditySweep] = []
    ref_high: float | None = None
    ref_low: float | None = None

    for i in range(len(df)):
        for sp in confirmations.get(i, []):
            if sp.is_high:
                ref_high = sp.price
            else:
                ref_low = sp.price

        high = float(highs[i])
        low = float(lows[i])
        close = float(closes[i])
        if ref_high is not None and high > ref_high and close < ref_high:
            sweeps.append(LiquiditySweep(idx=i, level=ref_high, side="high"))
            ref_high = None
        elif ref_low is not None and low < ref_low and close > ref_low:
            sweeps.append(LiquiditySweep(idx=i, level=ref_low, side="low"))
            ref_low = None

    return sweeps
