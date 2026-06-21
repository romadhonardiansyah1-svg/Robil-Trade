# Task 6.4 — ML gating-metric fixes (G1 + G2)

Branch: `fix/audit-remediation` · TDD · one commit · modules kept DORMANT.

## Summary

Two dormant ML modules had defective gating metrics. Both are now corrected,
backed by tests written RED-first. Neither module's public contract changed;
`meta_label.py` stays unwired from `scan.py` (the OOS-expectancy promotion gate
per ADR-A08 has NOT been re-run/passed — that is a separate decision).

---

## G1 — `src/rtrade/ml/meta_label.py`

### Bug 1: `outcome_r` dropped → expectancy gate always 0.0
`prepare_labels` derived `label` from `outcome_r` but never carried `outcome_r`
into the returned DataFrame. In `train()`, `df.get("outcome_r")` was therefore
`None`, so `expectancy_unfiltered/filtered/improvement_pct` were all `0.0` — the
ADR-A08 promotion gate was meaningless.

**Fix:** `prepare_labels` now sets `row["outcome_r"] = outcome_r` so the column
exists alongside `label`.

**Confirmed NOT a feature:** `outcome_r` is the target's magnitude; including it
would leak the label. It is deliberately kept OUT of `FEATURE_COLUMNS`.
`FEATURE_COLUMNS` is unchanged (14 columns, verified by
`test_outcome_r_is_not_a_training_feature`).

### Bug 2: expectancy computed IN-SAMPLE (look-ahead)
The old code computed filtered expectancy from `final_model.predict_proba(X)` —
the model refit on ALL rows — so the filtered expectancy was optimistic and
looked ahead.

**Fix (OOS design):** During the existing `TimeSeriesSplit` CV loop, each fold's
per-test-row predicted probabilities and the aligned `outcome_r[test_idx]` are
collected into `oos_proba` / `oos_outcome_r`. After the loop:
- `expectancy_unfiltered` = mean(all out-of-fold `outcome_r`)
- `expectancy_filtered`   = mean(out-of-fold `outcome_r` where OOS proba ≥ threshold)
- `improvement_pct` derived from those two.

Empty-mask and zero-division are guarded (`mask.any()`, `abs(unfiltered) > 0.001`,
empty `oos_r` → `0.0`). The refit-on-all model is still kept as `self._model` for
deployment/inference only — it no longer feeds the reported expectancy. Docstrings
on `MetaLabelEvaluation`, `prepare_labels`, and `train` now state expectancy is OOS.

---

## G2 — `src/rtrade/ml/similar.py`

### Bug: hour-of-day used as a linear Euclidean feature
`hour` was normalized linearly (`hour/23.0`), so hour 23 and hour 0 (adjacent in
time) were treated as MAXIMALLY distant — k-NN similarity was wrong across the
midnight boundary.

**Fix (cyclic encoding):** `hour` is removed from `_FEATURE_RANGES` and encoded as
two components `(sin(2π·hour/24), cos(2π·hour/24))` via `_cyclic_hour`. A shared
`_feature_vector` helper range-normalizes the other five features and appends the
two cyclic components, applied to BOTH `curr_vec` and every history `h_vec`. The
cyclic components are already in [-1, 1] so they need no range normalization.

`k`, the `<30`-history guard, and the return shape are unchanged. Hour is read the
same way the code already read it: `current["hour"]` (top-level) and history
`confluence_breakdown["hour"]` — matching the existing `scan.py` caller
(`candidate.bar_ts.hour` top-level), so the pre-existing wiring is unaffected.

---

## Tests

RED-first, all in existing files.

New tests:
- `test_outcome_r_carried_into_dataframe` (G1a)
- `test_outcome_r_is_not_a_training_feature` (G1a guard)
- `TestMetaLabelerOOSExpectancy::test_expectancy_unfiltered_is_oos_mean_not_in_sample`
  (G1b) — builds a deterministic 60-row set where later (OOS) rows have larger R,
  reconstructs the same `TimeSeriesSplit` test indices, and asserts
  `expectancy_unfiltered == round(mean(OOS outcome_r), 4)`, `!= 0.0`, and
  `!= round(full-dataset mean, 4)`.
- `test_cyclic_hour_wraps_midnight_boundary` (G2) — current at hour 23 vs 15
  hour-0 winners and 15 hour-12 losers; asserts `win_rate == 1.0`, `avg_r == 2.0`
  (linear encoding selected the hour-12 losers → `win_rate 0.0`).

### RED (pre-fix)
- G1a carry: `KeyError: 'outcome_r'`
- G1b OOS: `KeyError: 'outcome_r'`
- G2: `assert 0.0 == 1.0`
- (G1a not-a-feature guard passed pre-fix, as expected.)

### GREEN (post-fix)
- `tests/unit/test_meta_label.py` + `tests/unit/test_similar_setups.py`: all pass.
- Full suite `.venv\Scripts\pytest.exe -q`: all pass (7 skipped, 0 failed).
- `ruff check src tests`: All checks passed.
- `mypy src` (strict): Success, no issues in 129 source files.
  (One strict `[type-arg]` finding on the new `_feature_vector` return type was
  fixed by annotating it `npt.NDArray[np.float64]`.)

### Existing tests changed
None. No existing test expectation changed — all prior assertions still hold
because the public contracts (FEATURE_COLUMNS, `find_similar_setups` signature and
return shape) were preserved.

---

## Concerns
- `similar.py` is already wired into `scan.py` (LLM branch, behind
  `cfg.settings.llm.enabled`); only `meta_label.py` is the gated/dormant module.
  G2 preserved the contract so that path is unaffected, but the cyclic change does
  shift neighbour selection near the midnight boundary — intended and correct.
- The OOS expectancy fix only changes *reported* gate metrics; promoting
  meta-labeling still requires passing the ADR-A08 backtest gate, which is out of
  scope here.
