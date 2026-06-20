# Scalping MTF + SMC/ICT + Swing Refresh + Multi-Account Data — Design Spec

**Status:** Draft for review
**Date:** 2026-06-20
**Author:** Kiro (brainstorming skill)
**Scope owner:** project owner (personal use, private repo)

---

## 1. Goal

Turn Robil Trade from a quiet H1-only swing scanner (effectively producing zero
signals on rate-limited data) into a **multi-timeframe signal engine** that:

- Scalps **XAUUSD** on **M5 and M15 in parallel**, using **H4 as the trend/regime anchor**.
- Adds two new scalping strategies — **S3 MTF-Confluence** and **S4 SMC/ICT** — and
  **refreshes the swing strategies (S1/S2)** with researched, validated improvements.
- Runs on a **rate-limit-proof data layer**: **OANDA** primary for FX/metals, with
  **multi-account + provider fallback** (composite) so a single key/account limit never
  stalls the engine. Crypto stays on Binance/ccxt (already unlimited).
- Emits **more signals** via a dedicated **scalping gate profile** — WITHOUT weakening any
  hard safety floor.
- Proves every strategy×timeframe through the existing **walk-forward + DSR + PBO backtest
  gate** (P1-6) before it is allowed to go live. Shadow/paper first, live only after passing.

This stays **signal-only** (manual execution). No order/broker placement is added.

## 2. Non-Goals (explicitly deferred)

- Instruments beyond XAUUSD for the *new* scalping strategies (foundation enables M5/M15 for
  all FX, but strategy validation focuses on XAUUSD first; expansion is a follow-up).
- Automated order execution / broker integration.
- Enabling the LLM layer (`llm.enabled` stays `false`).
- Dukascopy historical backfill integration (optional future enhancement; OANDA backfill is
  sufficient for this spec).

## 3. Honest framing (so we don't chase ghosts)

There is no "90% accurate gacor" strategy. Credible sources converge on **asymmetric R:R over
high win-rate**, and most public TradingView strategies fail live. The real edge here is the
**anti-overfitting validation harness** already in the repo. So the plan is: adapt reputable,
permissively-licensed concepts (SMC/ICT, MTF confluence, SuperTrend+ADX regime filter), combine
them, and let the **backtest gate be the judge** — not the marketing. Expectation: WR ~40–55%
with RR ≥ 1.5, positive OOS expectancy, DSR ≥ 0.90, PBO ≤ 0.30.

## 4. Decomposition into sub-projects (each = its own implementation plan)

This spec is large by design. Per the writing-plans method it MUST be split into separate,
independently-testable plans. Build order:

| # | Sub-project | Why this order | Plan file |
|---|-------------|----------------|-----------|
| **SP-1** | Multi-account + fallback data layer (OANDA + Composite + multi-key) | Prerequisite — M5/M15 scalping is impossible on rate-limited single-key TwelveData | `docs/superpowers/plans/2026-06-20-sp1-data-layer.md` |
| **SP-2** | Multi-timeframe scan engine (anchor H4 + entry M5/M15) | Needs SP-1 data; all strategies plug into this | `…-sp2-mtf-engine.md` |
| **SP-3** | SMC/ICT indicator module (FVG, order block, liquidity sweep, BOS/CHoCH) | Shared dependency for S4 (and useful to S3) | `…-sp3-smc-indicators.md` |
| **SP-4** | Scalping strategies S3 (MTF confluence) + S4 (SMC/ICT) + scalping gate profile | The signal producers; needs SP-2, SP-3 | `…-sp4-scalping-strategies.md` |
| **SP-5** | Swing strategy refresh (S1/S2 improvements) | Independent of scalping; can run in parallel after SP-1 | `…-sp5-swing-refresh.md` |
| **SP-6** | Validation & rollout (backtest each strategy×TF, shadow→live gate) | Final gate; needs SP-4/SP-5 | `…-sp6-validation-rollout.md` |

Each plan produces working, tested software on its own and ends green on the full gate
(`ruff` + `ruff format` + `mypy --strict src` + `pytest`).

