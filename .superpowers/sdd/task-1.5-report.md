# Task 1.5 — PBO as model-selection diagnostic (A5, Option A) + permutation p-value correction (A11)

Branch: `fix/audit-remediation` · Python 3.12 · Windows/PowerShell
Tests: `.venv\Scripts\pytest.exe -q` · Lint/types: `ruff check src tests` + `mypy src` (strict)

## Summary

Two defects in the backtest validation layer, both safety-critical for the go-live gate:

- **A5** — `run_validation_gates` rubber-stamped a PBO it never computed (a single-config
  run silently "passed" PBO via `pbo_val = pbo_value if pbo_value is not None else 0.0`,
  and `0.0 <= 0.30` is always True). PBO is a *model-selection* diagnostic, so on a
  single configuration there is nothing to select among and nothing to measure.
- **A11** — the sign-flip permutation p-value `count_ge / n_permutations` could be exactly
  `0`, which is statistically invalid for a finite Monte-Carlo null.

---

## DEFECT A5 — Option A reframing (chosen resolution)

PBO (CSCV, Bailey et al. 2017) measures the overfitting introduced by **SELECTING** the
best-looking configuration among **many** candidate configurations (a parameter sweep). It
is meaningful only over an `(T, N>=2)` returns matrix. A single-config go-live run has
`N = 1` — there is no selection step, hence no PBO to compute.

### Why Option A removes the rubber stamp without blocking go-live

Previously the gate was *always* added with a defaulted `0.0`, so the report and
`gate_results` showed a green `"pbo <= 0.30"` for a value that was never measured — a false
assurance against overfitting. Option A makes the PBO gate **optional**, exactly mirroring
the existing `permutation_p` pattern:

- `run_validation_gates` now adds `"pbo <= 0.30"` **only when `pbo_value is not None`**.
- When `pbo_value is None` the gate is omitted entirely and does **not** count toward
  `all_passed`. A single-config run is therefore neither falsely certified nor blocked —
  it simply doesn't claim a PBO result it doesn't have.
- The parameter-sweep / model-selection step still supplies a real `pbo_value`, and the
  gate then behaves as a genuine pass/fail check.

`ValidationGateResult.pbo` changed from `float` to `float | None`: it is set to the
supplied `pbo_value` (or `None` when not evaluated) instead of a fabricated `0.0`.

### Fail-closed CSCV change

`probability_of_backtest_overfitting` previously failed **open** on degenerate input:

- `t < s_partitions or n < 2` → `return 0.0` (looked perfectly clean) → now `return 1.0`.
- final `overfit_count / len(combos) if combos else 0.0` → now `... else 1.0`.

A degenerate / insufficient PBO must be treated as **fully overfit (1.0)**, so that if
anyone ever wires this function's output into a `<= 0.30` gate, an uncomputable result
fails closed rather than silently passing.

### Documentation

Module docstring and the `run_validation_gates` / `probability_of_backtest_overfitting`
docstrings now state that PBO is a model-selection diagnostic computed in the sweep step
and supplied via `pbo_value`; the single-config go-live gate does not evaluate PBO; and the
CSCV implementation fails closed on degenerate data. `probability_of_backtest_overfitting`
remains the correct CSCV implementation for the sweep step.

---

## DEFECT A11 — permutation p-value correction

`src/rtrade/backtest/permutation.py`: applied the standard small-sample correction
(Davison & Hinkley 1997; North et al. 2002):

```
return (count_ge + 1) / (n_permutations + 1)   # was: count_ge / n_permutations
```

Including the observed statistic in the permutation null guarantees a strictly positive
p-value; an exact `0` is invalid (a finite MC sample cannot establish zero probability).
Docstring updated (return is now `(0, 1]`, with a note on the correction).

---

## Callers / tests touched and why

- `src/rtrade/backtest/validation.py` — `ValidationGateResult.pbo: float | None`; PBO gate
  made optional; fail-closed returns; docstrings.
- `src/rtrade/backtest/permutation.py` — p-value correction + docstring.
- `src/rtrade/cli/backtest.py` — `_print_report` now prints
  `PBO:           n/a (not evaluated — model-selection diagnostic)` when `vgr.pbo is None`,
  else the numeric value. The DB metrics dict stores `vgr.pbo`, which is now `None` →
  serialized as JSON `null` when not evaluated (no separate change needed; flows through
  from the `float | None` type). mypy strict stays clean.
- `tests/backtest/test_validation.py` — added: PBO gate absent + `pbo is None` when
  `pbo_value=None`; gate present & False at `pbo_value=0.5`; gate present & True at
  `pbo_value=0.1`; CSCV fail-closed → `1.0` on shape `(4,1)` (T<S) and `(20,1)` (N<2).
- `tests/unit/test_permutation.py` — added: perfect-edge series yields `1/(n+1)`, never `0`.
- **No existing test required modification.** `test_evaluate_passes_strong_metrics`
  (cli, single-config, `pbo_value` never passed) still asserts `all_passed is True`:
  removing the always-true `0.0 <= 0.30` gate does not change the boolean AND of the
  remaining gates, confirming `all_passed` derives correctly from the remaining gates.
  The existing permutation tests still hold (1000-perm cases shift by `1/1001`, well
  within their `< 0.01` / `> 0.2` margins; the `< 5 trades` path still returns `1.0`).

---

## Evidence

### RED (against original code)

```
FAILED tests/backtest/test_validation.py::test_pbo_gate_absent_when_not_evaluated
FAILED tests/backtest/test_validation.py::test_pbo_insufficient_rows_fails_closed   (assert 0.0 == 1.0)
FAILED tests/backtest/test_validation.py::test_pbo_single_config_fails_closed       (assert 0.0 == 1.0)
FAILED tests/unit/test_permutation.py::test_pvalue_never_exactly_zero               (assert 0.0 == 1/1001)
```
(The `pbo_value=0.5` / `0.1` "gate present" tests passed pre-fix because the gate was
always added; they remain green and now guard the optional-gate behavior.)

### GREEN (targeted)

```
.venv\Scripts\pytest.exe -q tests/backtest tests/unit/test_permutation_gate.py \
    tests/unit/test_promote_gate.py tests/unit/test_permutation.py
-> all passed (exit 0)
```

### Full suite

```
.venv\Scripts\pytest.exe -q
-> 805 passed, 7 skipped, 1 warning in ~52s (exit 0)
```

### Lint / types

```
.venv\Scripts\ruff.exe check src tests   -> All checks passed!
.venv\Scripts\mypy.exe src               -> Success: no issues found in 129 source files
```

---

## Concerns

- The unrelated `StarletteDeprecationWarning` (FastAPI test client) persists; out of scope.
- No production parameter-sweep caller yet supplies `pbo_value`; PBO remains uncovered for
  go-live until the model-selection step is wired to pass a real CSCV value. This is by
  design (Option A) but should be tracked so PBO is actually evaluated when sweeps land.
- A persisted `pbo` of JSON `null` is a schema change for downstream `backtest_runs`
  consumers; verify any analytics that read `metrics.pbo` tolerate null.
