"""Repositories — the only place that talks SQL. Callers never build queries.

P0 scope: Instrument + Candle fully implemented (needed by the integration
test AC); Event/Signal/Audit are functional skeletons extended in P1.
All write methods are idempotent where the domain requires it (upserts).
Transaction control (commit/rollback) belongs to the caller.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError
from rtrade.core.timeutil import ensure_utc
from rtrade.persistence.models import (
    Candle,
    EconomicEvent,
    Instrument,
    Signal,
    SignalAudit,
    StrategyState,
)


@dataclass(frozen=True, slots=True)
class CandleRow:
    """Validated candle ready for upsert. `ts` = bar OPEN time, UTC."""

    instrument_id: int
    timeframe: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        ensure_utc(self.ts)
        if not (self.high >= self.open and self.high >= self.close):
            raise DataValidationError(f"candle {self.ts}: high < open/close")
        if not (self.low <= self.open and self.low <= self.close):
            raise DataValidationError(f"candle {self.ts}: low > open/close")
        if self.high < self.low:
            raise DataValidationError(f"candle {self.ts}: high < low")
        if self.volume < 0:
            raise DataValidationError(f"candle {self.ts}: negative volume")


class InstrumentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_symbol(self, symbol: str) -> Instrument | None:
        result = await self._session.execute(select(Instrument).where(Instrument.symbol == symbol))
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        symbol: str,
        market: str,
        provider: str,
        provider_symbol: str,
        pip_size: Decimal,
        config: dict[str, Any] | None = None,
    ) -> Instrument:
        existing = await self.get_by_symbol(symbol)
        if existing is not None:
            return existing
        instrument = Instrument(
            symbol=symbol,
            market=market,
            provider=provider,
            provider_symbol=provider_symbol,
            pip_size=pip_size,
            config=config or {},
        )
        self._session.add(instrument)
        await self._session.flush()  # populate .id
        return instrument

    async def get_by_id(self, instrument_id: int) -> Instrument | None:
        """Get instrument by primary key (W1)."""
        return await self._session.get(Instrument, instrument_id)


class CandleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, rows: list[CandleRow]) -> int:
        """Idempotent bulk upsert; re-ingesting the same bars is safe."""
        if not rows:
            return 0
        stmt = pg_insert(Candle).values([asdict(r) for r in rows])
        stmt = stmt.on_conflict_do_update(
            index_elements=[Candle.instrument_id, Candle.timeframe, Candle.ts],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        await self._session.execute(stmt)
        return len(rows)

    async def get_range(
        self,
        instrument_id: int,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = None,
    ) -> list[Candle]:
        """Candles with open-time in [start, end), ascending."""
        stmt = (
            select(Candle)
            .where(
                Candle.instrument_id == instrument_id,
                Candle.timeframe == timeframe.value,
                Candle.ts >= ensure_utc(start),
                Candle.ts < ensure_utc(end),
            )
            .order_by(Candle.ts.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest(self, instrument_id: int, timeframe: Timeframe) -> Candle | None:
        stmt = (
            select(Candle)
            .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe.value)
            .order_by(Candle.ts.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_n(
        self,
        instrument_id: int,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        """Latest N candles, returned ascending by timestamp."""
        stmt = (
            select(Candle)
            .where(Candle.instrument_id == instrument_id, Candle.timeframe == timeframe.value)
            .order_by(Candle.ts.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        candles = list(result.scalars().all())
        return list(reversed(candles))


class EventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_many(self, events: list[EconomicEvent]) -> int:
        for event in events:
            await self._session.merge(event)
        return len(events)

    async def get_window(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        stmt = (
            select(EconomicEvent)
            .where(
                EconomicEvent.event_time >= ensure_utc(start),
                EconomicEvent.event_time < ensure_utc(end),
            )
            .order_by(EconomicEvent.event_time.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_fetch_ts(self) -> datetime | None:
        """Newest fetched_at across all events (None if table empty)."""
        result = await self._session.execute(select(func.max(EconomicEvent.fetched_at)))
        return result.scalar_one_or_none()


class SignalRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, signal: Signal) -> None:
        self._session.add(signal)

    async def get(self, signal_id: str) -> Signal | None:
        return await self._session.get(Signal, signal_id)

    async def recent(self, limit: int = 20) -> list[Signal]:
        stmt = select(Signal).order_by(Signal.bar_ts.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_dedup(
        self,
        *,
        instrument_id: int,
        timeframe: str,
        strategy: str,
        bar_ts: datetime,
    ) -> Signal | None:
        stmt = select(Signal).where(
            Signal.instrument_id == instrument_id,
            Signal.timeframe == timeframe,
            Signal.strategy == strategy,
            Signal.bar_ts == ensure_utc(bar_ts),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_since(
        self,
        *,
        instrument_id: int,
        start: datetime,
        end: datetime,
        statuses: tuple[str, ...] = ("PUBLISHED",),
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.instrument_id == instrument_id,
                Signal.bar_ts >= ensure_utc(start),
                Signal.bar_ts < ensure_utc(end),
                Signal.status.in_(statuses),
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def recent_outcomes(self, strategy: str, limit: int) -> list[float]:
        stmt = (
            select(Signal.outcome_r)
            .where(Signal.strategy == strategy, Signal.outcome_r.is_not(None))
            .order_by(Signal.resolved_at.desc().nullslast())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [float(r) for r in result.scalars().all() if r is not None]

    async def open_for_tracking(self) -> list[Signal]:
        stmt = (
            select(Signal)
            .where(Signal.status.in_(("PUBLISHED", "FILLED")))
            .order_by(Signal.bar_ts.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_tracking_status(
        self,
        signal_id: str,
        *,
        status: str,
        resolved_at: datetime,
        outcome_r: Decimal | None = None,
    ) -> None:
        values: dict[str, object] = {
            "status": status,
            "resolved_at": ensure_utc(resolved_at),
        }
        if outcome_r is not None:
            values["outcome_r"] = outcome_r
        stmt = update(Signal).where(Signal.signal_id == signal_id).values(**values)
        await self._session.execute(stmt)

    async def mark_delivery(
        self, signal_id: str, *, sent: bool, error: str | None, at: datetime
    ) -> None:
        signal = await self.get(signal_id)
        if signal is None:
            return
        payload = dict(signal.payload)
        payload["delivery"] = {
            "sent": sent,
            "error": error,
            "at": ensure_utc(at).isoformat(),
        }
        signal.payload = payload

    async def merge_payload(self, signal_id: str, key: str, value: object) -> None:
        """Read-modify-write one key into the signal's JSONB payload (W1)."""
        signal = await self.get(signal_id)
        if signal is None:
            return
        payload = dict(signal.payload)
        payload[key] = value
        signal.payload = payload

    async def resolved_with_features(self, strategy: str, limit: int = 500) -> list[dict[str, Any]]:
        """Resolved signals (TP/SL) with confluence features for k-NN (W6)."""
        stmt = (
            select(Signal)
            .where(
                Signal.strategy == strategy,
                Signal.status.in_(("TP_HIT", "SL_HIT")),
                Signal.outcome_r.is_not(None),
            )
            .order_by(Signal.resolved_at.desc().nullslast())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        out: list[dict[str, Any]] = []
        for s in result.scalars().all():
            cand = (s.payload or {}).get("candidate") or {}
            breakdown = cand.get("confluence_breakdown") or {}
            out.append(
                {
                    **{
                        k: float(breakdown.get(k, 0))
                        for k in ("trend", "momentum", "structure", "volume", "macro")
                    },
                    "hour": float(s.bar_ts.hour),
                    "outcome_r": float(s.outcome_r or 0),
                }
            )
        return out


class AuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        stage: str,
        ok: bool,
        detail: dict[str, Any],
        signal_id: str | None = None,
    ) -> None:
        # S9: hash chain — get prev_hash from last audit row
        from rtrade.persistence.audit_chain import build_chain_entry

        prev_hash = "genesis"
        last = await self._session.execute(
            select(SignalAudit).order_by(SignalAudit.id.desc()).limit(1)
        )
        last_row = last.scalar_one_or_none()
        if last_row is not None and isinstance(last_row.detail, dict):
            chain = last_row.detail.get("_chain", {})
            prev_hash = chain.get("row_hash", "genesis")

        chain_entry = build_chain_entry(prev_hash, stage, ok, signal_id, detail)
        detail["_chain"] = chain_entry
        self._session.add(SignalAudit(signal_id=signal_id, stage=stage, ok=ok, detail=detail))


class StrategyStateRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_enabled(self, strategy: str) -> bool:
        row = await self._session.get(StrategyState, strategy)
        return True if row is None else bool(row.enabled)

    async def set_state(self, strategy: str, *, enabled: bool, reason: str | None = None) -> None:
        from rtrade.core.timeutil import utcnow

        row = await self._session.get(StrategyState, strategy)
        if row is None:
            self._session.add(
                StrategyState(
                    strategy=strategy,
                    enabled=enabled,
                    disabled_reason=reason,
                    updated_at=utcnow(),
                )
            )
        else:
            row.enabled = enabled
            row.disabled_reason = reason
            row.updated_at = utcnow()
