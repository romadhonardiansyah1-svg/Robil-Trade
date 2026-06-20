# Task 3.2 — D2: UTC candle alignment for OANDA H4/D1

## Defect (D2): non-UTC candle alignment

`OandaProvider.fetch_ohlcv` built its `params` dict with `granularity`, `price`,
`from`, and `count` only — it sent **no** `alignmentTimezone` or `dailyAlignment`.

Per the OANDA v20 candles API, when these are omitted the server falls back to its
default **daily alignment of 17:00 America/New_York**. For `D` (D1) and `H4`
candles this means bar-open timestamps land on the NY-17:00 grid (which also
shifts by an hour across US DST transitions), **not** the UTC-day grid that
`rtrade.core.timeutil` and the rest of the system assume.

### Three downstream impacts

1. **Anti-look-ahead "last closed bar" cutoff.** The cutoff computes the most
   recent fully-closed bar against a UTC boundary. NY-17:00-aligned bar-opens
   make the system mis-identify which bar is closed — either admitting a
   still-forming bar (look-ahead leak) or discarding a valid closed bar.
2. **DST-aware gap detection.** Expected bar spacing is derived from UTC. A
   17:00-NY anchor drifts by one hour twice a year at US DST changes, so the gap
   detector sees phantom gaps / mis-sized bars around those transitions.
3. **Cross-provider MTF alignment (XAU/FX).** D1/H4 bars from OANDA would not
   share a common epoch with bars from other providers (e.g. TwelveData), so
   multi-timeframe stacks for XAU_USD / FX fail to line up bar-for-bar.

## Fix — params added

Added two keys to the candles request `params`:

```python
"alignmentTimezone": "UTC",
"dailyAlignment": 0,
```

### Conditional vs unconditional decision

Sent **unconditionally** for every timeframe (not gated to D/H4). Rationale:

- M1/M5/M15/H1 already align to the minute/hour grid, and `dailyAlignment` only
  affects the daily boundary, so these params are a **no-op** for sub-daily
  granularities — correct and harmless.
- Unconditional is the simplest, most consistent code path (no branching on
  timeframe) and avoids a future regression if a new timeframe is added to
  `_TF_MAP`.

No changes to parsing or rate-limiting.

## TDD evidence

### RED
Added a parametrized respx test
`test_fetch_ohlcv_sends_utc_alignment_params[H4, D1]` that mocks the candles
endpoint, calls `fetch_ohlcv`, and inspects the captured request via
`route.calls.last.request.url.params`, asserting:

- `alignmentTimezone == "UTC"`
- `dailyAlignment == "0"`

Before the fix both cases failed with `KeyError: 'alignmentTimezone'` (query was
`granularity=H4&price=M&from=...&count=500` — params absent). Confirmed FAIL.

### GREEN
After adding the two params, the new test passes for both H4 and D1.

### Suites + lint + types
- `pytest -q tests/unit/test_oanda_provider.py` → 8 passed
- `pytest -q tests/unit` → all passed
- `pytest -q` (full) → all passed, 7 skipped (live OANDA integration tests,
  no creds in env)
- `ruff check src tests` → All checks passed!
- `mypy src` (strict) → Success: no issues found in 129 source files

## Concerns

- **Existing respx matchers did not need updating.** The existing tests match on
  path only (`mock.get("/v3/instruments/XAU_USD/candles")`), not on query
  params, so adding params did not break them. If any future test pins an exact
  query string it would need the two new keys.
- `dailyAlignment` is sent as the integer `0`; httpx serializes it to the string
  `"0"` in the query, which is what the assertion checks and what OANDA expects.
- Only the candles request was touched; `_pricing`/quote/spread paths are
  unchanged.
