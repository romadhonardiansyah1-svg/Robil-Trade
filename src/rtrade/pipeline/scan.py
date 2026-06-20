"""Shared runtime scan pipeline for scheduler and API entrypoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import structlog
import yaml

from rtrade.core.config import AppConfig, GateProfile, InstrumentConfig
from rtrade.core.constants import (
    FUNDING_EXTREME_ABS,
    AuditStage,
    Market,
    SignalStatus,
    Timeframe,
)
from rtrade.core.errors import ConfigError, ProviderError
from rtrade.core.timeutil import ensure_utc, timeframe_duration
from rtrade.data.base import CalendarProvider, MarketDataProvider
from rtrade.data.ccxt_provider import CcxtProvider
from rtrade.data.composite_calendar import CompositeCalendarProvider
from rtrade.data.composite_market import CompositeMarketDataProvider
from rtrade.data.finnhub_calendar import FinnhubCalendarProvider
from rtrade.data.ingestion import ingest_candles
from rtrade.data.oanda_provider import OandaProvider
from rtrade.data.ratelimit import RateLimiter, market_bucket
from rtrade.data.twelvedata_provider import TwelveDataProvider
from rtrade.delivery.formatter import format_candidate_deterministic
from rtrade.delivery.telegram_bot import TelegramDelivery
from rtrade.guardrails.gate import run_gate
from rtrade.indicators.engine import compute as compute_indicators
from rtrade.indicators.engine import snapshot as indicator_snapshot
from rtrade.indicators.structure import cluster_sr_levels, detect_gaps, detect_swing_points
from rtrade.llm.budget_guard import BudgetGuard
from rtrade.llm.cascade import should_escalate
from rtrade.llm.context_pack import ContextPack, build_context_pack
from rtrade.llm.model_router import resolve_role_model
from rtrade.llm.pipeline import PipelineDecision, run_llm_pipeline
from rtrade.papertrack.excursion import compute_excursion
from rtrade.papertrack.minute_resolution import resolve_ambiguous_bar
from rtrade.papertrack.tracker import CandleBar, replay_signal
from rtrade.papertrack.virtual_exits import evaluate_virtual_exits
from rtrade.persistence.db import _get_engine, _get_redis, create_session_factory
from rtrade.persistence.models import DerivativesSnapshot, EconomicEvent, Signal
from rtrade.persistence.repositories import (
    AuditRepo,
    CalendarSourceHealthRepo,
    CandleRepo,
    EventRepo,
    InstrumentRepo,
    SignalRepo,
    StrategyStateRepo,
)
from rtrade.pipeline.mtf import aligned, h4_trend_bias
from rtrade.regime.rules import RegimeClassifier
from rtrade.risk.news_filter import check_news_blackout, high_impact_within
from rtrade.signals.edge_quality import EdgeQualityConfig
from rtrade.signals.engine import generate_candidate
from rtrade.signals.grading import grade_signal, risk_multiplier
from rtrade.signals.schemas import DISCLAIMER_TEXT, SignalCandidate, TradingSignal
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig

logger = structlog.get_logger(__name__)


_SCAN_POOL_CACHE: Any = None


def _build_llm_client(cfg: AppConfig) -> Any:
    """LLMClient dengan credential pool singleton (C5).

    Pool dibangun SEKALI per proses lalu dipakai ulang → cooldown rate-limit
    bertahan antar-kandidat & antar-cycle. redis_client diteruskan supaya cooldown
    juga persisten di Redis (lintas proses).
    """
    global _SCAN_POOL_CACHE
    from rtrade.llm.client import LLMClient
    from rtrade.llm.pool_builder import build_scan_pool

    if _SCAN_POOL_CACHE is None:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(cfg.secrets.redis_url)
        except Exception:
            redis_client = None
        _SCAN_POOL_CACHE = build_scan_pool(cfg, redis_client=redis_client)

    return LLMClient(
        timeout=cfg.settings.llm.timeout_seconds,
        temperature=cfg.settings.llm.temperature,
        credential_pool=_SCAN_POOL_CACHE,
    )


# A2: process-scoped RegimeClassifier singleton so per-symbol hysteresis state
# (RegimeClassifier._prev) persists across scans. classify() mutates this shared
# _prev dict, so it is intentionally NOT offloaded to a worker thread (A1) — it
# runs on the single event loop, which serializes the mutation and avoids a data
# race if scan jobs were to overlap. Pure indicator/structure computations have no
# shared state and ARE offloaded via asyncio.to_thread.
_REGIME_CLASSIFIER = RegimeClassifier()


# W8: HMM regime shadow cache.
_HMM_CACHE: dict[str, Any] = {}


def _hmm_shadow_classify(symbol: str, df: pd.DataFrame) -> Any | None:
    """Classify with saved HMM model; None when no model on disk (W8)."""

    from rtrade.regime.hmm import HMMRegimeDetector

    detector = _HMM_CACHE.get(symbol)
    if detector is None:
        path = Path("models") / f"hmm_{symbol}.joblib"
        if not path.exists():
            return None
        from rtrade.ml.model_io import load_model

        detector = load_model(path)
        _HMM_CACHE[symbol] = detector
    if not isinstance(detector, HMMRegimeDetector) or not detector.is_trained:
        return None
    return detector.classify(symbol, df)


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


async def _ingest_incremental(
    provider: MarketDataProvider,
    instrument: InstrumentConfig,
    instrument_id: int,
    tf: Timeframe,
    repo: CandleRepo,
    now: datetime,
) -> int:
    """Fetch only what's missing: watermark − 2 bars overlap, tiny limit."""
    latest = await repo.latest(instrument_id, tf)
    # BUG 5: freshness short-circuit. When the latest candle is still within the
    # current bar (age <= one bar), no newer closed bar exists yet, so skip the
    # provider call entirely instead of wasting a credit (Property 6, req 2.10).
    if latest is not None and now - ensure_utc(latest.ts) <= timeframe_duration(tf):
        return 0
    if latest is None:
        # P1-7 (FR-DATA-09): cold start backfills a full warmup window. 5000 is
        # the per-request ceiling for both TwelveData and OANDA.
        limit = 5000
        # OANDA's from+count returns `count` bars going FORWARD from `from`, so a
        # fixed 120-day start would return only the OLDEST `limit` bars on M5/M15
        # and never reach the present. Size the window so from+count lands at ~now.
        # For TwelveData TFs (H1/H4/D1) this min() stays 120d, so its behaviour is
        # unchanged.
        since = now - min(timedelta(days=120), limit * timeframe_duration(tf))
    else:
        since = ensure_utc(latest.ts) - 2 * timeframe_duration(tf)
        limit = 10
    return await ingest_candles(
        provider, instrument, instrument_id, tf, repo, since=since, limit=limit
    )


