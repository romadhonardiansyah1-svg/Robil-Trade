# Task 2.6 — Defect B6: Guardrails fail OPEN on missing inputs

Branch: `fix/audit-remediation` · Python 3.12 · Windows/PowerShell
Commit message: `fix(safety): guardrails fail closed on missing required inputs (B6)`

## 1. The fail-OPEN mechanism (what was broken)

`run_gate` in `src/rtrade/guardrails/gate.py` only evaluates several gates when
their backing input is actually passed in. Each of these is wrapped in
`if x is not None`:

- GR-06 freshness — `if latest_candle_ts is not None`
- GR-07 news blackout — `if events is not None and related_currencies is not None`
- GR-08 regime — `if regime is not None`
- GR-09 confidence floor — `if confidence is not None`
- GR-11 citations — `if sources is not None`
- GR-13 expectancy guard — `if paper_outcomes is not None`
- (GR-12 rate cap defaults `signals_today=0`, so it always runs.)

Consequence: a caller that simply OMITS an input silently DISABLES that safety
gate — no failure, no audit trail. This is fail-OPEN, the worst failure mode for
a trading safety gate. The startup self-test only exercised the inputs-PRESENT
path, so it could never catch a caller dropping an input.

## 2. The `require`-set design (fix)

Added a parameter `require: set[str] | None = None` to `run_gate`. It is the
CALLER's explicit declaration of which gate IDs MUST be evaluated. Before the
normal gate logic runs, for every id in `require` whose backing input is
missing/None, run_gate appends:

```
GateFailure(gate_id=<id>, reason="required input missing — fail closed")
```

This turns omission into a REJECTION with a full audit trail (the failure flows
through the existing audit + persisted-signal paths). When `require is None`
(the default) behaviour is byte-for-byte unchanged, so existing tests, the
no-LLM/dormant path, and crypto optionality all keep working.

### Requirable gate → backing input mapping (documented in the docstring)

| Gate  | Backing input(s)                       | "missing" condition                              |
|-------|----------------------------------------|--------------------------------------------------|
| GR-06 | `latest_candle_ts` (freshness)         | `latest_candle_ts is None`                       |
| GR-07 | `events` AND `related_currencies`      | either is `None`                                 |
| GR-08 | `regime`                               | `regime is None`                                 |
| GR-09 | `confidence`                           | `confidence is None`                             |
| GR-11 | `sources`                              | `sources is None`                                |
| GR-13 | `paper_outcomes`                       | `paper_outcomes is None`                         |

Note on GR-06 / live quote: the missing-live-quote case is already enforced
independently via the `live_quote_required` flag (fail-CLOSE when `live_price is
None`), so the `require` mapping for GR-06 covers the freshness input
(`latest_candle_ts`) and the live-quote requirement remains handled by its own
existing branch. GR-10 (no-LLM-number-mutation) and GR-12 (rate cap) are not in
the requirable set: GR-12 always runs (defaulted), and GR-10 is only meaningful
when an `original_candidate` is supplied to compare against.

Implementation builds a `required_input_present` dict and iterates `sorted(require)`
for deterministic failure ordering.

## 3. scan.py wiring — exactly which gates are required, in which context, and why

`src/rtrade/pipeline/scan.py` has two `run_gate` calls inside `_run_strategies`.
A base require set is computed once from context scan.py already knows:

```python
required_gates = {"GR-06", "GR-08", "GR-13"}
if instrument.market != Market.CRYPTO:
    required_gates.add("GR-07")
```

**Deterministic (first) gate — runs for every instrument, LLM or not:**
`require=required_gates`.

- **GR-06 (freshness)** — required ALWAYS. scan.py always passes
  `latest_candle_ts=candidate.bar_ts`, which exists for every candidate.
- **GR-08 (regime)** — required ALWAYS. scan.py always passes
  `regime=regime.regime`; the regime is computed before strategies run.
- **GR-13 (expectancy)** — required ALWAYS. `paper_outcomes` comes from
  `session_repo.recent_outcomes(...)` which returns `list[float]` (never None;
  empty is fine and does not trip the guard).
