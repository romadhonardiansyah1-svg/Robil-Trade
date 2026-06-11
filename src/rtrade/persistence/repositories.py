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

from sqlalchemy import select
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
        self._session.add(SignalAudit(signal_id=signal_id, stage=stage, ok=ok, detail=detail))