def _warmup_deficit(
    *, bars_1h: int, bars_4h: int, has_4h: bool, warmup_bars: int
) -> dict[str, int] | None:
    """P1-7 (G-07): decide whether a scan is still under its warmup window.

    Pure decision helper so the cold-start safety property can be verified
    deterministically. Returns the abstain detail when under-warmed (1h checked
    first), or ``None`` once a full warmup window is held on every required
    timeframe. Behaviour for a fully warmed instrument is unchanged: with the
    default ``warmup_bars`` the steady-state load (500 closed bars) clears both
    checks exactly as before.
    """
    if bars_1h < warmup_bars:
        return {"bars_1h": bars_1h, "required": warmup_bars}
    if has_4h and bars_4h < warmup_bars:
        return {"bars_4h": bars_4h, "required": warmup_bars}
    return None


def _warmup_deficit_mtf(
    *,
    bars_entry: int,
    entry_tf: Timeframe,
    bars_anchor: int,
    anchor_tf: Timeframe,
    warmup_bars: int,
) -> dict[str, int | str] | None:
    """P1-7 generalized to MTF: abstain until BOTH the entry tf and the anchor tf
    hold a full warmup window. The entry tf is checked first so its deficit is the
    one surfaced. Returns the abstain detail, or ``None`` once both are warmed.
    """
    if bars_entry < warmup_bars:
        return {"timeframe": entry_tf.value, "bars": bars_entry, "required": warmup_bars}
    if bars_anchor < warmup_bars:
        return {"timeframe": anchor_tf.value, "bars": bars_anchor, "required": warmup_bars}
    return None