## 5. Global Constraints (verbatim, bind every task)

- **Signal-only** — no order/broker placement, ever.
- **Hard risk floors (config-loader enforced, never weakened):** GR-03 `rr_min ≥ 1.5`;
  GR-04 `sl_atr ∈ [0.5, 3.0]`; GR-05 `risk_per_trade_pct ≤ 2.0`.
- **News blackout (GR-07)** applies to ALL timeframes incl. M5/M15.
- **Calendar fail-CLOSE:** `calendar.fail_open_when_stale = false`.
- **`llm.enabled = false`**; GI-5: no `model_construct` on the production path.
- **Warmup guarantee (P1-7):** abstain (`abstain_warmup`) until a full warmup window exists,
  per entry timeframe.
- **Determinism in tests:** `freezegun`/`respx`, no live network; integration tests skip when
  the live stack (DB/Redis/OANDA) is unreachable.
- **Toolchain (run via venv):** `.venv\Scripts\python.exe -m <tool>`. Gate per phase:
  `ruff check src tests migrations` ; `ruff format src tests migrations` ;
  `mypy --strict src` ; `pytest tests -q`.
- **Commits:** message via `COMMIT_MSG_TMP.txt` + `git commit -F`, then delete the temp file.
  Remove stray Windows `nul` artifact before commit. No push unless explicitly requested.
- **Min trades floor:** `backtest.min_trades_for_validation ≥ 100` (never lower).

---

## 6. SP-1 — Multi-account + fallback data layer

### 6.1 Problem
`TwelveDataProvider` is single-key (8 req/min, 800/day). One instrument×TF per scan + quote
burns the budget fast; M5 scalping across multiple TFs is impossible. We need (a) a
higher-throughput primary (OANDA: ~120 req/s, 5000 candles/page), and (b) **multi-account +
fallback** so no single limit stalls the engine.

### 6.2 Reuse existing patterns (do NOT invent new ones)
- **Fallback chain** mirrors `data/composite_calendar.py::CompositeCalendarProvider` — ordered
  sources, per-source health, alert on transition, raise only when ALL fail.
- **Multi-key config** mirrors `Secrets.keys_for(family)` (`gemini_api_key_1..5`).
- **Per-key rate limiting** uses the existing Redis token bucket `data/ratelimit.py`
  (one `BucketConfig` per account/key).

### 6.3 Components
- **`data/oanda_provider.py` → `OandaProvider(MarketDataProvider)`** — implements
  `fetch_ohlcv(symbol, timeframe, since, limit)`, `fetch_quote(symbol)`, `fetch_spread(symbol)`,
  `close()`. REST v20 (`/v3/instruments/{instrument}/candles`, `granularity`, `count`/`from`,
  `price="M"` mid). TF map: `{M5:"M5", M15:"M15", H1:"H1", H4:"H4", D1:"D"}`. Constructor takes
  `(token, account_id, rate_limiter, *, practice: bool)`. Base URL switches practice vs live.
  Parse OANDA candle JSON → domain `Candle` (mid OHLC, volume). Newest/oldest order normalized
  ascending. 429/5xx → `RateLimitExceeded`/`ProviderError`.
- **`data/composite_market.py` → `CompositeMarketDataProvider(MarketDataProvider)`** — wraps an
  ordered list of `(name, provider)` "legs" (each leg = one account/key of one vendor). On
  `fetch_ohlcv`/`fetch_quote`: try legs in order; on `RateLimitExceeded`/`ProviderError`, record
  health, alert on transition, advance to next leg; raise `ProviderError` only when all legs
  fail. Health snapshot + `active_tier()` like the calendar composite. Round-robin within a
  vendor's accounts to spread load (configurable: failover-only vs round-robin).
- **`Secrets` additions** (`core/config.py`): `oanda_token_1..3`, `oanda_account_1..3`,
  `oanda_env: Literal["practice","live"]="practice"`, `twelvedata_api_key_1..3` (keep legacy
  `twelvedata_api_key` as slot 0 for back-compat). New method
  `market_keys_for(provider: str) -> list[tuple[token, account|None]]`.
