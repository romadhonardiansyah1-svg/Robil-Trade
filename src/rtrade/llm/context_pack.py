"""Context pack builder -- single JSON object as LLM's ONLY knowledge source (PLAN 8.9.3).

Every field has a `source_id` so the Verifier (GR-11) can check citations.
Source ID format: `{category}:{field}:{symbol}:{timeframe}:{bar_ts_iso}`

DILARANG memasukkan berita mentah tanpa timestamp.
P2 belum pakai RAG berita; kalender ekonomi + data terstruktur dulu.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import structlog

from rtrade.core.constants import Timeframe
from rtrade.indicators.engine import IndicatorSnapshot

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ContextPack:
    """Immutable context pack for LLM consumption."""

    pack_id: str
    generated_at: str  # ISO 8601 UTC
    instrument: dict[str, Any]
    candidate: dict[str, Any]
    indicators: dict[str, Any]
    structure: dict[str, Any]
    regime: dict[str, Any]
    calendar_next_72h: list[dict[str, Any]]
    derivatives: dict[str, Any] | None
    recent_summary: dict[str, Any]
    source_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "pack_id": self.pack_id,
            "generated_at": self.generated_at,
            "instrument": self.instrument,
            "candidate": self.candidate,
            "indicators": self.indicators,
            "structure": self.structure,
            "regime": self.regime,
            "calendar_next_72h": self.calendar_next_72h,
            "derivatives": self.derivatives,
            "recent_summary": self.recent_summary,
            "source_ids": self.source_ids,
        }

    def to_prompt_text(self) -> str:
        """Convert to a readable text block for the LLM prompt."""
        import json

        return json.dumps(self.to_dict(), indent=2, default=str)


def _make_source_id(
    category: str,
    field_name: str,
    symbol: str,
    timeframe: str,
    bar_ts: str,
) -> str:
    """Build a source_id string."""
    return f"{category}:{field_name}:{symbol}:{timeframe}:{bar_ts}"


def build_context_pack(
    *,
    symbol: str,
    market: str,
    timeframe: Timeframe,
    session_active: bool,
    action: str,
    entry: float,
    sl: float,
    tp: float,
    rr: float,
    valid_until: str,
    strategy: str,
    confluence_breakdown: dict[str, int],
    snapshot: IndicatorSnapshot,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
    sr_levels: list[dict[str, Any]],
    gap_zones: list[dict[str, Any]],
    regime_state: str,
    regime_since: str,
    calendar_events: list[dict[str, Any]],
    derivatives: dict[str, Any] | None = None,
    df_1h: pd.DataFrame | None = None,
) -> ContextPack:
    """Build a context pack from pipeline data.

    All indicator values come from the IndicatorSnapshot (last closed bar).
    """
    now = datetime.now(UTC)
    bar_ts = snapshot.bar_ts.isoformat() if snapshot.bar_ts else now.isoformat()
    tf_str = str(timeframe)

    source_ids: list[str] = []

    # --- Instrument ---
    instrument = {
        "symbol": symbol,
        "market": market,
        "session_active": session_active,
    }

    # --- Candidate ---
    candidate = {
        "action": action,
        "entry_limit": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "rr": round(rr, 2),
        "valid_until": valid_until,
        "strategy": strategy,
        "confluence_breakdown": confluence_breakdown,
    }

    # --- Indicators (all from snapshot) ---
    ind_fields = {
        "ema21": snapshot.ema21,
        "ema50": snapshot.ema50,
        "ema200": snapshot.ema200,
        "rsi": snapshot.rsi,
        "atr": snapshot.atr,
        "adx": snapshot.adx,
        "plus_di": snapshot.plus_di,
        "minus_di": snapshot.minus_di,
        "macd": snapshot.macd,
        "macd_signal": snapshot.macd_signal,
        "macd_hist": snapshot.macd_hist,
        "bb_upper": snapshot.bb_upper,
        "bb_mid": snapshot.bb_mid,
        "bb_lower": snapshot.bb_lower,
        "vwap": snapshot.vwap,
        "atr_percentile": snapshot.atr_percentile,
    }

    indicators: dict[str, Any] = {"bar_ts": bar_ts}
    for fname, val in ind_fields.items():
        sid = _make_source_id("ind", fname, symbol, tf_str, bar_ts)
        source_ids.append(sid)
        indicators[fname] = {
            "value": round(val, 6) if val is not None else None,
            "source_id": sid,
        }

    # --- Structure ---
    structure: dict[str, Any] = {
        "swing_highs": [],
        "swing_lows": [],
        "sr_levels": [],
        "gap_zones": [],
    }
    for sh in swing_highs[:5]:  # limit to 5 nearest
        sid = _make_source_id("str", "swing_high", symbol, tf_str, bar_ts)
        source_ids.append(sid)
        structure["swing_highs"].append({**sh, "source_id": sid})
    for sl_pt in swing_lows[:5]:
        sid = _make_source_id("str", "swing_low", symbol, tf_str, bar_ts)
        source_ids.append(sid)
        structure["swing_lows"].append({**sl_pt, "source_id": sid})
    for level in sr_levels[:10]:
        sid = _make_source_id("str", "sr_level", symbol, tf_str, bar_ts)
        source_ids.append(sid)
        structure["sr_levels"].append({**level, "source_id": sid})
    for gap in gap_zones[:5]:
        sid = _make_source_id("str", "gap_zone", symbol, tf_str, bar_ts)
        source_ids.append(sid)
        structure["gap_zones"].append({**gap, "source_id": sid})

    # --- Regime ---
    regime = {
        "state": regime_state,
        "since": regime_since,
        "source_id": _make_source_id("reg", "state", symbol, tf_str, bar_ts),
    }
    source_ids.append(regime["source_id"])

    # --- Calendar (next 72h) ---
    calendar_entries: list[dict[str, Any]] = []
    for evt in calendar_events[:20]:  # limit
        sid = _make_source_id(
            "cal",
            evt.get("event", "unknown"),
            evt.get("currency", "UNK"),
            tf_str,
            evt.get("event_time", bar_ts),
        )
        source_ids.append(sid)
        calendar_entries.append({**evt, "source_id": sid})

    # --- Derivatives (crypto only) ---
    deriv_data: dict[str, Any] | None = None
    if derivatives is not None:
        sid_fr = _make_source_id("der", "funding_rate", symbol, tf_str, bar_ts)
        sid_oi = _make_source_id("der", "oi_change", symbol, tf_str, bar_ts)
        source_ids.extend([sid_fr, sid_oi])
        deriv_data = {
            "funding_rate": {
                "value": derivatives.get("funding_rate"),
                "source_id": sid_fr,
            },
            "funding_extreme_flag": derivatives.get("funding_extreme_flag", False),
            "oi_change_24h": {
                "value": derivatives.get("oi_change_24h"),
                "source_id": sid_oi,
            },
        }

    # --- Recent candles summary (NO raw data) ---
    recent: dict[str, Any] = {
        "return_24h": None,
        "return_7d": None,
        "range_position": None,
    }
    if df_1h is not None and len(df_1h) >= 24:
        close_now = float(df_1h.iloc[-1]["close"])
        close_24h = float(df_1h.iloc[-24]["close"])
        recent["return_24h"] = round((close_now - close_24h) / close_24h * 100, 2)
        sid_24 = _make_source_id("sum", "return_24h", symbol, tf_str, bar_ts)
        source_ids.append(sid_24)
        recent["return_24h_source_id"] = sid_24

        if len(df_1h) >= 168:  # 7 days * 24h
            close_7d = float(df_1h.iloc[-168]["close"])
            recent["return_7d"] = round((close_now - close_7d) / close_7d * 100, 2)

        # Range position (0=at low, 100=at high of last 20 bars).
        recent_20 = df_1h.tail(20)
        high_20 = float(recent_20["high"].max())
        low_20 = float(recent_20["low"].min())
        if high_20 != low_20:
            recent["range_position"] = round((close_now - low_20) / (high_20 - low_20) * 100, 1)

    # --- Pack ID ---
    pack_id = hashlib.sha256(f"{symbol}:{tf_str}:{bar_ts}:{now.isoformat()}".encode()).hexdigest()[
        :16
    ]

    return ContextPack(
        pack_id=f"pack_{pack_id}",
        generated_at=now.isoformat(),
        instrument=instrument,
        candidate=candidate,
        indicators=indicators,
        structure=structure,
        regime=regime,
        calendar_next_72h=calendar_entries,
        derivatives=deriv_data,
        recent_summary=recent,
        source_ids=source_ids,
    )