def _is_entry_timeframe(instrument: InstrumentConfig, tf: Timeframe) -> bool:
    """True when ``tf`` is one of the instrument's resolved entry timeframes.

    Legacy default (no entry_timeframes configured) → only H1 is an entry tf,
    preserving the original H1-entry / H4-context pipeline.
    """
    return tf in instrument.resolved_entry_timeframes()


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

    redis_client = _get_redis(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = _make_market_provider(instrument, cfg, limiter)
    engine = _get_engine(cfg.secrets.database_url)
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

            now = datetime.now(UTC)
            candle_repo = CandleRepo(session)

            await _ingest_incremental(provider, instrument, inst_row.id, tf, candle_repo, now)

            entry_tfs = instrument.resolved_entry_timeframes()
            anchor_tf = instrument.resolved_anchor_timeframe()
            mtf_mode = bool(instrument.entry_timeframes)

            # Ingest-only for any non-entry timeframe (incl. the anchor tf).
            if tf not in entry_tfs:
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="ingested_context_only",
                    detail={"timeframe": tf.value},
                )

            # Refresh the anchor tf so the trend bias is current.
            if anchor_tf != tf and anchor_tf in instrument.timeframes:
                latest_anchor = await candle_repo.latest(inst_row.id, anchor_tf)
                due_anchor = latest_anchor is None or (
                    ensure_utc(latest_anchor.ts) + 2 * timeframe_duration(anchor_tf) <= now
                )
                if due_anchor:
                    await _ingest_incremental(
                        provider, instrument, inst_row.id, anchor_tf, candle_repo, now
                    )

            warmup_bars = cfg.settings.signal.warmup_bars
            load_n = max(500, warmup_bars)
            df_1h = _candles_to_df(await candle_repo.latest_n(inst_row.id, tf, load_n))
            df_4h = _candles_to_df(await candle_repo.latest_n(inst_row.id, anchor_tf, load_n))

            deficit = _warmup_deficit_mtf(
                bars_entry=len(df_1h),
                entry_tf=tf,
                bars_anchor=len(df_4h),
                anchor_tf=anchor_tf,
                warmup_bars=warmup_bars,
            )
            if deficit is not None:
                await session.commit()
                return ScanResult(
                    symbol=symbol,
                    timeframe=tf.value,
                    status="abstain_warmup",
                    detail=deficit,
                )

            # H4 trend bias from the raw anchor closes (pre-indicator; only needs close).
            bias = h4_trend_bias(df_4h)

            df_1h = await asyncio.to_thread(compute_indicators, df_1h)
            df_4h_ind = (
                await asyncio.to_thread(compute_indicators, df_4h) if not df_4h.empty else None
            )
            # A1: regime classify is intentionally NOT offloaded. _REGIME_CLASSIFIER is a
            # process-scoped shared singleton whose classify() mutates per-symbol hysteresis
            # state (self._prev); running it in the threadpool could race on that shared dict
            # if scan jobs overlap. Keeping it on the single event loop serializes the
            # mutation safely. Indicators/structure ARE offloaded (pure, no shared state).
            regime = _REGIME_CLASSIFIER.classify(symbol, df_1h)

            # W8: HMM regime shadow classification.
            try:
                hmm_state = _hmm_shadow_classify(symbol, df_1h)
                if hmm_state is not None:
                    await AuditRepo(session).add(
                        stage=AuditStage.REGIME_SHADOW.value,
                        ok=hmm_state.regime == regime.regime,
                        detail={
                            "rule": regime.regime.value,
                            "hmm": hmm_state.regime.value,
                            "prob": hmm_state.probability,
                        },
                    )
            except Exception as exc:
                logger.warning("hmm shadow failed", error=str(exc))

            atr = float(df_1h.iloc[-1].get("atr", 0.0))
            swings = await asyncio.to_thread(detect_swing_points, df_1h.tail(200))
            sr_levels = await asyncio.to_thread(cluster_sr_levels, swings, atr)
            gap_zones = await asyncio.to_thread(detect_gaps, df_1h.tail(200), atr)

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

            # FR-CAL-04: pakai freshest source health (lebih akurat dari MAX(fetched_at)).
            calendar_ts = await CalendarSourceHealthRepo(session).freshest_success()
            if calendar_ts is None:
                calendar_ts = await EventRepo(session).latest_fetch_ts()  # backwards-compat
            stale_hours = cfg.settings.calendar.stale_after_hours
            calendar_stale = instrument.market != Market.CRYPTO and (
                calendar_ts is None
                or (now - ensure_utc(calendar_ts)) > timedelta(hours=stale_hours)
            )

            live_price: float | None = None
            try:
                quote = await provider.fetch_quote(instrument.provider_symbol)
                live_price = float(quote.price)
            except ProviderError as exc:
                logger.warning("live quote unavailable, skipping price drift gate", error=str(exc))

            # W2: Wire derivatives (funding rate + OI).
            derivatives_data: dict[str, Any] | None = None
            funding_extreme = False
            if instrument.derivatives and isinstance(provider, CcxtProvider):
                try:
                    funding = await provider.fetch_funding_rate(instrument.provider_symbol)
                    oi = await provider.fetch_open_interest(instrument.provider_symbol)
                    rate = float(funding.funding_rate)
                    funding_extreme = abs(rate) >= FUNDING_EXTREME_ABS
                    derivatives_data = {
                        "funding_rate": rate,
                        "funding_extreme_flag": funding_extreme,
                        "oi_change_24h": None,
                        "open_interest": float(oi.open_interest),
                    }
                    session.add(
                        DerivativesSnapshot(
                            instrument_id=inst_row.id,
                            ts=now,
                            funding_rate=funding.funding_rate,
                            open_interest=oi.open_interest,
                        )
                    )
                except ProviderError as exc:
                    logger.warning("derivatives fetch failed", error=str(exc))

            # W3: Wire live spread.
            spread: float | None = None
            try:
                spread = await provider.fetch_spread(instrument.provider_symbol)
            except Exception as exc:
                logger.warning("spread fetch failed", error=str(exc))

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
                state_repo=StrategyStateRepo(session),
                audit_repo=AuditRepo(session),
                now=now,
                calendar_stale=calendar_stale,
                funding_extreme=funding_extreme,
                derivatives_data=derivatives_data,
                spread=spread,
                entry_tf=tf,
                bias=bias,
                enforce_bias=mtf_mode,
            )
            await session.commit()

        # F3: Honest delivery with mark_delivery + audit.
        sent = False
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
                sent = await telegram.send_signal(result.message)
                logger.info(
                    "telegram delivery",
                    signal_id=result.signal_id,
                    sent=sent,
                )
            except Exception as exc:
                logger.error(
                    "telegram delivery failed — signal still PUBLISHED",
                    signal_id=result.signal_id,
                    error=str(exc),
                )
            finally:
                await telegram.close()

        # F3: Persist delivery status.
        if result.signal_id:
            async with session_factory() as session:
                await SignalRepo(session).mark_delivery(
                    result.signal_id,
                    sent=sent,
                    error=None if sent else "telegram send failed",
                    at=datetime.now(UTC),
                )
                await AuditRepo(session).add(
                    stage=AuditStage.DELIVERY.value,
                    ok=sent,
                    signal_id=result.signal_id,
                    detail={"sent": sent},
                )
                await session.commit()

        return result
    finally:
        await provider.close()


def _make_calendar_provider(name: str, cfg: AppConfig, limiter: RateLimiter) -> CalendarProvider:
    """Factory source-agnostic berdasarkan nama config (FR-CAL-07)."""
    if name == "investing":
        from rtrade.data.investing_calendar import InvestingCalendarProvider

        return InvestingCalendarProvider()
    if name == "static_high_impact":
        from rtrade.data.static_calendar import StaticCalendarProvider

        return StaticCalendarProvider()
    if name == "finnhub":
        if not cfg.secrets.finnhub_api_key:
            raise ConfigError("finnhub source enabled but FINNHUB_API_KEY empty")
        return FinnhubCalendarProvider(cfg.secrets.finnhub_api_key, limiter)
    if name == "nasdaq":
        from rtrade.data.nasdaq_calendar import NasdaqCalendarProvider

        return NasdaqCalendarProvider()
    raise ConfigError(f"unknown calendar source: {name!r}")


async def _calendar_alert(message: str) -> None:
    """Inline alert callback untuk composite (route ke AlertManager via jobs, P2-5)."""
    try:
        from rtrade.monitoring.alerts import AlertType
        from rtrade.scheduler.jobs import _send_failure_alert

        await _send_failure_alert(message, alert_type=AlertType.PROVIDER_DOWN)
    except Exception as exc:
        logger.warning("calendar alert callback failed", error=str(exc))