- **Provider factory** (`pipeline/scan.py::_make_market_provider`) — build a
  `CompositeMarketDataProvider` from configured legs based on `instrument.provider`:
  - `oanda` → one leg per `(oanda_token_i, oanda_account_i)`, then optional TwelveData legs as
    last-resort fallback.
  - `twelvedata` → one leg per `twelvedata_api_key_i`.
  - `ccxt_binance` → unchanged (single `CcxtProvider`), no key needed.
- **Rate buckets** (`data/ratelimit.py`): `OANDA_BUCKET` (e.g. 100/s, well under 120/s),
  one bucket name per account (`oanda_acc{i}`); keep `TWELVEDATA_BUCKET` per key
  (`twelvedata_k{i}`).
- **`instruments.yaml`**: XAUUSD `provider: oanda`, `provider_symbol: "XAU_USD"`,
  `timeframes: ["5m","15m","4h"]`. (EURUSD etc. unchanged until expansion.)

### 6.4 Interfaces produced
- `OandaProvider(token: str, account_id: str, rate_limiter: RateLimiter, *, practice: bool=True)`
- `CompositeMarketDataProvider(legs: list[tuple[str, MarketDataProvider]], *, alert_callback=None, mode: Literal["failover","round_robin"]="failover")`
- `Secrets.market_keys_for(provider: str) -> list[tuple[str, str | None]]`

### 6.5 Tests
- `respx`-mocked OANDA: candle parse (ascending, mid OHLC, volume), 429 → `RateLimitExceeded`,
  granularity map, `from`/`count` paramization.
- Composite: leg-1 ok → used; leg-1 429 → leg-2 used + transition alert; all fail → `ProviderError`;
  round-robin distributes across accounts (count calls per leg).
- `Secrets.market_keys_for` returns only non-empty slots, ordered.
- Integration (skip without OANDA token): real candle fetch for XAU_USD M5.

## 7. SP-2 — Multi-timeframe scan engine (anchor H4 + entry M5/M15)

### 7.1 Current state
`run_scan(symbol, timeframe)` ingests `tf`, returns `ingested_context_only` for any tf≠H1, and
only runs the full pipeline for H1, loading H4 as context. The "entry TF" is hard-coded to H1.

### 7.2 Change
- Generalize the pipeline to an **entry-TF set** `{M5, M15}` with **H4 as the anchor**:
  - Introduce `ENTRY_TIMEFRAMES` concept driven by config (per instrument
    `entry_timeframes: [5m,15m]`, `anchor_timeframe: 4h`).
  - `run_scan(symbol, tf)` runs the FULL pipeline when `tf` ∈ entry TFs; ingests-only for the
    anchor TF.
  - Load entry-TF df (M5/M15) + anchor-TF df (H4). Compute **H4 trend bias** (EMA slope +
    `RegimeClassifier` on H4) and pass it to strategies; only entries **aligned with H4 bias**
    pass.
  - Warmup guarantee (P1-7 `_warmup_deficit`) applies per entry TF AND the anchor TF.
- **Scheduler** (`scheduler/main.py` + `scan_schedules`): add cron jobs for XAUUSD × {M5 every
  5 min, M15 every 15 min} at candle-close + buffer; anchor H4 ingest job. Idempotency already
  guaranteed by `signals` unique constraint per bar.
- **MTF helper** `pipeline/mtf.py`: pure functions `h4_trend_bias(df_h4) -> Bias{UP,DOWN,NONE}`
  and `aligned(bias, action) -> bool`. Testable without I/O.

### 7.3 Interfaces produced
- `h4_trend_bias(df_h4: pd.DataFrame) -> Literal["UP","DOWN","NONE"]`
- `run_scan(..., timeframe ∈ {"5m","15m","1h","4h"})` with entry/anchor routing.

