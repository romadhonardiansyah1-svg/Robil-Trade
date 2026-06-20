# Task 2.3 — Risk Sizing Remediation (B3 + B4)

**File:** `src/rtrade/risk/sizing.py`
**Tests:** `tests/unit/test_risk.py`
**Branch:** `fix/audit-remediation`
**Methodology:** TDD (RED → GREEN), one commit.

---

## Defects fixed

### B3 — min-lot rounding could EXCEED the risk cap and reported the wrong risk
`compute_position_size` floored the size to the lot step:
`position_size = math.floor(position_size / lot_step) * lot_step`.
When this floored to `<= 0` the old code did `position_size = lot_step if lot_step else pip_size`
— it **bumped up to one lot_step**. But a floored-to-0 size means even ONE lot_step risks MORE
than the `risk_amount` budget, so the bump silently breached GR-05 (2% per-trade cap). It also
still returned `risk_amount_usd = round(risk_amount, 2)` — the *intended smaller* budget —
understating the TRUE risk of the bumped-up size.

### B4 — Kelly risk was uncapped and the Kelly suggestion's USD risk was misreported
In `compute_with_kelly`, `kelly_risk = equity * kelly_f` was uncapped — quarter-Kelly can still
imply 5–15% risk, far above the 2% GR-05 cap. The returned `risk_amount_usd` reflected only the
base (fixed-pct) size, so the Kelly suggestion's true USD risk was never reported anywhere.

---

## Decisions / fixes

### Abstain-vs-bump (B3)
When lot-step rounding floors the size to `<= 0`, the function now **ABSTAINS** rather than bumping
up. It returns `SizingResult(position_size=0.0, risk_amount_usd=0.0, method="abstain_min_lot",
kelly_size=None, kelly_fraction=None, kelly_risk_usd=None)`. Rationale: bumping to one lot_step
would risk more than the budget — a silent GR-05 breach. We'd rather take no position than over-risk.

### True-risk reporting (B3)
For a valid (rounded-down) size, `risk_amount_usd` is now `round(position_size * sl_distance, 2)`.
Because rounding is always downward, this value is always `<=` the budget and accurately reflects
what is actually at risk — it is NOT the pre-rounding target. (The no-`lot_step` path also reports
true risk of the exact size.)

### Kelly 2% clamp (B4)
`kelly_risk = min(equity * kelly_f, equity * 0.02)` — clamps the Kelly-implied risk to the GR-05
2% cap. Documented inline. The Kelly size derives from the clamped risk, so the advisory can never
imply more than 2% of equity at risk.

### Kelly true-risk reporting + new field (B4)
Added `kelly_risk_usd: float | None = None` to `SizingResult`, set to
`round(kelly_size * sl_distance, 2)` for the lot-rounded Kelly size. Kelly stays a
secondary/advisory suggestion: primary `position_size` / `risk_amount_usd` remain the base
fixed-pct size. If the lot-rounded `kelly_size` floors to 0, we drop the advisory (return the base
result, so `kelly_size` and `kelly_risk_usd` are both None).

### Guards preserved
The `risk_pct > 2.0` GR-05 guard and the positive-inputs guard (`equity`, `risk_pct`, `sl_distance`)
are unchanged.

---

## `SizingResult` constructions / callers updated
- **New field:** `kelly_risk_usd: float | None = None` (defaulted so existing positional/keyword
  construction stays valid; all paths set it explicitly).
- `compute_position_size` — `fixed_pct` path → `kelly_risk_usd=None`.
- `compute_position_size` — new `abstain_min_lot` path → `kelly_risk_usd=None`.
- `compute_with_kelly` — `fixed_pct_with_kelly` path → `kelly_risk_usd=round(kelly_size * sl_distance, 2)`.
- `compute_with_kelly` — no-edge / floored-Kelly paths → return `base` (so `kelly_risk_usd=None`).

**Caller scan:** GREP for `compute_position_size` / `compute_with_kelly` / `SizingResult` /
`kelly_size` / `risk_amount_usd` across `src` and `tests`. The only consumer of these symbols /
`SizingResult` fields is `tests/unit/test_risk.py`. The scan pipeline (`src/rtrade/signals/engine.py`)
computes its own inline sizing and does **not** import `SizingResult` or these functions, and
`src/rtrade/risk/kelly.py` is an independent Bayesian helper. No production callers needed changes.

---

## Tests (TDD)

### RED (added, confirmed failing first)
- `test_min_lot_floor_abstains_never_over_risks` — equity=100, risk_pct=1.0, sl_distance=1000,
  lot_step=0.01 → floored size 0 → expects `position_size==0.0`, `risk_amount_usd==0.0`,
  `method=="abstain_min_lot"`. (Old code bumped to 0.01 and over-risked.)
- `test_reports_true_risk_after_lot_rounding` — equity=10_000, risk_pct=1.0, sl=7.0, lot_step=0.01 →
  size 14.28, expects `risk_amount_usd == round(14.28*7.0,2) == 99.96 <= $100 budget`.
- `test_kelly_risk_clamped_to_gr05_cap` — win_rate=0.9, avg_win=3.0, avg_loss=1.0 (kelly_f≈0.217)
  → `kelly_risk_usd <= equity*0.02`.
- `test_kelly_risk_usd_reports_kelly_size_not_base` — `kelly_risk_usd == round(kelly_size*sl,2)`
  and `!= risk_amount_usd` (base $100).
- `test_kelly_risk_usd_none_without_edge` — no-edge inputs → `kelly_fraction/kelly_size/kelly_risk_usd`
  all None.

RED run: all 5 failed for the expected reasons (`AttributeError: 'SizingResult' object has no
attribute 'kelly_risk_usd'` for B4; assertion failures for B3 abstain + true-risk).

### GREEN
- `tests/unit/test_risk.py`: 22 passed.
- Full suite: `.venv\Scripts\pytest.exe -q` → all passed (7 skipped, 0 failed); 1 unrelated
  Starlette deprecation warning.

### Quality gates
- `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- `.venv\Scripts\mypy.exe src` (strict) → Success: no issues found in 129 source files.

---

## Commit
`fix(risk): abstain on min-lot over-risk + clamp/report Kelly risk (B3,B4)`
Hash: __COMMIT_HASH__

---

## Concerns
- `SizingResult.kelly_risk_usd` is defaulted to `None` to keep the dataclass backward-compatible;
  all current construction sites set it explicitly, so the default only protects external callers.
- The Kelly advisory floors to "no advisory" when the lot-rounded size is 0 (consistent with the
  B3 abstain philosophy: no over-/spurious-risk). If a future consumer wants a min-lot Kelly
  advisory, it must explicitly accept the resulting risk.
- The scan pipeline (`engine.py`) still does its own inline sizing and does not route through
  `compute_position_size`; B3's abstain/true-risk protections do not cover that path. Out of scope
  here but worth a follow-up audit item if the pipeline should share the hardened sizing logic.