async def sync_calendar(
    *,
    config: AppConfig | None = None,
    config_dir: Path | str = Path("config"),
    env_file: Path | str | None = Path(".env"),
) -> int:
    """Fetch and upsert economic calendar events via composite provider."""
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)

    redis_client = _get_redis(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    engine = _get_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    cal_cfg = cfg.settings.calendar
    enabled = [s for s in cal_cfg.sources if s.enabled]
    if not enabled:
        raise ConfigError("no enabled calendar sources")

    providers: list[CalendarProvider] = []
    names: list[str] = []
    for src in enabled:
        try:
            providers.append(_make_calendar_provider(src.name, cfg, limiter))
            names.append(src.name)
        except (NotImplementedError, ConfigError) as exc:
            logger.warning(
                "calendar source not available, skipping",
                source=src.name,
                error=str(exc),
            )

    if not providers:
        raise ConfigError("no calendar sources could be built")

    composite = CompositeCalendarProvider(providers, names=names, alert_callback=_calendar_alert)
    try:
        today = datetime.now(UTC).date()
        start = today - timedelta(days=cal_cfg.sync_lookback_days)
        end = today + timedelta(days=cal_cfg.sync_lookforward_days)
        events = await composite.fetch_events(start, end)
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
            # Persist per-source health.
            health_repo = CalendarSourceHealthRepo(session)
            for name, h in composite.health_snapshot().items():
                await health_repo.upsert(
                    name,
                    last_success=h.last_success,
                    last_error=h.last_error,
                    consecutive_failures=h.consecutive_failures,
                    last_attempt=h.last_attempt,
                )
            await session.commit()
            return count
    finally:
        await composite.close()


async def track_paper_signals(
    *,
    config: AppConfig | None = None,
    config_dir: Path | str = Path("config"),
    env_file: Path | str | None = Path(".env"),
) -> int:
    """Advance paper-trade statuses for open signals using latest candles."""
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)
    engine = _get_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)
    updates = 0

    async with session_factory() as session:
        signal_repo = SignalRepo(session)
        candle_repo = CandleRepo(session)
        for signal in await signal_repo.open_for_tracking():
            start = ensure_utc(signal.published_at or signal.bar_ts)
            rows = await candle_repo.get_range(
                signal.instrument_id,
                Timeframe(signal.timeframe),
                start,
                datetime.now(UTC),
            )
            if not rows:
                continue
            bars = [
                CandleBar(
                    ts=ensure_utc(r.ts),
                    high=float(r.high),
                    low=float(r.low),
                    close=float(r.close),
                )
                for r in rows
            ]
            entry = float(signal.entry_limit or 0)
            sl = float(signal.stop_loss or 0)
            tp = float(signal.take_profit or 0)

            update = replay_signal(
                signal.signal_id,
                signal.action,
                entry,
                sl,
                tp,
                signal.valid_until or signal.bar_ts,
                already_filled=signal.status == SignalStatus.FILLED.value,
                candles=bars,
            )
            if update is None or update.new_status.value == signal.status:
                continue

            # --- T22: 1-minute resolution for ambiguous bars (crypto only) ---
            final_status = update.new_status
            outcome_r = update.outcome_r
            resolution = "bar"
            if update.new_status == SignalStatus.SL_HIT and _bar_is_ambiguous(
                signal.action, sl, tp, bars, update.resolved_at
            ):
                resolution = "worst_case"
                inst_row = await InstrumentRepo(session).get_by_id(signal.instrument_id)
                if inst_row is not None and inst_row.market == "crypto":
                    minute_bars = await _fetch_minute_bars(cfg, inst_row, update.resolved_at)
                    if minute_bars:
                        first = resolve_ambiguous_bar(signal.action, entry, sl, tp, minute_bars)
                        resolution = "minute"
                        if first == "TP":
                            final_status = SignalStatus.TP_HIT
                            sl_dist = abs(entry - sl) or 1.0
                            outcome_r = abs(tp - entry) / sl_dist

            await signal_repo.update_tracking_status(
                update.signal_id,
                status=final_status.value,
                resolved_at=update.resolved_at,
                outcome_r=Decimal(str(outcome_r)) if outcome_r is not None else None,
            )
            await signal_repo.merge_payload(signal.signal_id, "resolution", resolution)

            # --- T23 + T24 + T30: analytics when trade RESOLVED ---
            if final_status in (SignalStatus.TP_HIT, SignalStatus.SL_HIT):
                fill_idx = _first_touch_index(signal.action, entry, bars)
                after_fill = bars[fill_idx:] if fill_idx is not None else []
                if after_fill:
                    atr_val = (
                        float(
                            (signal.payload.get("candidate") or {})
                            .get("levels", {})
                            .get("atr_at_signal", 0)
                        )
                        or 1.0
                    )
                    await signal_repo.merge_payload(
                        signal.signal_id,
                        "virtual_exits",
                        evaluate_virtual_exits(signal.action, entry, sl, tp, atr_val, after_fill),
                    )
                    mae_r, mfe_r = compute_excursion(signal.action, entry, sl, after_fill)
                    await signal_repo.merge_payload(
                        signal.signal_id,
                        "excursion",
                        {"mae_r": mae_r, "mfe_r": mfe_r},
                    )
                if (
                    final_status == SignalStatus.SL_HIT
                    and cfg.settings.llm.enabled
                    and cfg.settings.llm.coroner_enabled
                ):
                    try:
                        from rtrade.llm.coroner import run_coroner

                        report = await run_coroner(
                            _build_llm_client(cfg),
                            model=resolve_role_model(cfg, "analyst"),
                            candidate_payload=signal.payload.get("candidate") or {},
                            price_path=[
                                {
                                    "ts": str(b.ts),
                                    "high": b.high,
                                    "low": b.low,
                                    "close": b.close,
                                }
                                for b in after_fill[:12]
                            ],
                        )
                        await signal_repo.merge_payload(
                            signal.signal_id,
                            "coroner",
                            report.model_dump(),
                        )
                    except Exception as exc:
                        logger.warning("coroner failed", error=str(exc))
            updates += 1
        await session.commit()
        return updates


