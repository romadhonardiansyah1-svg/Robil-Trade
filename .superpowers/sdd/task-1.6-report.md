# Task 1.6 — Backtest cost realism (Defects A6 & A7)

Branch: `fix/audit-remediation` · Python 3.12 · TDD · one commit.

## Summary of defects

### A6 — per-lot round-turn commission loaded but never charged
`CostModel.commission_usd_per_lot_rt` was loaded from `costs.yaml` but
`compute_trade_cost` only returned `pct_cost + pip_cost`. The configured
`$7/lot` EURUSD commission was silently dropped, so every forex backtest
under-counted costs — exactly the "decision-grade backtest running cheaper than
reality" failure mode.

### A7 — unconfigured symbols backtest cost-free
Both CLIs did `load_cost_models().get(symbol)` → `None` → ran with zero costs
(`run_backtest.py` only logged a warning; `cli/backtest.py` was silent). An
operator could run a go-live gate on an unconfigured instrument and get a
cost-free, invalid result with no hard stop.

## Unit derivation — `commission_price_per_unit`

The engine sizes a trade by risk: `position_size = risk_amount / sl_dist`
(instrument units). It treats the value returned by `compute_trade_cost` as a
**price-distance per unit** cost: USD PnL multiplies `cost × position_size`, and
R is `cost / sl_dist`.

A commission quoted in **USD per standard lot** must therefore be converted to
the same price-per-unit basis so it scales with `position_size` automatically:

```
commission_price_per_unit [price units]
    = commission_usd_per_lot_rt [USD / lot]
      ────────────────────────────────────
      contract_size [units / lot]
```

Units check: `USD/lot ÷ units/lot = USD/unit`. Since one unit of price move on
one unit of position = 1 USD, `USD/unit` is dimensionally a price distance per
unit — the exact basis the engine expects. Multiplying back by
`position_size` (units) recovers USD; dividing by `sl_dist` recovers R.

EURUSD: `7.0 / 100000 = 0.00007` price units, added on top of the pip cost.

Guard: `contract_size <= 0` ⇒ commission term is `0.0` (no div-by-zero).

## Files changed

- `src/rtrade/backtest/costs.py`
  - `CostModel`: new field `contract_size: float = 100_000.0` (forex std lot).
  - `compute_trade_cost`: adds
    `commission_cost = (commission_usd_per_lot_rt / contract_size) if contract_size > 0 else 0.0`.
  - `load_cost_models`: reads `contract_size` (default `100_000.0`).
  - New `get_cost_model(symbol, *, config_path=..., allow_missing=False) -> CostModel | None`:
    raises `ConfigError` (from `rtrade.core.errors`) naming the symbol and
    `costs.yaml` when absent and not allowed; returns `None` when `allow_missing`.
- `config/costs.yaml`: added `contract_size` — EURUSD `100000`, XAUUSD `100`
  (oz/lot), BTCUSDT `1` (BTC/lot). Existing fields unchanged.
- `scripts/run_backtest.py`: new `--allow-zero-cost` flag (default False); uses
  `get_cost_model(args.instrument, allow_missing=args.allow_zero_cost)`; catches
  `ConfigError` → `logger.error` + `sys.exit(1)`; with the flag, logs a
  prominent "RUNNING COST-FREE — NOT a valid decision basis" warning.
- `src/rtrade/cli/backtest.py`: uses `get_cost_model(args.symbol)` (no opt-in);
  on `ConfigError` → `_err(...)` + `return 2`. Refusal is at the CLI/decision
  layer only.
- `tests/backtest/test_costs.py`: new (A6 + A7 coverage).

`run_harness` / `run_backtest` / walkforward signatures keep accepting
`cost_model: CostModel | None` — `None` still means no costs for unit tests. The
**refusal lives at the CLI/decision layer only**, so library-level tests that
intentionally pass `None` are unaffected.

## contract_size values added

| Symbol  | contract_size | meaning             |
|---------|---------------|---------------------|
| EURUSD  | 100000        | units per std lot   |
| XAUUSD  | 100           | oz per lot          |
| BTCUSDT | 1             | BTC per lot         |

## Refuse-at-CLI design

- Library (`compute_trade_cost`, harness): unchanged contract, `None` = no costs.
- `get_cost_model`: single chokepoint that turns "missing symbol" into a hard
  `ConfigError` unless the caller explicitly opts out.
- `scripts/run_backtest.py`: opt-out via `--allow-zero-cost` (loud warning).
- `cli/backtest.py` (go-live gate): no opt-out — always refuses, exit code 2.

## RED evidence

```
$ .venv\Scripts\pytest.exe -q tests/backtest/test_costs.py
ImportError while importing test module 'tests/backtest/test_costs.py'.
E   ImportError: cannot import name 'get_cost_model' from 'rtrade.backtest.costs'
!!! Interrupted: 1 error during collection !!!
Exit Code: 1
```
(Also drove `contract_size` field and the per-lot commission term.)

## GREEN

```
$ .venv\Scripts\pytest.exe -q tests/backtest tests/unit/test_costs_xauusd.py tests/unit/test_backtest.py
.... (all passed)  Exit Code: 0
```
USDJPY pip-only test still passes (`commission_usd_per_lot_rt=0` ⇒ term 0).
EURUSD now includes `0.00007` and asserts `cost > pip_only`.

## Full suite + lint + types

```
$ .venv\Scripts\pytest.exe -q
799 passed, 7 skipped, 1 warning in 52.43s            Exit Code: 0

$ .venv\Scripts\ruff.exe check src tests
All checks passed!

$ .venv\Scripts\mypy.exe src
Success: no issues found in 129 source files
```

## Concerns

- Pre-existing `ruff I001` (import block around `sys.path.insert`) in
  `scripts/run_backtest.py` — present before this change and outside the
  required `ruff check src tests` scope; left untouched to honor the one-commit /
  minimal-diff constraint.
- XAUUSD uses percentage-based costs only (`commission_pct_round_turn`), so its
  `contract_size: 100` and BTCUSDT `contract_size: 1` are documented for
  completeness but do not feed a per-lot commission term today. If a USD/lot
  commission is later added for those symbols, the conversion is already wired.
- `--allow-zero-cost` exists as an escape hatch on `run_backtest.py`; it is loud
  but still allows a cost-free run. The go-live gate (`cli/backtest.py`) has no
  such hatch by design.
