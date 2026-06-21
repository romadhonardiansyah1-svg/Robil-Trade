# Task 4.3 — Reliability remediation (E3, E4, E5)

Branch: `fix/audit-remediation`
Persona: Senior Reliability Engineer. TDD (RED → GREEN), tz-aware UTC, structlog,
ruff + mypy (strict) clean, single commit.

Three independent defects across three files. Each file and its tests were read
first; RED tests were written and confirmed failing before any production code.

---

## E3 — Telegram alerts dropped on Markdown-special content
File: `src/rtrade/monitoring/alerts.py`

### Problem
`_send_telegram` sent `parse_mode="Markdown"` while `_format_alert` and the
`alert_*` helpers interpolate arbitrary dynamic content (error strings in
backticks, provider names, instrument names, titles, detail keys/values). Any
unbalanced Markdown reserved character (`_ * [ ] ( ) \` …`) makes Telegram
return HTTP 400, and the alert is silently dropped — exactly when something is
already going wrong.

### Fix — choice: plain text (`parse_mode=None`)
Picked **option (b): plain text** over MarkdownV2 escaping. Rationale (fail-safe
for a reliability path): with `parse_mode=None` *no* dynamic content can ever
break parsing, so there is no escaping surface to get wrong as messages evolve.
MarkdownV2 would require escaping every reserved char in every dynamic substring
*and* in static template parts (the timestamp's `-`, decimals' `.`, etc.) — more
code and more ways to regress.

Changes:
- `_send_telegram` payload now sends `"parse_mode": None`.
- `_format_alert` no longer emits `*…*` bold markers (they would render as
  literal asterisks in plain text); structure preserved via line layout, the
  `level` emoji prefix, and `ROBIL TRADE ALERT` header so it still reads well.
- Removed now-pointless backticks around dynamic values in `alert_provider_down`,
  `alert_scan_failed`, `alert_service_unhealthy` (they would show as literal
  backticks in plain text).
- Dedup/cooldown logic and the `-> bool` send contract are unchanged.

### Tests (`tests/unit/test_alerts.py`)
- `test_send_telegram_payload_disables_markdown_parsing` — mocks
  `httpx.AsyncClient`, formats an alert whose title/message/details contain
  `_ * [ ] ( \``, sends it, asserts `payload["parse_mode"] is None` and the
  call returns `True`.
- `test_format_alert_does_not_emit_literal_markdown_markers` — asserts no `*`
  in the rendered text while header/title/message are still present.

---

## E4 — backfill pagination hardcoded H1/H4 cursor step
File: `src/rtrade/cli/backfill.py`

### Problem
`td = timedelta(hours=1) if tf == Timeframe.H1 else timedelta(hours=4)` then
`batch_since = batch_since + td * 499`. For D1 the cursor advanced by only
4h×499 (re-fetching overlapping windows / wasting rate budget); for M5/M15 it
advanced by 4h×499 (skipping data).

### Fix — tf-aware pure helper using `timeframe_duration`
Extracted a small unit-testable helper:

```python
_BATCH_SIZE = 499

def _advance_cursor(since: datetime, tf: Timeframe, batch: int = _BATCH_SIZE) -> datetime:
    return since + timeframe_duration(tf) * batch
```

`timeframe_duration(tf)` from `src/rtrade/core/timeutil.py` gives the correct
per-timeframe step (M5→5min, …, D1→1day). The loop now calls
`batch_since = _advance_cursor(batch_since, tf)`. `batch=499` matches the
existing page constant (no overlap, no gaps).

### Tests (`tests/unit/test_backfill.py`, new file)
- Parametrized over M5/M15/H1/H4/D1: `_advance_cursor(since, tf)` equals
  `since + timeframe_duration(tf) * 499`.
- D1 advances exactly 499 days; M5 advances 499×5min; H4 advances 499×4h.
- `batch=500` argument honored.

---

## E5 — composite_market.fetch_spread aborted on one leg's error
File: `src/rtrade/data/composite_market.py`

### Problem
`fetch_spread` called `provider.fetch_spread(symbol)` with no guard, so an
unexpected error in one leg aborted the whole spread lookup instead of failing
over to the next leg.

### Fix — per-leg try/except failover
Each leg is now wrapped in `try/except Exception`; on a leg error a warning is
logged (`leg`, `op="fetch_spread"`, `error`) and the loop continues to the next
leg. The first leg that returns a non-`None` spread wins; `None` is returned
only after all legs fail or all return `None`. Return type `float | None` and
the success-path ordering are unchanged. (`fetch_ohlcv`/`fetch_quote` already
failed over via `_attempt`; only `fetch_spread` was missing the guard.)

### Tests (`tests/unit/test_composite_market.py`)
- `test_fetch_spread_fails_over_to_next_leg_on_error` — leg A raises
  `RuntimeError`, leg B returns `0.42` → result `0.42`, both legs called.
- `test_fetch_spread_returns_none_when_all_legs_fail` — A raises ProviderError,
  B raises RuntimeError → `None`.
- `test_fetch_spread_first_successful_leg_wins` — A=0.10, B=0.20 → `0.10`,
  B never called (ordering preserved).

---

## Verification

RED (before implementation):
- `test_backfill.py` — ImportError: `_advance_cursor` does not exist (collection error).
- `test_alerts.py` — `assert 'Markdown' is None` failed; `'*' in text` failed.
- `test_composite_market.py` — spread failover + all-fail tests failed (error propagated);
  ordering test already passed (success path unchanged).

GREEN (after implementation):
- 3 modules: `pytest -q tests/unit/test_backfill.py tests/unit/test_composite_market.py tests/unit/test_alerts.py` → all pass.
- Full suite: `.venv\Scripts\pytest.exe -q` → **874 passed, 8 skipped, 1 warning** (~66s).
  (8 skips and the Starlette/httpx deprecation warning are pre-existing, unrelated.)
- `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- `.venv\Scripts\mypy.exe src` → Success: no issues found in 129 source files.

## Commit
`fix(reliability): robust alert formatting + tf-aware backfill pagination + per-leg spread failover (E3,E4,E5)`
Commit hash: c548aa946f1a86f506be29f554a417f7acda0172 (implementation commit;
the report is part of this commit so its own final hash is reported back to the
orchestrator separately)

## Concerns / notes
- E3: plain text means messages no longer render bold/monospace in Telegram.
  This is intentional (robustness over styling). If styled alerts are later
  required, switch to MarkdownV2 with a tested escaper for the full reserved set
  applied to every dynamic substring.
- E4: `_advance_cursor` assumes contiguous, non-overlapping pages of `batch`
  candles. If a provider returns fewer than 499 on a non-final page (e.g. market
  holidays/gaps), the cursor could over-step; the existing `count == 0` break
  bounds total work, and ingestion is idempotent on candle open-time, so this is
  not a correctness risk for the signal path — just possible extra empty pages.
- E5: `except Exception` is deliberately broad to satisfy "unexpected error in
  one leg" failover; genuinely fatal conditions are not re-raised here, matching
  the fail-over-then-None contract of the method.