- **GR-07 (news blackout)** — required for NON-crypto only. scan.py supplies
  `events=event_dicts` and `related_currencies=instrument.related_currencies`
  for all instruments, but economic-calendar coverage only applies to non-crypto
  markets (`calendar_stale` is itself gated on `instrument.market != Market.CRYPTO`).
  Per spec, GR-07 is NOT required for crypto.
- **GR-09 / GR-11** — NOT required here. The deterministic gate intentionally
  omits `confidence` and `sources` (those are P2/LLM outputs), so requiring them
  would be incorrect on this path.

**Post-LLM (second) gate — only reached when `cfg.settings.llm.enabled` AND the
LLM decision is PUBLISH:** `require=required_gates | {"GR-09", "GR-11"}`.

- Inherits GR-06/GR-08/GR-13 (+GR-07 for non-crypto) for the same reasons.
- **GR-09 (confidence floor)** — added because the LLM pipeline ran and scan.py
  passes `confidence=float(pres.confidence)`.
- **GR-11 (citations)** — added because scan.py passes
  `sources=pres.sources or ["deterministic_pipeline"]` (always non-None here).

This is conservative and correct: each required gate is only required where
scan.py genuinely supplies its input in that context. GR-07 is never required
for crypto; the LLM gates are never required on the no-LLM/dormant path.

## 4. selftest.py additions

`src/rtrade/guardrails/selftest.py` (`run_guardrail_selftest`) now, in addition
to all existing inputs-PRESENT rejection cases:

1. For each requirable gate (GR-06, 07, 08, 09, 11, 13) calls
   `run_gate(good, require={gate})` with the input OMITTED and asserts the gate
   REJECTS with a `"required input missing"` failure. This proves the
   fail-OPEN hole is closed — something the old self-test structurally could not
   detect.
2. A regression case mirroring the production require set (non-crypto + LLM:
   `{GR-06, GR-07, GR-08, GR-09, GR-11, GR-13}`) with ALL inputs present, and
   asserts it PASSES (no missing-input failures introduced).
3. The original "valid candidate passes" regression is retained.

Test additions in `tests/unit/test_guardrails.py` (`TestRequireFailClosed`):
per-gate required-but-missing rejection (GR-06/07/08/09/11/13), a
present-input-passes case, and a `require=None` backward-compat case.

## 5. Verification

- **RED**: `TestRequireFailClosed` — 7 failures with
  `TypeError: run_gate() got an unexpected keyword argument 'require'`
  (the backward-compat `require=None` case passed pre-change, as designed).
- **GREEN**: `tests/unit/test_guardrails.py` `TestRequireFailClosed` → 8 passed.
- Guardrail + selftest + scan-gate subset
  (`test_guardrails`, `test_guardrail_selftest`,
  `test_deploy_blocker_selftest_exploration`, `test_scan_post_llm_gate`) → all pass.
- **Full suite**: `.venv\Scripts\pytest.exe -q` → all passed (7 skipped), 0 failures.
- **ruff**: `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- **mypy (strict)**: `.venv\Scripts\mypy.exe src` → Success, no issues (129 files).
- The post-LLM gate spy test (`test_post_llm_gate_invoked_on_publish`) confirms
  production still publishes with all inputs present under the new require set.

## 6. Concerns / notes

- `require` is opt-in; only `scan.py` currently opts in. Any future caller on a
  production signal path should pass an explicit `require` set — the default
  preserves legacy skip-on-absent behaviour for compatibility, so a brand-new
  caller could still fail-open if it forgets `require`. Mitigation: the self-test
  documents the expected production set and the scan.py comments spell out the
  rationale.
- GR-12 stays defaulted (always runs) and is intentionally not requirable; GR-10
  is only meaningful with `original_candidate` and is likewise not requirable.
- GR-13 with an empty `paper_outcomes` list passes (expectancy guard needs a
  minimum window); requiring GR-13 only guards against the input being dropped
  entirely (None), which is the B6 fail-open case.
