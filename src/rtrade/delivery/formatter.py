"""Signal formatter — renders signals into Telegram messages (PLAN §8.10).

Output language: Bahasa Indonesia (per PLAN §0.12).
Disclaimer MANDATORY on every signal (PLAN §14.3).
In P1 (no LLM): rationale = deterministic summary from confluence breakdown.
"""

from __future__ import annotations

from rtrade.signals.schemas import DISCLAIMER_TEXT, SignalCandidate, TradingSignal


def _action_emoji(action: str) -> str:
    return "🟢" if action == "BUY" else "🔴"


def _format_price(price: float, decimals: int = 2) -> str:
    """Format price with thousand separators."""
    return f"{price:,.{decimals}f}"


def _guess_decimals(pip_size: float) -> int:
    """Determine decimal places from pip size."""
    if pip_size >= 1:
        return 0
    elif pip_size >= 0.01:
        return 2
    elif pip_size >= 0.001:
        return 3
    elif pip_size >= 0.0001:
        return 4
    return 5


def format_signal_telegram(
    signal: TradingSignal,
    *,
    pip_size: float = 0.01,
    equity: float = 10_000.0,
) -> str:
    """Format a TradingSignal for Telegram delivery.

    Returns a complete message string with emoji, levels, sizing,
    confidence, and mandatory disclaimer.
    """
    c = signal.candidate
    e = c.levels.entry_limit
    sl = c.levels.stop_loss
    tp = c.levels.take_profit
    atr = c.levels.atr_at_signal

    dec = _guess_decimals(pip_size)
    emoji = _action_emoji(c.action.value)

    sl_dist = abs(e - sl)
    atr_mult = sl_dist / atr if atr > 0 else 0
    rr = abs(tp - e) / sl_dist if sl_dist > 0 else 0

    risk_amount = equity * (c.risk_pct / 100)

    lines = [
        f"{emoji} SINYAL {c.action.value} — {c.symbol} ({c.timeframe.value.upper()}) · {c.strategy.replace('_', ' ').title()}",
        f"Entry (LIMIT): {_format_price(e, dec)}",
        f"Stop Loss   : {_format_price(sl, dec)}  (−{_format_price(sl_dist, dec)} / {atr_mult:.1f}×ATR)",
        f"Take Profit : {_format_price(tp, dec)}  (R:R 1:{rr:.1f})",
        f"Berlaku s/d : {c.valid_until.strftime('%Y-%m-%d %H:%M')} UTC",
        f"Ukuran saran: {c.position_size:.4f} (risiko {c.risk_pct:.0f}% dari equity ${equity:,.0f} = ${risk_amount:,.0f})",
        f"Confidence  : {signal.confidence:.2f}  ·  Confluence {c.confluence_score}/100",
        "",
        f"Alasan: {signal.rationale}",
    ]

    if signal.key_risks:
        lines.append(f"Risiko utama: {'; '.join(signal.key_risks)}")

    lines.append("")
    lines.append(DISCLAIMER_TEXT)

    return "\n".join(lines)


def format_candidate_deterministic(
    candidate: SignalCandidate,
    *,
    pip_size: float = 0.01,
    equity: float = 10_000.0,
) -> str:
    """Format a candidate for Telegram in P1 (no LLM — deterministic rationale).

    Rationale is a summary of the confluence breakdown.
    """
    b = candidate.confluence_breakdown
    rationale_parts = []
    if b.trend > 15:
        rationale_parts.append("trend alignment kuat")
    elif b.trend > 5:
        rationale_parts.append("trend cukup searah")
    if b.momentum > 10:
        rationale_parts.append("momentum mendukung")
    if b.structure > 10:
        rationale_parts.append("entry dekat level struktur")
    if b.volume > 7:
        rationale_parts.append("volume konfirmasi")
    if b.macro > 15:
        rationale_parts.append("kondisi makro bersih")
    elif b.macro > 5:
        rationale_parts.append("tidak ada event high-impact dekat")

    rationale = "; ".join(rationale_parts) if rationale_parts else "Setup deterministik terpenuhi"

    key_risks = []
    if b.trend < 15:
        key_risks.append("Trend alignment tidak optimal")
    if b.macro < 10:
        key_risks.append("Event makro mendekati — perhatikan kalender")
    if b.volume == 0:
        key_risks.append("Data volume tidak tersedia — konfirmasi manual disarankan")
    if not key_risks:
        key_risks.append("Pastikan manajemen risiko sesuai rencana trading")

    return format_signal_telegram(
        TradingSignal(
            signal_id=candidate.candidate_id,
            candidate=candidate,
            confidence=candidate.confluence_score / 100,
            rationale=rationale,
            key_risks=key_risks,
            sources=["deterministic_pipeline"],
            llm_used=False,
            disclaimer=DISCLAIMER_TEXT,
            published_at=candidate.created_at,
        ),
        pip_size=pip_size,
        equity=equity,
    )


def format_signal_from_pipeline(
    candidate: SignalCandidate,
    *,
    confidence: float,
    rationale: str,
    key_risks: list[str],
    sources: list[str],
    llm_used: bool,
    pip_size: float = 0.01,
    equity: float = 10_000.0,
) -> str:
    """Format a signal with LLM pipeline results (P2).

    Uses rationale and key_risks from the LLM analyst when available,
    falls back to deterministic if llm_used=False.
    """
    from datetime import UTC, datetime

    signal = TradingSignal(
        signal_id=candidate.candidate_id,
        candidate=candidate,
        confidence=confidence,
        rationale=rationale,
        key_risks=key_risks,
        sources=sources if sources else ["deterministic_pipeline"],
        llm_used=llm_used,
        disclaimer=DISCLAIMER_TEXT,
        published_at=datetime.now(UTC),
    )
    return format_signal_telegram(
        signal,
        pip_size=pip_size,
        equity=equity,
    )
