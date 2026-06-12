"""T24: MAE/MFE excursion capture.

Computes Maximum Adverse Excursion and Maximum Favorable Excursion
in R-multiples from a list of candle bars after fill.
"""

from __future__ import annotations

from rtrade.papertrack.tracker import CandleBar


def compute_excursion(
    action: str,
    entry: float,
    stop_loss: float,
    candles: list[CandleBar],
) -> tuple[float | None, float | None]:
    """Compute MAE and MFE in R-multiples.

    Args:
        action: "BUY" or "SELL".
        entry: Fill price.
        stop_loss: Original stop loss.
        candles: Candle bars after fill.

    Returns:
        (mae_r, mfe_r) — both in R-multiples.
        None if no candles or zero risk.
    """
    sl_dist = abs(entry - stop_loss)
    if sl_dist == 0 or not candles:
        return None, None

    mae_r = 0.0  # worst (most negative R)
    mfe_r = 0.0  # best (most positive R)

    for bar in candles:
        if action == "BUY":
            adverse_r = (bar.low - entry) / sl_dist
            favorable_r = (bar.high - entry) / sl_dist
        else:
            adverse_r = (entry - bar.high) / sl_dist
            favorable_r = (entry - bar.low) / sl_dist

        if adverse_r < mae_r:
            mae_r = adverse_r
        if favorable_r > mfe_r:
            mfe_r = favorable_r

    return round(mae_r, 4), round(mfe_r, 4)