def _bar_is_ambiguous(
    action: str,
    stop_loss: float,
    take_profit: float,
    bars: list[CandleBar],
    resolved_at: datetime,
) -> bool:
    """True if the resolution bar hits SL AND TP in the same bar (W1)."""
    for bar in bars:
        if bar.ts != resolved_at:
            continue
        if action == "BUY":
            return bar.low <= stop_loss and bar.high >= take_profit
        return bar.high >= stop_loss and bar.low <= take_profit
    return False


def _first_touch_index(action: str, entry: float, bars: list[CandleBar]) -> int | None:
    """Index of the first bar where entry is touched (W1)."""
    for i, bar in enumerate(bars):
        if bar.low <= entry <= bar.high:
            return i
    return None


async def _fetch_minute_bars(
    cfg: AppConfig, inst_row: Any, bar_open_ts: datetime
) -> list[CandleBar]:
    """Fetch 1m candles for one ambiguous 1H bar (crypto only, best-effort) (W1)."""
    instrument = cfg.instrument(inst_row.symbol)
    redis_client = _get_redis(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = _make_market_provider(instrument, cfg, limiter)
    try:
        candles = await provider.fetch_ohlcv(
            instrument.provider_symbol,
            Timeframe.M1,
            since=ensure_utc(bar_open_ts),
            limit=70,
        )
        end = ensure_utc(bar_open_ts) + timeframe_duration(Timeframe.H1)
        return [
            CandleBar(
                ts=ensure_utc(c.ts),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
            )
            for c in candles
            if ensure_utc(c.ts) < end
        ]
    except Exception as exc:
        logger.warning("minute fetch failed — keeping worst-case", error=str(exc))
        return []
    finally:
        await provider.close()


def _build_pack(
    instrument: InstrumentConfig,
    candidate: SignalCandidate,
    df_1h: pd.DataFrame,
    sr_levels: list[Any],
    gap_zones: list[Any],
    regime: Any,
    event_dicts: list[dict[str, object]],
    session_active: bool,
    derivatives_data: dict[str, Any] | None = None,
    similar_setups: dict[str, Any] | None = None,
) -> ContextPack:
    """Build a context pack for the LLM pipeline (F1)."""
    snap = indicator_snapshot(df_1h)
    e = candidate.levels.entry_limit
    sl = candidate.levels.stop_loss
    tp = candidate.levels.take_profit
    sl_dist = abs(e - sl)
    rr = abs(tp - e) / sl_dist if sl_dist > 0 else 0.0
    swings = detect_swing_points(df_1h.tail(200))
    highs = [{"price": p.price, "ts": p.ts.isoformat()} for p in swings if p.is_high][-3:]
    lows = [{"price": p.price, "ts": p.ts.isoformat()} for p in swings if not p.is_high][-3:]
    return build_context_pack(
        symbol=instrument.symbol,
        market=instrument.market.value,
        timeframe=candidate.timeframe,
        session_active=session_active,
        action=candidate.action.value,
        entry=e,
        sl=sl,
        tp=tp,
        rr=rr,
        valid_until=candidate.valid_until.isoformat(),
        strategy=candidate.strategy,
        confluence_breakdown=candidate.confluence_breakdown.model_dump(),
        snapshot=snap,
        swing_highs=highs,
        swing_lows=lows,
        sr_levels=[
            {
                "price": lv.price,
                "strength": lv.strength,
                "is_resistance": lv.is_resistance,
            }
            for lv in sr_levels
        ],
        gap_zones=[{"high": g.high, "low": g.low, "direction": g.direction} for g in gap_zones],
        regime_state=regime.regime.value,
        regime_since=regime.since.isoformat(),
        calendar_events=[
            {
                **ev,
                "event_time": (
                    ev["event_time"].isoformat()
                    if hasattr(ev["event_time"], "isoformat")
                    else ev["event_time"]
                ),
            }
            for ev in event_dicts
        ],
        derivatives=derivatives_data,
        similar_setups=similar_setups,
        df_1h=df_1h,
    )


def _status_for_decision(decision: PipelineDecision) -> SignalStatus:
    """Map pipeline decision to signal status (pure, unit-tested)."""
    if decision in (PipelineDecision.PUBLISH, PipelineDecision.FALLBACK):
        return SignalStatus.PUBLISHED
    if decision == PipelineDecision.REJECTED:
        return SignalStatus.REJECTED
    return SignalStatus.ABSTAINED


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
    state_repo: StrategyStateRepo,
    audit_repo: AuditRepo,
    now: datetime,
    calendar_stale: bool = False,
    funding_extreme: bool = False,
    derivatives_data: dict[str, Any] | None = None,
    spread: float | None = None,
    entry_tf: Timeframe = Timeframe.H1,
    bias: Literal["UP", "DOWN", "NONE"] = "NONE",
    enforce_bias: bool = False,
) -> ScanResult:
    for strategy_name, strategy_cls in STRATEGY_REGISTRY.items():
        strategy = strategy_cls()
        if strategy.required_regime != regime.regime:
            continue

        # F2: Strategy state check.
        if not await state_repo.is_enabled(strategy_name):
            logger.info("strategy disabled, skipping", strategy=strategy_name)
            continue

        strategy_cfg = _load_strategy_config(strategy_name)
        if not _strategy_applies(strategy_cfg, instrument, entry_tf):
            logger.info(
                "strategy not applicable for instrument/timeframe, skipping",
                strategy=strategy_name,
                symbol=instrument.symbol,
                entry_tf=entry_tf.value,
            )
            continue
        valid_bars = strategy_cfg.get_int("levels.valid_bars", 6)
        profile = _active_profile(cfg, strategy_cfg)
        edge_cfg = _edge_quality_config(cfg, min_score=profile.edge_quality_min_score)

        # F2: S2 hard-block — skip strategy if high-impact event within hours.
        hard_block_h = strategy_cfg.get_int("news.hard_block_hours", 0)
        if hard_block_h > 0 and high_impact_within(
            event_dicts, instrument.related_currencies, now, hours=hard_block_h
        ):
            logger.info("news hard-block, skipping strategy", strategy=strategy_name)
            continue

        # W4: Move paper_outcomes query before generate_candidate for throttle.
        paper_outcomes = await session_repo.recent_outcomes(
            strategy_name,
            cfg.settings.risk.expectancy_guard_window,
        )

        # W4: Wire risk throttle.
        risk_pct = cfg.settings.risk.risk_per_trade_pct
        if cfg.settings.risk.throttle_enabled:
            from rtrade.risk.limits import throttled_risk_pct

            risk_pct = throttled_risk_pct(
                risk_pct,
                paper_outcomes,
                window=cfg.settings.risk.throttle_window,
                mult=cfg.settings.risk.throttle_mult,
            )

        candidate = generate_candidate(
            strategy,
            strategy_cfg,
            instrument,
            df_1h,
            df_4h,
            sr_levels,
            gap_zones,
            has_high_impact_event=high_impact_within(
                event_dicts, instrument.related_currencies, now, hours=12
            ),
            session_active=_session_active(instrument, now),
            funding_extreme=funding_extreme,
            risk_pct=risk_pct,
            equity=cfg.settings.risk.equity_usd,
            rr_min=cfg.settings.risk.rr_min,
            confluence_min_score=profile.confluence_min_score,
            valid_bars=valid_bars,
            timeframe=entry_tf,
            edge_quality_enabled=cfg.settings.signal.edge_quality.enabled,
            edge_quality_config=edge_cfg,
            spread=spread,
        )
        if candidate is None:
            continue

        if enforce_bias and not aligned(bias, candidate.action):
            await audit_repo.add(
                stage=AuditStage.CANDIDATE.value,
                ok=False,
                signal_id=candidate.candidate_id,
                detail={
                    "rejected": "h4_bias_misaligned",
                    "bias": bias,
                    "action": candidate.action.value,
                    "entry_tf": entry_tf.value,
                },
            )
            logger.info(
                "candidate rejected: H4 bias misaligned",
                strategy=strategy_name,
                bias=bias,
                action=candidate.action.value,
            )
            continue

        # F2: Audit candidate.
        await audit_repo.add(
            stage=AuditStage.CANDIDATE.value,
            ok=True,
            signal_id=candidate.candidate_id,
            detail={
                "symbol": instrument.symbol,
                "strategy": candidate.strategy,
                "confluence": candidate.confluence_score,
                "spread": spread,
            },
        )

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
        gate = run_gate(
            candidate,
            latest_candle_ts=candidate.bar_ts,
            timeframe=candidate.timeframe,
            staleness_factor=cfg.settings.signal.candle_staleness_factor,
            live_price=live_price,
            live_quote_required=True,  # G-09: fail-CLOSE if quote unavailable
            price_drift_max_pct=cfg.settings.signal.price_drift_max_pct,
            now=now,
            events=event_dicts,
            related_currencies=instrument.related_currencies,
            news_blackout_before_min=cfg.settings.risk.news_blackout_before_min,
            news_blackout_after_min=cfg.settings.risk.news_blackout_after_min,
            calendar_stale=calendar_stale,
            regime=regime.regime,
            required_regime=strategy.required_regime,
            signals_today=signals_today,
            max_signals_per_day=profile.max_signals_per_day_per_instrument,
            paper_outcomes=paper_outcomes,
            expectancy_window=cfg.settings.risk.expectancy_guard_window,
        )

        # F2: Audit gate.
        await audit_repo.add(
            stage=AuditStage.GATE.value,
            ok=gate.passed,
            signal_id=candidate.candidate_id,
            detail={"failures": [f"{f.gate_id}: {f.reason}" for f in gate.failures]},
        )

        if not gate.passed:
            # F2: GR-13 auto-disable strategy.
            if any(f.gate_id == "GR-13" for f in gate.failures):
                await state_repo.set_state(
                    candidate.strategy,
                    enabled=False,
                    reason="GR-13 negative expectancy",
                )
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
        rationale = "Sinyal deterministik: semua guardrail utama lolos."
        key_risks = ["Eksekusi tetap manual; validasi ulang spread dan berita sebelum entry."]
        sources: list[str] = ["deterministic_pipeline"]
        llm_used = False

        # --- LLM pipeline: Analyst → Critic → Verifier (F1) ---
        if cfg.settings.llm.enabled:
            # W6: Case-based memory — similar historical setups.
            from rtrade.ml.similar import find_similar_setups

            history = await session_repo.resolved_with_features(candidate.strategy)
            bd = candidate.confluence_breakdown
            similar = find_similar_setups(
                {
                    "trend": float(bd.trend),
                    "momentum": float(bd.momentum),
                    "structure": float(bd.structure),
                    "volume": float(bd.volume),
                    "macro": float(bd.macro),
                    "hour": float(candidate.bar_ts.hour),
                },
                history,
            )
            similar_setups = similar if similar.get("n") else None

            pack = _build_pack(
                instrument,
                candidate,
                df_1h,
                sr_levels,
                gap_zones,
                regime,
                event_dicts,
                _session_active(instrument, now),
                derivatives_data=derivatives_data,
                similar_setups=similar_setups,
            )
            client = _build_llm_client(cfg)
            flagship_model = resolve_role_model(cfg, "flagship")
            # G-11 (P2-4): per-scan budget guard. One state is shared across
            # the initial call and any flagship escalation so token/USD/wall/
            # step caps accumulate over the whole scan's LLM work.
            budget_guard = BudgetGuard(cfg.settings.llm.budget)
            budget_state = budget_guard.start_scan()
            pres = await run_llm_pipeline(
                candidate,
                pack,
                client,
                confidence_min=profile.confidence_min,
                analyst_model=resolve_role_model(cfg, "analyst"),
                critic_model=resolve_role_model(cfg, "critic"),
                budget_guard=budget_guard,
                budget_state=budget_state,
            )
            # F5: Cascade escalation on doubt band.
            if should_escalate(
                pres,
                low=cfg.settings.llm.escalation_low,
                high=cfg.settings.llm.escalation_high,
                flagship_model=flagship_model,
            ):
                logger.info("escalating to flagship", confidence=pres.confidence)
                pres = await run_llm_pipeline(
                    candidate,
                    pack,
                    client,
                    confidence_min=profile.confidence_min,
                    analyst_model=flagship_model,
                    critic_model=flagship_model,
                    budget_guard=budget_guard,
                    budget_state=budget_state,
                )
            status = _status_for_decision(pres.decision)
            if status != SignalStatus.PUBLISHED:
                await session_repo.add(
                    _signal_model(
                        candidate,
                        instrument_id,
                        status=status,
                        confidence=Decimal(str(pres.confidence)),
                        payload={
                            "candidate": candidate.model_dump(mode="json"),
                            "llm": {
                                "decision": pres.decision.value,
                                "rationale": pres.rationale,
                                "key_risks": pres.key_risks,
                                "latency_ms": pres.pipeline_latency_ms,
                            },
                        },
                    )
                )
                return ScanResult(
                    symbol=instrument.symbol,
                    timeframe=candidate.timeframe.value,
                    status=("rejected_llm" if status == SignalStatus.REJECTED else "abstained"),
                    signal_id=candidate.candidate_id,
                    detail={
                        "decision": pres.decision.value,
                        "confidence": pres.confidence,
                    },
                )

            # C2 / P1-3: SECOND gate after the LLM pipeline ("post_llm").
            # Exercises GR-09 (confidence floor), GR-10 (no LLM number-mutation) and
            # GR-11 (citations) at runtime — these are skipped by the deterministic
            # first gate because confidence/original_candidate/sources/pack_source_ids
            # are omitted there. Re-passes every deterministic gate arg so this is a
            # full 13-gate run on the PUBLISH path. Only reached when llm.enabled.
            post_llm_gate = run_gate(
                candidate,
                latest_candle_ts=candidate.bar_ts,
                timeframe=candidate.timeframe,
                staleness_factor=cfg.settings.signal.candle_staleness_factor,
                live_price=live_price,
                live_quote_required=True,  # G-09: fail-CLOSE if quote unavailable
                price_drift_max_pct=cfg.settings.signal.price_drift_max_pct,
                now=now,
                events=event_dicts,
                related_currencies=instrument.related_currencies,
                news_blackout_before_min=cfg.settings.risk.news_blackout_before_min,
                news_blackout_after_min=cfg.settings.risk.news_blackout_after_min,
                calendar_stale=calendar_stale,
                regime=regime.regime,
                required_regime=strategy.required_regime,
                signals_today=signals_today,
                max_signals_per_day=profile.max_signals_per_day_per_instrument,
                paper_outcomes=paper_outcomes,
                expectancy_window=cfg.settings.risk.expectancy_guard_window,
                # --- P2 additions (only meaningful post-LLM) ---
                confidence=float(pres.confidence),
                confidence_min=profile.confidence_min,
                # GR-10: frozen pre-LLM candidate. Vacuous today (deterministic
                # candidate isn't mutated by the LLM) but durable vs future paths.
                original_candidate=candidate,
                sources=pres.sources or ["deterministic_pipeline"],
                pack_source_ids=set(pack.source_ids) if pack is not None else set(),
            )
            await audit_repo.add(
                stage=AuditStage.GATE.value,
                ok=post_llm_gate.passed,
                signal_id=candidate.candidate_id,
                detail={
                    "phase": "post_llm",
                    "failures": [f"{f.gate_id}: {f.reason}" for f in post_llm_gate.failures],
                },
            )
            if not post_llm_gate.passed:
                await session_repo.add(
                    _signal_model(
                        candidate,
                        instrument_id,
                        status=SignalStatus.REJECTED,
                        confidence=Decimal("0"),
                        payload={
                            "candidate": candidate.model_dump(mode="json"),
                            "gate_post_llm": post_llm_gate.model_dump(mode="json"),
                        },
                    )
                )
                return ScanResult(
                    symbol=instrument.symbol,
                    timeframe=candidate.timeframe.value,
                    status="rejected",
                    signal_id=candidate.candidate_id,
                    failures=[f"{f.gate_id}: {f.reason}" for f in post_llm_gate.failures],
                )

            confidence = Decimal(str(pres.confidence))
            rationale = pres.rationale
            key_risks = pres.key_risks or key_risks
            sources = pres.sources or ["deterministic_pipeline"]
            llm_used = pres.llm_used

        # F4: Signal grading — wire edge quality score (G-05 fix).
        from rtrade.signals.edge_quality import assess_edge_quality

        edge_score: float | None = None
        if cfg.settings.signal.edge_quality.enabled:
            edge_report = assess_edge_quality(
                df_1h,
                candidate.action,
                candidate.levels.entry_limit,
                spread=spread,
                config=edge_cfg,
            )
            edge_score = float(edge_report.score)

        grade_res = grade_signal(
            confluence_score=candidate.confluence_score,
            regime_match=True,
            edge_quality_score=edge_score,  # G-05: was None — now wired
            has_high_impact_event=high_impact_within(
                event_dicts, instrument.related_currencies, now, hours=12
            ),
            confidence=float(confidence),
        )

        signal = TradingSignal(
            signal_id=candidate.candidate_id,
            candidate=candidate,
            confidence=float(confidence),
            rationale=rationale,
            key_risks=key_risks,
            sources=sources,
            llm_used=llm_used,
            disclaimer=DISCLAIMER_TEXT,
            published_at=now,
        )
        payload = signal.model_dump(mode="json")
        payload["grade"] = {
            "grade": grade_res.grade.value,
            "reasons": grade_res.reasons,
            "risk_mult": risk_multiplier(grade_res.grade),
            "scaled_size": round(candidate.position_size * risk_multiplier(grade_res.grade), 4),
        }

        # W5: Bayesian Kelly fraction.
        resolved = [r for r in paper_outcomes if r is not None]
        if len(resolved) >= 30:
            wins = [r for r in resolved if r > 0]
            losses = [r for r in resolved if r <= 0]
            if wins and losses:
                from rtrade.risk.kelly import bayesian_kelly_fraction

                kelly_f = bayesian_kelly_fraction(
                    len(wins),
                    len(losses),
                    sum(wins) / len(wins),
                    abs(sum(losses) / len(losses)),
                )
                payload["kelly"] = {"bayes_fraction": kelly_f, "n": len(resolved)}

        await session_repo.add(
            _signal_model(
                candidate,
                instrument_id,
                status=SignalStatus.PUBLISHED,
                confidence=confidence,
                payload=payload,
                published_at=now,
            )
        )
        return ScanResult(
            symbol=instrument.symbol,
            timeframe=candidate.timeframe.value,
            status="published",
            signal_id=candidate.candidate_id,
            message=format_candidate_deterministic(
                candidate,
                pip_size=instrument.pip_size,
                equity=cfg.settings.risk.equity_usd,
                grade=grade_res.grade.value,
                scaled_size=payload["grade"]["scaled_size"],
            ),
            detail={
                "confluence": candidate.confluence_score,
                "regime": regime.regime.value,
                "grade": grade_res.grade.value,
            },
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
    if instrument.provider == "ccxt_binance":
        return CcxtProvider(limiter)

    legs: list[tuple[str, MarketDataProvider]] = []
    if instrument.provider == "oanda":
        practice = cfg.secrets.oanda_env == "practice"
        for i, (token, account) in enumerate(cfg.secrets.market_keys_for("oanda"), start=1):
            legs.append(
                (
                    f"oanda_{i}",
                    OandaProvider(
                        token,
                        account or "",
                        limiter,
                        bucket=market_bucket("oanda", i),
                        practice=practice,
                    ),
                )
            )
        # TwelveData as last-resort fallback after all OANDA accounts.
        for j, (key, _acc) in enumerate(cfg.secrets.market_keys_for("twelvedata"), start=1):
            legs.append((f"twelvedata_{j}", TwelveDataProvider(key, limiter)))
        if not legs:
            raise ConfigError(
                "provider 'oanda' selected but no OANDA_TOKEN_*/ACCOUNT_* (or TwelveData) configured"
            )
        return CompositeMarketDataProvider(legs)

    if instrument.provider == "twelvedata":
        for j, (key, _acc) in enumerate(cfg.secrets.market_keys_for("twelvedata"), start=1):
            legs.append((f"twelvedata_{j}", TwelveDataProvider(key, limiter)))
        if not legs:
            raise ConfigError("provider 'twelvedata' selected but no TWELVEDATA_API_KEY configured")
        return CompositeMarketDataProvider(legs)

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


def _edge_quality_config(cfg: AppConfig, *, min_score: int | None = None) -> EdgeQualityConfig:
    eq = cfg.settings.signal.edge_quality
    return EdgeQualityConfig(
        min_score=eq.min_score if min_score is None else min_score,
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


def _active_profile(cfg: AppConfig, strategy_cfg: StrategyConfig) -> GateProfile:
    """Select the gate profile for a strategy via its `gate_profile` YAML key.

    Absent / unknown key → the `default` profile (= global values), so swing
    strategies (S1/S2) keep their current thresholds unchanged.
    """
    name = str(strategy_cfg.get("gate_profile", "default"))
    return cfg.settings.signal.profile(name)


def _strategy_applies(
    strategy_cfg: StrategyConfig,
    instrument: InstrumentConfig,
    entry_tf: Timeframe,
) -> bool:
    """True unless the strategy YAML restricts it away from this symbol/timeframe.

    `instruments: [...]` and `entry_timeframes: [...]` are optional allowlists.
    Absent (or empty) → the strategy applies everywhere (S1/S2 back-compat).
    """
    symbols = strategy_cfg.get("instruments")
    if isinstance(symbols, list) and symbols and instrument.symbol not in symbols:
        return False
    tfs = strategy_cfg.get("entry_timeframes")
    return not (isinstance(tfs, list) and tfs and entry_tf.value not in tfs)


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
