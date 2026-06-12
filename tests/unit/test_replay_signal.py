"""T9: Full candle replay tests for papertrack."""

from datetime import UTC, datetime, timedelta

import pytest

from rtrade.core.constants import SignalStatus
from rtrade.papertrack.tracker import CandleBar, replay_signal

# All tests: BUY, entry=100, SL=98, TP=104, valid_until=ts of candle 3.
_ENTRY = 100.0
_SL = 98.0
_TP = 104.0

_T0 = datetime(2026, 6, 11, 8, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(hours=1)
_T2 = _T0 + timedelta(hours=2)
_T3 = _T0 + timedelta(hours=3)
_T4 = _T0 + timedelta(hours=4)
_VALID = _T3  # valid_until = ts of candle 3


class TestReplaySignal:
    def test_fill_then_tp(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=99.0, low=97.0),  # no fill (99 < 100)
            CandleBar(ts=_T2, high=101.0, low=99.5),  # fill (99.5 <= 100 <= 101)
            CandleBar(ts=_T3, high=102.0, low=100.5),  # no TP
            CandleBar(ts=_T4, high=104.5, low=101.0),  # TP hit
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=False,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.TP_HIT
        assert result.outcome_r == pytest.approx(2.0)
        assert result.resolved_at == _T4

    def test_fill_then_sl(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=99.0, low=97.0),  # no fill
            CandleBar(ts=_T2, high=101.0, low=99.5),  # fill
            CandleBar(ts=_T3, high=100.0, low=97.9),  # SL hit
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=False,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.SL_HIT
        assert result.outcome_r == -1.0

    def test_expired(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=99.0, low=97.0),  # no touch
            CandleBar(ts=_T2, high=99.5, low=97.5),  # no touch
            CandleBar(ts=_T3, high=99.8, low=97.3),  # no touch
            CandleBar(ts=_T4, high=99.0, low=96.0),  # ts > valid_until → expired
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=False,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.EXPIRED

    def test_fill_bar_also_hits_sl_worst_case(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=99.0, low=96.0),  # no fill
            CandleBar(ts=_T2, high=101.0, low=97.5),  # fill AND SL in same bar
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=False,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.SL_HIT

    def test_both_hit_after_fill_sl_first(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=99.0, low=97.0),
            CandleBar(ts=_T2, high=101.0, low=99.5),  # fill
            CandleBar(ts=_T3, high=104.5, low=97.9),  # SL=98 and TP=104 both hit → SL
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=False,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.SL_HIT

    def test_already_filled_continues(self) -> None:
        candles = [
            CandleBar(ts=_T1, high=104.2, low=100.5),  # TP hit
        ]
        result = replay_signal(
            "s1",
            "BUY",
            _ENTRY,
            _SL,
            _TP,
            _VALID,
            already_filled=True,
            candles=candles,
        )
        assert result is not None
        assert result.new_status == SignalStatus.TP_HIT
