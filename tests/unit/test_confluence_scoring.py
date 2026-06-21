"""Unit tests for confluence scoring defects F4 (volume) and F5 (structure).

F4: score_volume must compute the baseline SMA over the prior N bars EXCLUDING
    the current/trigger bar, matching edge_quality._volume_ratio's convention.
F5: score_structure must pick the level with the MINIMUM absolute distance to the
    entry (true nearest), not the first level by ascending price within tolerance.
"""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.indicators.structure import SRLevel
from rtrade.signals.confluence import score_structure, score_volume
from rtrade.signals.edge_quality import _volume_ratio


class TestScoreVolumeExcludesCurrentBar:
    def test_baseline_excludes_trigger_bar(self) -> None:
        # 24 prior bars at volume 100, then a trigger-bar spike at 150.
        # Excluding the current bar: baseline = mean(prior 20) = 100,
        #   ratio = 150 / 100 = 1.5 -> full max_score (15).
        # Including the current bar (the old bug): rolling(20).mean() at the
        #   last position = (19*100 + 150)/20 = 102.5, ratio = 1.463 -> 10.
        volumes = [100.0] * 24 + [150.0]
        df = pd.DataFrame({"volume": volumes})

        assert score_volume(df, max_score=15) == 15

    def test_consistent_with_edge_quality_exclusion(self) -> None:
        # With constant prior bars, mean == median, so the ratio computed by
        # score_volume's baseline must equal edge_quality._volume_ratio (both
        # exclude the current bar): 150 / 100 = 1.5.
        volumes = [100.0] * 24 + [150.0]
        df = pd.DataFrame({"volume": volumes})

        ratio = _volume_ratio(df, 20)
        assert ratio == 1.5


class TestScoreStructureNearestByDistance:
    def test_picks_nearest_level_not_first_by_price(self) -> None:
        # entry=100, atr=10 -> tolerance=5. Two supports within tolerance:
        #   far  : price 96 (distance 4, strength 1)  -- first by ascending price
        #   near : price 98 (distance 2, strength 3)  -- true nearest
        # Old (first-by-price) picks strength 1 -> round(1/3*20)=7.
        # New (nearest-by-distance) picks strength 3 -> round(3/3*20)=20.
        sr_levels = [
            SRLevel(price=96.0, strength=1, is_resistance=False, touches=[]),
            SRLevel(price=98.0, strength=3, is_resistance=False, touches=[]),
        ]

        score = score_structure(
            entry=100.0,
            sr_levels=sr_levels,
            gap_zones=[],
            action=Action.BUY,
            atr=10.0,
            max_score=20,
        )

        assert score == 20