### 7.4 Tests
- `h4_trend_bias`: synthetic up/down/flat frames → correct bias; insufficient bars → NONE.
- Entry routing: monkeypatched I/O (like `tests/unit/test_scan_post_llm_gate.py`) — M5 runs full
  pipeline, H4 returns ingest-only; bias-misaligned candidate is rejected.
- Warmup per TF: M5 with <warmup bars → `abstain_warmup`.

## 8. SP-3 — SMC/ICT indicator module

### 8.1 Component
`indicators/smc.py` — pure functions over an OHLC(V) DataFrame, extending the existing
`indicators/structure.py` (which already has swing points, SR clustering, gap detection):
- `fair_value_gaps(df) -> list[FVG{start_idx,end_idx,top,bottom,direction}]` (3-bar imbalance).
- `order_blocks(df, *, swing_lookback) -> list[OrderBlock{idx,top,bottom,direction}]`
  (last opposing candle before a Break of Structure).
- `liquidity_sweeps(df, *, swing) -> list[Sweep{idx,level,side}]` (wick beyond prior swing then
  reversal close).
- `market_structure(df) -> list[StructureEvent{idx,kind:BOS|CHoCH,direction}]`.
- Concepts adapted from the **MIT-licensed** `joshyattridge/smart-money-concepts` and
  `LesterCS/Decoding-Institutional-Order-Flow-in-Python-like-ICT`. We **port the specific
  detectors** (not add the dependency) for control, `mypy --strict` compliance, and
  testability; attribution + MIT notice in the module docstring.

### 8.2 Tests
Deterministic fixtures with hand-constructed bars: a known FVG, a known bullish/bearish OB, a
sweep + reversal, a BOS and a CHoCH. Assert exact indices/levels. Property tests for invariants
(e.g., FVG top > bottom; OB direction matches preceding move).

## 9. SP-4 — Scalping strategies + gate profile

### 9.1 S3 — MTF Confluence Scalper (`strategies/s3_mtf_scalper.py`)
- Implements the `Strategy` ABC (`populate_indicators`, `entry_signal`, `custom_entry_price`,
  `confirm_signal`); `name="s3_mtf_scalper"`; registered in `STRATEGY_REGISTRY`.
- Logic: H4 bias = trend direction → on entry TF, wait for **pullback to EMA20/VWAP** in the
  bias direction + **momentum trigger** (RSI/StochRSI exiting extreme) + **structure confirm**
  (HL for longs / LH for shorts) + **volume/edge-quality** filter. SL = k×ATR (within
  [0.5,3.0]); TP at RR ≥ rr_min (≥1.5). `required_regime = TREND`.
- Grounded in community consensus (Dual-MTF confirmed trend + VWAP/EMA/RSI confluence).

### 9.2 S4 — SMC/ICT Scalper (`strategies/s4_smc_scalper.py`)
- Uses `indicators/smc.py`. Logic: H4 bias → on entry TF, after a **liquidity sweep** +
  **BOS/CHoCH** in bias direction, place entry **limit at the order block / FVG**, SL beyond the
  sweep, TP toward the next liquidity pool (RR ≥ 1.5). `required_regime = TREND` (and/or a
  dedicated regime tag).

### 9.3 Scalping gate profile (`core/config.py` + gate plumbing)
- New `signal.profiles` map: `default` (current values) and `scalping` (more permissive:
  e.g. `confluence_min_score: 50`, `edge_quality.min_score: 55`, `confidence_min: 0.50`,
  `max_signals_per_day_per_instrument: 10`). Strategy/TF selects its profile.
- **Hard floors are NOT part of the profile** — RR/SL/risk/news/`llm.enabled` remain
  globally validated and untouched.
- Mirror the P1-6 "thresholds from config flip pass/fail" testability: a unit test changes a
  profile threshold and asserts a candidate flips published↔abstain with no code change.

### 9.4 Tests
- S3/S4 entry rules: TDD with synthetic frames — valid setup → candidate; bias-misaligned →
  none; RR<1.5 → discarded; SL outside ATR band → discarded.
- Gate profile selection: scalping profile lowers the "becomes-candidate" bar while hard floors
  still reject violations.

