# Task 2.5 — tz-aware regime timestamp + loud naive news-event warning (B5, B7)

Branch: `fix/audit-remediation` · Python 3.12 · Windows/PowerShell
Tests: `.venv\Scripts\pytest.exe -q` · Lint: `ruff check src tests` · Types: `mypy src` (strict)

## `ensure_utc` behavior (canonical normalizer)

`src/rtrade/core/timeutil.py`:

```python
def ensure_utc(ts: datetime) -> datetime:
    """Return `ts` converted to UTC; reject naive datetimes loudly."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise DataValidationError(
            "naive datetime rejected: all timestamps must be timezone-aware (UTC)"
        )
    return ts.astimezone(UTC)
```

Key fact that shaped both fixes: **`ensure_utc` RAISES `DataValidationError` on naive
input** — it does NOT assume/stamp UTC. For aware input it converts to UTC via
`astimezone(UTC)`. So any code path that may legitimately receive a naive datetime
cannot simply call `ensure_utc`; it must explicitly stamp UTC first (and document why).

## B7 — `regime/rules.py`: `since` could be NAIVE

Before:

```python
ts = now or pd.Timestamp(df.index[-1]).to_pydatetime()
```

Two problems: (1) when `now` is None and `df.index` is tz-naive, `to_pydatetime()`
returns a NAIVE datetime that flows into `RegimeState.since` (golden-rule violation);
(2) `now or ...` is a fragile truthiness test.

Fix — explicit None-check, and a helper that normalizes the fresh index timestamp.
Because `ensure_utc` rejects naive input, the naive-index case (legitimate for backtest
frames) explicitly assumes UTC; the aware case routes through `ensure_utc`:

```python
ts = (
    ensure_utc(now)
    if now is not None
    else self._fresh_index_ts(df.index[-1])
)
```

```python
@staticmethod
def _fresh_index_ts(index_value: object) -> datetime:
    """Normalize the latest df-index value to a tz-aware UTC datetime.

    `ensure_utc` deliberately REJECTS naive datetimes, but a tz-naive index
    is a legitimate input here (e.g. backtest frames). For that case we
    explicitly assume UTC — the canonical timezone for all candle data — so
    `RegimeState.since` never escapes as a naive datetime (golden-rule UTC).
    A tz-aware index is converted to UTC via the canonical normalizer.
    """
    dt: datetime = pd.Timestamp(index_value).to_pydatetime()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return ensure_utc(dt)
```

The `since` carried from a previous state is already tz-aware, so only the fresh
`ts` path needed the fix (unchanged: `since = prev.since if ... else ts`).

## B5 — `risk/news_filter.py`: silent naive→UTC assumption + duplicate parse

Two parsing spots existed: `check_news_blackout` inlined the same str/datetime parse
that `_parse_event_time` already implemented. Both silently stamped naive times as UTC.

Fix:
- **Centralize**: `check_news_blackout` now routes through `_parse_event_time(raw, event_name=...)`,
  removing the duplicate parse block. `high_impact_within` already used the helper.
- **Make the assumption LOUD**: `_parse_event_time` emits a `logger.warning` when an
  event_time is parsed as NAIVE (for both the `str` and `datetime` branches), then keeps
  the UTC assumption. DB events arrive tz-aware (`timestamptz`) and never trigger the
  warning; only the naive provider fallback does.

```python
if parsed.tzinfo is None:
    logger.warning(
        "news event_time is naive — assuming UTC (verify provider timezone)",
        event_name=event_name,
        raw=raw,
    )
    parsed = parsed.replace(tzinfo=UTC)
return ensure_utc(parsed)
```

Note: the structlog key is `event_name` (not `event`) because `event` is reserved by
structlog for the log message itself — passing `event=` raised
`TypeError: got multiple values for argument 'event'`.

Behavior preserved: blackout window math and the always-high keyword logic are unchanged;
a naive high-impact event in-window still blocks (now with a visible warning).

### Why full provider-tz verification is deferred (B1)

Properly verifying each provider's source timezone (a provider could emit naive LOCAL
times, which would shift the blackout by hours) is provider-hardening work tracked under
finding **B1**. The focused, correct improvement here is visibility + centralization:
surface the naive fallback in logs so a mis-tz'd provider is caught, without changing the
UTC assumption that is correct for the production DB path.

## RED → GREEN

RED (before implementation) — 4 new tests failed as expected:
- `test_since_is_tz_aware_with_naive_index` → `AssertionError: assert None is not None`
  (`since` was naive `datetime(2026, 1, 5, 3, 0)`).
- `test_naive_event_time_string_warns_and_returns_utc` → `TypeError: unexpected keyword 'event_name'`.
- `test_aware_event_time_string_does_not_warn` → `TypeError: unexpected keyword 'event_name'`.
- `test_naive_high_impact_event_still_blocks_in_window` → `assert False` (no warning emitted).
- (`test_since_uses_aware_now_in_utc` passed already — confirms aware-now behavior preserved.)

GREEN (after implementation):
- `pytest -q tests/unit/test_regime.py tests/unit/test_risk.py` → **35 passed**.
- Full suite `pytest -q` → **all passed** (7 skipped, 0 failed).
- `ruff check src tests` → **All checks passed!**
- `mypy src` (strict) → **Success: no issues found in 129 source files**
  (fixed one `[no-any-return]` from pandas `to_pydatetime()` returning `Any` via an
  explicit `dt: datetime` annotation).

## Commit

`fix(safety): tz-aware regime timestamp + loud naive news-event warning (B5,B7)`
Commit hash: <filled in below>

## Concerns / follow-ups

- **B1 (provider hardening)**: the naive-time warning is visibility only; the real fix is
  verifying/normalizing each provider's source tz at ingestion. Tracked under B1.
- `_parse_event_time` warns once per naive event per call; a chronically mis-tz'd provider
  will produce repeated warnings — acceptable as a loud signal, but a future rate-limit or
  ingest-time dedupe may be warranted.
- B7 naive-index UTC assumption is correct for our epoch-aligned UTC candle data; if a
  backtest ever feeds a non-UTC naive index, the assumption would be wrong — same class of
  risk as B1, mitigated by the codebase-wide tz-aware-everywhere convention.
