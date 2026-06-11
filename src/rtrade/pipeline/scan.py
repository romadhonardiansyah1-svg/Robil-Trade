"""Shared runtime scan pipeline for scheduler and API entrypoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import redis.asyncio as aioredis
import structlog
import yaml

from rtrade.core.config import AppConfig, InstrumentConfig
from rtrade.core.constants import Market, SignalStatus, Timeframe
from rtrade.core.errors import ConfigError, ProviderError
from rtrade.core.timeutil import ensure_utc
from rtrade.data.base import MarketDataProvider
from rtrade.data.ccxt_provider import CcxtProvider
from rtrade.data.finnhub_calendar import FinnhubCalendarProvider
from rtrade.data.ingestion import ingest_candles
from rtrade.data.ratelimit import RateLimiter
from rtrade.data.twelvedata_provider import TwelveDataProvider
from rtrade.delivery.formatter import format_candidate_deterministic
from rtrade.delivery.telegram_bot import TelegramDelivery
from rtrade.guardrails.gate import run_gate
from rtrade.indicators.engine import compute as compute_indicators
from rtrade.indicators.structure import cluster_sr_levels, detect_gaps, detect_swing_points
from rtrade.papertrack.tracker import check_fill, check_outcome
from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.models import EconomicEvent, Signal
from rtrade.persistence.repositories import CandleRepo, EventRepo, InstrumentRepo, SignalRepo
from rtrade.regime.rules import RegimeClassifier
from rtrade.risk.news_filter import check_news_blackout
from rtrade.signals.edge_quality import EdgeQualityConfig
from rtrade.signals.engine import generate_candidate
from rtrade.signals.schemas import DISCLAIMER_TEXT, TradingSignal
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """High-level result returned by runtime scan entrypoints."""

    symbol: str
    timeframe: str
    status: str
    signal_id: str | None = None
    message: str | None = None
    failures: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


async def run_scan(
    symbol: str,
    timeframe: str | Timeframe = Timeframe.H1,
    *,
    config: AppConfig | None = None,
    config_dir: Path | str = Path("config"),
    env_file: Path | str | None = Path(".env"),
    deliver: bool = True,
) -> ScanResult:
    """Run one complete scan cycle for a symbol/timeframe."""
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)
    tf = Timeframe(timeframe)
    instrument = cfg.instrument(symbol)

    redis_client = aioredis.from_url(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = _make_market_provider(instrument, cfg, limiter)
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            inst_row = await InstrumentRepo(session).get_or_create(
                symbol=instrument.symbol,
                market=instrument.market.value,
                provider=instrument.provider,
                provider_symbol=instrument.provider_symbol,
                pip_size=Decimal(str(instrument.pip_size)),
                config=instrument.model_dump(mode="json"),
            )

            since = datetime.now(UTC) - timedelta(days=120)
            await ingest_candles(
                provider, instrument, inst_row.id, tf, CandleRepo(session), since=since
            )

            if tf != Timeframe.H1:
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="ingested_context_only",
                    detail={"timeframe": tf.value},
                )

            if Timeframe.H4 in instrument.timeframes:
                await ingest_candles(
                    provider,
                    instrument,
                    inst_row.id,
                    Timeframe.H4,
                    CandleRepo(session),
                    since=since,
                )

            candle_repo = CandleRepo(session)
            df_1h = _candles_to_df(await candle_repo.latest_n(inst_row.id, Timeframe.H1, 500))
            df_4h = _candles_to_df(await candle_repo.latest_n(inst_row.id, Timeframe.H4, 500))

            if len(df_1h) < 200:
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="insufficient_data",
                    detail={"candles_1h": len(df_1h)},
                )

            df_1h = compute_indicators(df_1h)
            df_4h_ind = compute_indicators(df_4h) if not df_4h.empty else None
            regime = RegimeClassifier().classify(symbol, df_1h)

            atr = float(df_1h.iloc[-1].get("atr", 0.0))
            swings = detect_swing_points(df_1h.tail(200))
            sr_levels = cluster_sr_levels(swings, atr)
            gap_zones = detect_gaps(df_1h.tail(200), atr)

            now = datetime.now(UTC)
            events = await EventRepo(session).get_window(
                now - timedelta(hours=2), now + timedelta(hours=72)
            )
            event_dicts = [_event_to_gate_dict(e) for e in events]
            in_news_blackout, _reason = check_news_blackout(
                event_dicts,
                instrument.related_currencies,
                now,
                before_min=cfg.settings.risk.news_blackout_before_min,
                after_min=cfg.settings.risk.news_blackout_after_min,
            )

            live_price: float | None = None
            try:
                quote = await provider.fetch_quote(instrument.provider_symbol)
                live_price = float(quote.price)
            except ProviderError as exc:
                logger.warning("live quote unavailable, skipping price drift gate", error=str(exc))

            result = await _run_strategies(
                cfg,
                instrument,
                inst_row.id,
                df_1h,
                df_4h_ind,
                sr_levels,
                gap_zones,
                event_dicts,
                in_news_blackout,
                regime,
                live_price,
                session_repo=SignalRepo(session),
                now=now,
            )
            await session.commit()

        if (
            deliver
            and result.message
            and cfg.secrets.telegram_bot_token
            and cfg.secrets.telegram_chat_id
        ):
            telegram = TelegramDelivery(
                cfg.secrets.telegram_bot_token, cfg.secrets.telegram_chat_id
            )
            try:
                await telegram.send_signal(result.message)
            finally:
                await telegram.close()

        return result
    finally:
        await provider.close()
        await redis_client.aclose()
        await engine.dispose()


async def sync_calendar(
    *,
    config: AppConfig | None = None,
    config_dir: Path | str = Path("config"),
    env_file: Path | str | None = Path(".env"),
) -> int:
    """Fetch and upsert economic calendar events."""
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)
    if not cfg.secrets.finnhub_api_key:
        raise ConfigError("FINNHUB_API_KEY is required for calendar sync")

    redis_client = aioredis.from_url(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = FinnhubCalendarProvider(cfg.secrets.finnhub_api_key, limiter)
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    try:
        today = datetime.now(UTC).date()
        events = await provider.fetch_events(today - timedelta(days=1), today + timedelta(days=7))
        orm_events = [
            EconomicEvent(
                id=e.event_id,
                event=e.event,
                currency=e.currency,
                impact=e.impact,
                event_time=e.event_time,
                actual=e.actual,
                forecast=e.forecast,
                previous=e.previous,
                fetched_at=e.fetched_at,
            )
            for e in events
        ]
        async with session_factory() as session:
            count = await EventRepo(session).upsert_many(orm_events)
            await session.commit()
            return count
    finally:
        await provider.close()
        await redis_client.aclose()
        await engine.dispose()


async def track_paper_signals(
    *,
    config: AppConfig | None = None,
    config_dir: Path | str = Path("config"),
    env_file: Path | str | None = Path(".env"),
) -> int:
    """Advance paper-trade statuses for open signals using latest candles."""
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)
    updates = 0

    try:
        async with session_factory() as session:
            signal_repo = SignalRepo(session)
            candle_repo = CandleRepo(session)
            for signal in await signal_repo.open_for_tracking():
                latest = await candle_repo.latest(signal.instrument_id, Timeframe(signal.timeframe))
                if latest is None:
                    continue
                candle_ts = ensure_utc(latest.ts)
                if signal.status == SignalStatus.PUBLISHED.value:
                    update = check_fill(
                        signal.signal_id,
                        signal.action,
                        float(signal.entry_limit or 0),
                        signal.valid_until or signal.bar_ts,
                        float(latest.high),
                        float(latest.low),
                        candle_ts,
                    )
                else:
                    update = check_outcome(
                        signal.signal_id,
                        signal.action,
                        float(signal.entry_limit or 0),
                        float(signal.stop_loss or 0),
                        float(signal.take_profit or 0),
                        float(latest.high),
                        float(latest.low),
                        candle_ts,
                    )
                if update is None:
                    continue
                await signal_repo.update_tracking_status(
                    update.signal_id,
                    status=update.new_status.value,
                    resolved_at=update.resolved_at,
                    outcome_r=Decimal(str(update.outcome_r))
                    if update.outcome_r is not None
                    else None,
                )
                updates += 1
            await session.commit()
            return updates
    finally:
        await engine.dispose()


async def _run_strategies(
    cfg: AppConfig,
    instrument: InstrumentConfig,
    instrument_id: int,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame | None,
    sr_levels: list[Any],
    gap_zones: list[Any],
    event_dicts: list[dict[str, object]],
    in_news_blackout: bool,
    regime: Any,
    live_price: float | None,
    *,
    session_repo: SignalRepo,
    now: datetime,
) -> ScanResult:
    for strategy_name, strategy_cls in STRATEGY_REGISTRY.items():
        strategy = strategy_cls()
        if strategy.required_regime != regime.regime:
            continue

        strategy_cfg = _load_strategy_config(strategy_name)
        valid_bars = strategy_cfg.get_int("levels.valid_bars", 6)
        edge_cfg = _edge_quality_config(cfg)

        candidate = generate_candidate(
            strategy,
            strategy_cfg,
            instrument,
            df_1h,
            df_4h,
            sr_levels,
            gap_zones,
            has_high_impact_event=in_news_blackout,
            session_active=_session_active(instrument, now),
            funding_extreme=False,
            risk_pct=cfg.settings.risk.risk_per_trade_pct,
            equity=10_000.0,
            rr_min=cfg.settings.risk.rr_min,
            confluence_min_score=cfg.settings.signal.confluence_min_score,
            valid_bars=valid_bars,
            edge_quality_enabled=cfg.settings.signal.edge_quality.enabled,
            edge_quality_config=edge_cfg,
        )
        if candidate is None:
            continue

        duplicate = await session_repo.get_by_dedup(
            instrument_id=instrument_id,
            timeframe=candidate.timeframe.value,
            strategy=candidate.strategy,
            bar_ts=candidate.bar_ts,
        )
        if duplicate is not None:
            return ScanResult(
                symbol=instrument.symbol,
                timeframe=candidate.timeframe.value,
                status="duplicate",
                signal_id=duplicate.signal_id,
            )

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        signals_today = await session_repo.count_since(
            instrument_id=instrument_id,
            start=day_start,
            end=day_start + timedelta(days=1),
        )
        paper_outcomes = await session_repo.recent_outcomes(
            candidate.strategy,
            cfg.settings.risk.expectancy_guard_window,
        )
        gate = run_gate(
            candidate,
            latest_candle_ts=candidate.bar_ts,
            timeframe=candidate.timeframe,
            staleness_factor=cfg.settings.signal.candle_staleness_factor,
            live_price=live_price,
            price_drift_max_pct=cfg.settings.signal.price_drift_max_pct,
            now=now,
            events=event_dicts,
            related_currencies=instrument.related_currencies,
            news_blackout_before_min=cfg.settings.risk.news_blackout_before_min,
            news_blackout_after_min=cfg.settings.risk.news_blackout_after_min,
            regime=regime.regime,
            required_regime=strategy.required_regime,
            signals_today=signals_today,
            max_signals_per_day=cfg.settings.signal.max_signals_per_day_per_instrument,
            paper_outcomes=paper_outcomes,
            expectancy_window=cfg.settings.risk.expectancy_guard_window,
        )

        if not gate.passed:
            await session_repo.add(
                _signal_model(
                    candidate,
                    instrument_id,
                    status=SignalStatus.REJECTED,
                    confidence=Decimal("0"),
                    payload={
                        "candidate": candidate.model_dump(mode="json"),
                        "gate": gate.model_dump(mode="json"),
                    },
                )
            )
            return ScanResult(
                symbol=instrument.symbol,
                timeframe=candidate.timeframe.value,
                status="rejected",
                signal_id=candidate.candidate_id,
                failures=[f"{f.gate_id}: {f.reason}" for f in gate.failures],
            )

        confidence = Decimal(str(round(candidate.confluence_score / 100, 4)))
        signal = TradingSignal(
            signal_id=candidate.candidate_id,
            candidate=candidate,
            confidence=float(confidence),
            rationale="Sinyal deterministik: semua guardrail utama lolos.",
            key_risks=["Eksekusi tetap manual; validasi ulang spread dan berita sebelum entry."],
            sources=["deterministic_pipeline"],
            llm_used=False,
            disclaimer=DISCLAIMER_TEXT,
            published_at=now,
        )
        await session_repo.add(
            _signal_model(
                candidate,
                instrument_id,
                status=SignalStatus.PUBLISHED,
                confidence=confidence,
                payload=signal.model_dump(mode="json"),
                published_at=now,
            )
        )
        return ScanResult(
            symbol=instrument.symbol,
            timeframe=candidate.timeframe.value,
            status="published",
            signal_id=candidate.candidate_id,
            message=format_candidate_deterministic(candidate, pip_size=instrument.pip_size),
            detail={"confluence": candidate.confluence_score, "regime": regime.regime.value},
        )

    return ScanResult(
        symbol=instrument.symbol,
        timeframe=Timeframe.H1.value,
        status="no_signal",
        detail={"regime": regime.regime.value},
    )


def _make_market_provider(
    instrument: InstrumentConfig,
    cfg: AppConfig,
    limiter: RateLimiter,
) -> MarketDataProvider:
    if instrument.provider == "twelvedata":
        return TwelveDataProvider(cfg.secrets.twelvedata_api_key, limiter)
    if instrument.provider == "ccxt_binance":
        return CcxtProvider(limiter)
    raise ConfigError(f"unsupported market data provider: {instrument.provider}")


def _candles_to_df(candles: list[Any]) -> pd.DataFrame:
    rows = [
        {
            "ts": c.ts,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
        }
        for c in candles
    ]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).set_index("ts")
    return df[["open", "high", "low", "close", "volume"]]


def _load_strategy_config(strategy_name: str) -> StrategyConfig:
    path = Path("config") / "strategies" / f"{strategy_name}.yaml"
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ConfigError(f"strategy config must be a mapping: {path}")
    return StrategyConfig(raw=doc)


def _edge_quality_config(cfg: AppConfig) -> EdgeQualityConfig:
    eq = cfg.settings.signal.edge_quality
    return EdgeQualityConfig(
        min_score=eq.min_score,
        max_spread_atr=eq.max_spread_atr,
        min_atr_percentile=eq.min_atr_percentile,
        max_atr_percentile=eq.max_atr_percentile,
        max_opposing_wick_ratio=eq.max_opposing_wick_ratio,
        max_total_wick_body_ratio=eq.max_total_wick_body_ratio,
        min_body_atr=eq.min_body_atr,
        min_volume_ratio=eq.min_volume_ratio,
        volume_window=eq.volume_window,
        max_range_expansion_atr=eq.max_range_expansion_atr,
        max_entry_distance_atr=eq.max_entry_distance_atr,
    )


def _event_to_gate_dict(event: EconomicEvent) -> dict[str, object]:
    return {
        "event": event.event,
        "currency": event.currency,
        "impact": event.impact,
        "event_time": event.event_time,
    }


def _session_active(instrument: InstrumentConfig, now: datetime) -> bool:
    if not instrument.session_filter or instrument.market == Market.CRYPTO:
        return True
    hour = ensure_utc(now).hour
    return 7 <= hour <= 21


def _signal_model(
    candidate: Any,
    instrument_id: int,
    *,
    status: SignalStatus,
    confidence: Decimal,
    payload: dict[str, Any],
    published_at: datetime | None = None,
) -> Signal:
    return Signal(
        signal_id=candidate.candidate_id,
        instrument_id=instrument_id,
        timeframe=candidate.timeframe.value,
        strategy=candidate.strategy,
        action=candidate.action.value,
        status=status.value,
        entry_limit=Decimal(str(candidate.levels.entry_limit)),
        stop_loss=Decimal(str(candidate.levels.stop_loss)),
        take_profit=Decimal(str(candidate.levels.take_profit)),
        position_size=Decimal(str(candidate.position_size)),
        risk_pct=Decimal(str(candidate.risk_pct)),
        confluence_score=candidate.confluence_score,
        confidence=confidence,
        bar_ts=candidate.bar_ts,
        valid_until=candidate.valid_until,
        published_at=published_at,
        payload=payload,
    )