## 10. SP-5 — Swing strategy refresh (S1/S2)

### 10.1 Research-backed improvements
- **S1 (trend pullback, TREND):** add **SuperTrend + ADX/Choppiness regime confirmation** to cut
  whipsaw, and an **MTF EMA bias (H4/D1)** filter; keep the pullback-to-value entry. Reference:
  SuperTrend V4 "regime filter" pattern; XAUUSD pullback state-machine (Sharpe 0.89 reference).
- **S2 (range mean-reversion, RANGE):** tighten with **Bollinger/Keltner band touch + RSI
  divergence** confirmation and a **Choppiness/ADX gate** so it only fires in genuine ranges.
- All additions are **opt-in via strategy config** (`config/strategies/*.yaml`) and must pass the
  same backtest gate; no hard-floor changes.

### 10.2 Tests
- New confirmation predicates as pure functions with deterministic fixtures (SuperTrend flip,
  ADX threshold, Choppiness, BB/Keltner touch, RSI divergence). Existing S1/S2 tests must stay
  green (preservation) unless a behavior change is intentional and re-baselined.

## 11. SP-6 — Validation & rollout

- For each `{S3,S4,S1,S2} × {M5,M15 or H4}` on XAUUSD, run `rtrade.cli.backtest` (walk-forward +
  DSR + PBO + ≥100 trades + OOS expectancy>0 + PF≥1.15 + DD≤25%). Costs from `config/costs.yaml`
  (realistic XAUUSD spread/commission) MUST be applied.
- **Go-live gate:** a strategy is only enabled (`StrategyState`/registry) after it passes. Until
  then it runs in **shadow/paper** mode (the repo already has paper-tracking) so signals are
  recorded and tracked without being trusted.
- Historical M5/M15 data sourced via OANDA backfill (paginated). Optional Dukascopy deep history
  is a future enhancement, not required here.

## 12. Cost / spread realism (scalping gold)
- Add XAUUSD spread/commission to `config/costs.yaml` (supported by `backtest/costs.py`).
- Filter: TP must exceed `k × spread`; keep `edge_quality.max_spread_atr`. Keep session filter
  (London/NY) so we don't scalp thin liquidity. This is what makes the validation honest —
  scalping gold has real cost headwinds and many ideas will (correctly) fail the gate.

## 13. Error handling & safety
- Provider failover is **fail-CLOSE for signals**: if ALL data legs fail → skip the cycle, no
  blind signal. News blackout enforced on M5/M15. Warmup guarantee per TF. Calendar stale →
  reject (unchanged). OANDA outage → composite advances legs, alerts, and only raises when all
  exhausted.
- Multi-account secrets: never logged; reference by slot name, not value.

## 14. Testing strategy
- **Deterministic units/properties** for: OANDA parse (respx), composite failover, MTF bias,
  SMC detectors, S3/S4/S1/S2 predicates, gate-profile flips. No live network.
- **Integration (skip without stack):** OANDA real fetch, end-to-end backtest, cold-start M5
  warmup. Use the existing TCP-reachability skip pattern.
- Every phase ends on the full green gate.

## 15. Risks & open questions
- **SMC determinism:** SMC/ICT is partly subjective; we pin the most standard definitions
  (3-bar FVG, OB = last opposing candle before BOS) so it's testable. Risk: detectors differ
  from a given TradingView script's visuals — acceptable; the gate judges profitability.
- **Scope size:** six sub-projects. Mitigation: each is an independent, gated plan; SP-5 can run
  parallel to SP-2..SP-4 after SP-1.
- **OANDA gold = CFD** (slightly different from spot); fine for signal-only H1/H4/M5/M15.
- **Open Q (non-blocking):** failover-only vs round-robin default for multi-account (spec
  default: failover; round-robin configurable).

## 16. Licensing / attribution
Personal, private use. Where concepts/code are adapted from open-source repos (e.g. MIT
`smart-money-concepts`, MIT ICT order-flow), keep the MIT notice + attribution in the porting
module's docstring. No commercial redistribution.
