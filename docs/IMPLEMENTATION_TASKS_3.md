# IMPLEMENTATION TASKS 3 — WIRING ONLY (W1–W10)

> Hasil verifikasi gelombang 2: **F1–F7 LULUS** (kecuali satu sisa di jobs.py), tapi
> **T20–T30 mengulang pola modul-yatim**: commit `6e4085f` hanya berisi modul + test —
> `scan.py` TIDAK TERSENTUH SATU BARIS PUN. Selain itu ditemukan REGRESI: `track_paper_signals`
> masih memakai jalur lama 1-candle (`check_fill`/`check_outcome`), padahal `replay_signal` (T9)
> sudah ada di tracker.py.
>
> Dokumen ini **TIDAK berisi modul baru sama sekali**. Semua modul SUDAH ADA dan testnya hijau.
> Tugasmu HANYA menyambungkan. Definisi selesai per task = BUKTI Select-String terpenuhi
> (pakai Select-String PowerShell, BUKAN ripgrep — ripgrep terbukti melewatkan beberapa file).
> Aturan kerja & larangan: sama dengan `docs/IMPLEMENTATION_TASKS.md` Section 0.
> Commit per task. Jalankan `pytest -q` setelah SETIAP task.

---

## W1 — Papertrack: ganti jalur 1-candle dengan replay penuh + semua analitik

**Ini task terbesar dan paling penting.** Lokasi: `pipeline/scan.py::track_paper_signals`
(baris ±319–360 saat ini — blok `for signal in await signal_repo.open_for_tracking():`).

### Persiapan kecil
1. `papertrack/tracker.py` — pastikan `CandleBar` punya field `close: float` (kalau belum:
   tambahkan, lalu perbaiki semua konstruktor di test). `virtual_exits._eval_time_stop`
   membutuhkannya.
2. `persistence/repositories.py` — tambah method generik di `SignalRepo` (pola sama dengan
   `mark_delivery`):
   ```python
   async def merge_payload(self, signal_id: str, key: str, value: object) -> None:
       """Read-modify-write one key into the signal's JSONB payload."""
       signal = await self.get(signal_id)
       if signal is None:
           return
       payload = dict(signal.payload)
       payload[key] = value
       signal.payload = payload
   ```
3. `persistence/repositories.py` — tambah di `InstrumentRepo`:
   ```python
   async def get_by_id(self, instrument_id: int) -> Instrument | None:
       return await self._session.get(Instrument, instrument_id)
   ```

### Tulis ulang isi loop `track_paper_signals`
GANTI SELURUH blok `for signal in ...` (baris 322–358) dengan:
```python
for signal in await signal_repo.open_for_tracking():
    start = ensure_utc(signal.published_at or signal.bar_ts)
    rows = await candle_repo.get_range(
        signal.instrument_id,
        Timeframe(signal.timeframe),
        start,
        datetime.now(UTC),
    )
    if not rows:
        continue
    bars = [
        CandleBar(
            ts=ensure_utc(r.ts),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
        )
        for r in rows
    ]
    entry = float(signal.entry_limit or 0)
    sl = float(signal.stop_loss or 0)
    tp = float(signal.take_profit or 0)

    update = replay_signal(
        signal.signal_id,
        signal.action,
        entry,
        sl,
        tp,
        signal.valid_until or signal.bar_ts,
        already_filled=signal.status == SignalStatus.FILLED.value,
        candles=bars,
    )
    if update is None or update.new_status.value == signal.status:
        continue

    # --- T22: resolusi 1 menit untuk hasil ambigu (crypto saja) ---
    final_status = update.new_status
    outcome_r = update.outcome_r
    resolution = "bar"
    if update.new_status == SignalStatus.SL_HIT and _bar_is_ambiguous(
        signal.action, sl, tp, bars, update.resolved_at
    ):
        resolution = "worst_case"
        inst_row = await InstrumentRepo(session).get_by_id(signal.instrument_id)
        if inst_row is not None and inst_row.market == "crypto":
            minute_bars = await _fetch_minute_bars(
                cfg, inst_row, update.resolved_at
            )
            if minute_bars:
                first = resolve_ambiguous_bar(
                    signal.action, entry, sl, tp, minute_bars
                )
                resolution = "minute"
                if first == "TP":
                    final_status = SignalStatus.TP_HIT
                    sl_dist = abs(entry - sl) or 1.0
                    outcome_r = abs(tp - entry) / sl_dist

    await signal_repo.update_tracking_status(
        update.signal_id,
        status=final_status.value,
        resolved_at=update.resolved_at,
        outcome_r=Decimal(str(outcome_r)) if outcome_r is not None else None,
    )
    await signal_repo.merge_payload(signal.signal_id, "resolution", resolution)

    # --- T23 + T24 + T30: analitik saat trade SELESAI (TP/SL) ---
    if final_status in (SignalStatus.TP_HIT, SignalStatus.SL_HIT):
        fill_idx = _first_touch_index(signal.action, entry, bars)
        after_fill = bars[fill_idx:] if fill_idx is not None else []
        if after_fill:
            atr_val = float(
                (signal.payload.get("candidate") or {})
                .get("levels", {})
                .get("atr_at_signal", 0)
            ) or 1.0
            await signal_repo.merge_payload(
                signal.signal_id,
                "virtual_exits",
                evaluate_virtual_exits(
                    signal.action, entry, sl, tp, atr_val, after_fill
                ),
            )
            mae_r, mfe_r = compute_excursion(signal.action, entry, sl, after_fill)
            await signal_repo.merge_payload(
                signal.signal_id, "excursion", {"mae_r": mae_r, "mfe_r": mfe_r}
            )
        if (
            final_status == SignalStatus.SL_HIT
            and cfg.settings.llm.enabled
            and cfg.settings.llm.coroner_enabled
        ):
            try:
                report = await run_coroner(
                    LLMClient(
                        api_key=cfg.secrets.gemini_api_key_1,
                        timeout=cfg.settings.llm.timeout_seconds,
                    ),
                    model=cfg.settings.llm.analyst_model,
                    candidate_payload=signal.payload.get("candidate") or {},
                    price_path=[
                        {"ts": b.ts.isoformat(), "high": b.high, "low": b.low, "close": b.close}
                        for b in after_fill[:12]
                    ],
                )
                await signal_repo.merge_payload(
                    signal.signal_id, "coroner", report.model_dump()
                )
            except Exception as exc:
                logger.warning("coroner failed", error=str(exc))
    updates += 1
```
Helper module-level baru di scan.py:
```python
def _bar_is_ambiguous(
    action: str,
    stop_loss: float,
    take_profit: float,
    bars: list[CandleBar],
    resolved_at: datetime,
) -> bool:
    """True bila bar tempat resolusi menyentuh SL DAN TP sekaligus."""
    for bar in bars:
        if bar.ts != resolved_at:
            continue
        if action == "BUY":
            return bar.low <= stop_loss and bar.high >= take_profit
        return bar.high >= stop_loss and bar.low <= take_profit
    return False


def _first_touch_index(
    action: str, entry: float, bars: list[CandleBar]
) -> int | None:
    for i, bar in enumerate(bars):
        if bar.low <= entry <= bar.high:
            return i
    return None


async def _fetch_minute_bars(
    cfg: AppConfig, inst_row: Any, bar_open_ts: datetime
) -> list[CandleBar]:
    """Fetch 1m candles for one ambiguous 1H bar (crypto only, best-effort)."""
    from rtrade.core.timeutil import timeframe_duration

    instrument = cfg.instrument(inst_row.symbol)
    redis_client = aioredis.from_url(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = _make_market_provider(instrument, cfg, limiter)
    try:
        candles = await provider.fetch_ohlcv(
            instrument.provider_symbol,
            Timeframe.M1,
            since=ensure_utc(bar_open_ts),
            limit=70,
        )
        end = ensure_utc(bar_open_ts) + timeframe_duration(Timeframe.H1)
        return [
            CandleBar(
                ts=ensure_utc(c.ts),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
            )
            for c in candles
            if ensure_utc(c.ts) < end
        ]
    except Exception as exc:
        logger.warning("minute fetch failed — keeping worst-case", error=str(exc))
        return []
    finally:
        await provider.close()
        await redis_client.aclose()
```
Import yang ditambah di scan.py:
`CandleBar, replay_signal` dari `rtrade.papertrack.tracker`;
`resolve_ambiguous_bar` dari `rtrade.papertrack.minute_resolution`;
`evaluate_virtual_exits` dari `rtrade.papertrack.virtual_exits`;
`compute_excursion` dari `rtrade.papertrack.excursion`;
`run_coroner` dari `rtrade.llm.coroner`.
HAPUS import `check_fill, check_outcome` dari scan.py (fungsi lamanya tetap ada untuk test lama).
CATATAN: `cfg.settings.llm.coroner_enabled` — kalau field belum ada di `LLMSettings`,
tambahkan `coroner_enabled: bool = False` + key `coroner_enabled: false` di settings.yaml.
CATATAN resolved_at TP via minute: `update.resolved_at` tetap dipakai (timestamp bar 1H).

**Test baru** `tests/unit/test_track_helpers.py`:
- `_bar_is_ambiguous`: BUY bar low<SL high>TP pada ts yang sama → True; bar normal → False.
- `_first_touch_index`: entry tersentuh di bar ke-2 → 1; tidak pernah → None.
**BUKTI**:
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "replay_signal|evaluate_virtual_exits|compute_excursion|resolve_ambiguous_bar|run_coroner" | Measure-Object   # Count >= 5
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "check_fill|check_outcome" | Measure-Object  # Count == 0
```
**Commit**: `feat(papertrack): wire full replay + minute resolution + virtual exits + MAE/MFE + coroner (W1)`

---

## W2 — Wire derivatives (T20) ke run_scan

Lokasi anchor: `run_scan`, SETELAH blok `live_price` (baris ±184–189), SEBELUM `_run_strategies`.
```python
derivatives_data: dict[str, Any] | None = None
funding_extreme = False
if instrument.derivatives and isinstance(provider, CcxtProvider):
    try:
        funding = await provider.fetch_funding_rate(instrument.provider_symbol)
        oi = await provider.fetch_open_interest(instrument.provider_symbol)
        rate = float(funding.funding_rate)
        funding_extreme = abs(rate) >= FUNDING_EXTREME_ABS
        derivatives_data = {
            "funding_rate": rate,
            "funding_extreme_flag": funding_extreme,
            "oi_change_24h": None,
            "open_interest": float(oi.open_interest),
        }
        session.add(
            DerivativesSnapshot(
                instrument_id=inst_row.id,
                ts=now,
                funding_rate=funding.funding_rate,
                open_interest=oi.open_interest,
            )
        )
    except ProviderError as exc:
        logger.warning("derivatives fetch failed", error=str(exc))
```
Konstanta module-level: `FUNDING_EXTREME_ABS = 0.0005  # |0.05%|/8h — funding ekstrem`.
Import `DerivativesSnapshot` dari `rtrade.persistence.models`.
Lalu:
1. `_run_strategies` tambah parameter `funding_extreme: bool = False` dan
   `derivatives_data: dict[str, Any] | None = None`; `run_scan` meneruskan keduanya.
2. Di `generate_candidate(...)` ganti `funding_extreme=False` → `funding_extreme=funding_extreme`.
3. `_build_pack` tambah parameter `derivatives_data: dict[str, Any] | None = None` →
   `build_context_pack(..., derivatives=derivatives_data, ...)`; pemanggilnya meneruskan.

**Test**: konstanta + helper: ekstrak `def _is_funding_extreme(rate: float) -> bool` bila mau,
atau cukup test threshold via `abs(0.0006) >= FUNDING_EXTREME_ABS` di test kecil.
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "fetch_funding_rate|DerivativesSnapshot" | Measure-Object` ≥ 2.
**Commit**: `feat(data): wire funding/OI into scan, confluence, and context pack (W2)`

---

## W3 — Wire live spread (T21) ke generate_candidate

Anchor: setelah blok derivatives (W2):
```python
spread: float | None = None
try:
    spread = await provider.fetch_spread(instrument.provider_symbol)
except Exception as exc:
    logger.warning("spread fetch failed", error=str(exc))
```
- `_run_strategies` += parameter `spread: float | None = None`; teruskan ke
  `generate_candidate(..., spread=spread)` (parameter `spread` SUDAH ADA di signature
  generate_candidate — cek `signals/engine.py`).
- Audit kandidat (stage CANDIDATE, W sudah ada di F2) — tambahkan `"spread": spread` ke detail.

**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "fetch_spread"` ≥ 1.
**Commit**: `feat(data): wire live spread into edge-quality EQ-02 (W3)`

---

## W4 — Wire risk throttle (T27)

Di `_run_strategies`: query `paper_outcomes` saat ini dilakukan SETELAH `generate_candidate`.
**PINDAHKAN** ke atas (sebelum `generate_candidate`), lalu:
```python
risk_pct = cfg.settings.risk.risk_per_trade_pct
if cfg.settings.risk.throttle_enabled:
    risk_pct = throttled_risk_pct(
        risk_pct,
        paper_outcomes,
        window=cfg.settings.risk.throttle_window,
        mult=cfg.settings.risk.throttle_mult,
    )
```
dan `generate_candidate(..., risk_pct=risk_pct, ...)`.
Kalau field `throttle_enabled/throttle_window/throttle_mult` belum ada di `RiskSettings`:
tambahkan (default True/10/0.5) + 3 key di settings.yaml `risk:`.
Import `throttled_risk_pct` dari `rtrade.risk.limits`.

**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "throttled_risk_pct"` ≥ 1.
**Commit**: `feat(risk): wire equity-curve throttle into scan sizing (W4)`

---

## W5 — Wire Bayesian Kelly (T26) ke payload publish

Di publish path `_run_strategies` (setelah grading F4, sebelum `session_repo.add`):
```python
resolved = [r for r in paper_outcomes if r is not None]
if len(resolved) >= 30:
    wins = [r for r in resolved if r > 0]
    losses = [r for r in resolved if r <= 0]
    if wins and losses:
        kelly_f = bayesian_kelly_fraction(
            len(wins),
            len(losses),
            sum(wins) / len(wins),
            abs(sum(losses) / len(losses)),
        )
        payload["kelly"] = {"bayes_fraction": kelly_f, "n": len(resolved)}
```
(variabel `payload` sudah ada dari F4. Import `bayesian_kelly_fraction` dari `rtrade.risk.kelly`.)

**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "bayesian_kelly_fraction"` ≥ 1.
**Commit**: `feat(risk): publish Bayesian Kelly suggestion in signal payload (W5)`

---

## W6 — Wire case-based memory (T29) ke context pack

1. `persistence/repositories.py` — `SignalRepo` method baru:
   ```python
   async def resolved_with_features(self, strategy: str, limit: int = 500) -> list[dict[str, Any]]:
       """Resolved signals (TP/SL) with confluence features for k-NN."""
       stmt = (
           select(Signal)
           .where(
               Signal.strategy == strategy,
               Signal.status.in_(("TP_HIT", "SL_HIT")),
               Signal.outcome_r.is_not(None),
           )
           .order_by(Signal.resolved_at.desc().nullslast())
           .limit(limit)
       )
       result = await self._session.execute(stmt)
       out: list[dict[str, Any]] = []
       for s in result.scalars().all():
           cand = (s.payload or {}).get("candidate") or {}
           breakdown = cand.get("confluence_breakdown") or {}
           out.append(
               {
                   **{k: float(breakdown.get(k, 0)) for k in
                      ("trend", "momentum", "structure", "volume", "macro")},
                   "hour": float(s.bar_ts.hour),
                   "outcome_r": float(s.outcome_r or 0),
               }
           )
       return out
   ```
2. `llm/context_pack.py` — `build_context_pack` += parameter
   `similar_setups: dict[str, Any] | None = None`. Bila tidak None:
   masukkan ke dict hasil (`"similar_setups": {...}` + field di `ContextPack` dataclass +
   `to_dict()`), dan tambahkan source_id `mem:similar:{symbol}:{tf}:{bar_ts}` ke `source_ids`.
3. `scan.py` — `_run_strategies` (di blok `if cfg.settings.llm.enabled:` SEBELUM `_build_pack`):
   ```python
   history = await session_repo.resolved_with_features(candidate.strategy)
   bd = candidate.confluence_breakdown
   similar = find_similar_setups(
       {
           "trend": float(bd.trend),
           "momentum": float(bd.momentum),
           "structure": float(bd.structure),
           "volume": float(bd.volume),
           "macro": float(bd.macro),
           "hour": float(candidate.bar_ts.hour),
       },
       history,
   )
   similar_setups = similar if similar.get("n") else None
   ```
   `_build_pack` += parameter `similar_setups` → teruskan ke `build_context_pack`.

**Test**: context pack dengan similar_setups → key muncul di `to_dict()` + source_id `mem:similar`
ada di source_ids; tanpa similar → key tidak ada/None dan TIDAK ada source_id mem.
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "find_similar_setups"` ≥ 1;
`Select-String -Path src/rtrade/llm/context_pack.py -Pattern "similar_setups"` ≥ 3.
**Commit**: `feat(llm): wire similar historical setups into context pack (W6)`

---

## W7 — Wire permutation gate (T25) ke validation

`backtest/validation.py::run_validation_gates` += parameter
`permutation_p: float | None = None`; bila tidak None:
`gates["permutation_p <= 0.05"] = permutation_p <= 0.05`.
Tambah juga ke `ValidationGateResult` field `permutation_p: float | None = None`.

**Test**: `run_validation_gates(..., permutation_p=0.01)` → gate True;
`0.2` → False dan `all_passed` False.
**BUKTI**: `Select-String -Path src/rtrade/backtest/validation.py -Pattern "permutation_p"` ≥ 3.
**Commit**: `feat(validation): permutation p-value gate (W7)`

---

## W8 — HMM shadow mode + job training mingguan (T28 — belum ada sama sekali)

1. `core/constants.py` — `AuditStage` += `REGIME_SHADOW = "regime_shadow"` (aditif).
2. `scan.py` — module-level:
   ```python
   _HMM_CACHE: dict[str, Any] = {}


   def _hmm_shadow_classify(symbol: str, df: pd.DataFrame) -> Any | None:
       """Classify with saved HMM model; None when no model on disk."""
       import joblib

       from rtrade.regime.hmm import HMMRegimeDetector

       detector = _HMM_CACHE.get(symbol)
       if detector is None:
           path = Path("models") / f"hmm_{symbol}.joblib"
           if not path.exists():
               return None
           detector = joblib.load(path)
           _HMM_CACHE[symbol] = detector
       if not isinstance(detector, HMMRegimeDetector) or not detector.is_trained:
           return None
       return detector.classify(symbol, df)
   ```
3. `run_scan` — setelah `regime = RegimeClassifier()...`:
   ```python
   try:
       hmm_state = _hmm_shadow_classify(symbol, df_1h)
       if hmm_state is not None:
           await AuditRepo(session).add(
               stage=AuditStage.REGIME_SHADOW.value,
               ok=hmm_state.regime == regime.regime,
               detail={
                   "rule": regime.regime.value,
                   "hmm": hmm_state.regime.value,
                   "prob": hmm_state.probability,
               },
           )
   except Exception as exc:
       logger.warning("hmm shadow failed", error=str(exc))
   ```
4. `scheduler/jobs.py` — job baru:
   ```python
   async def hmm_train_job() -> None:
       """Weekly HMM retrain per instrument (Sunday 02:00 UTC)."""
       import joblib

       from rtrade.regime.hmm import HMMRegimeDetector

       cfg = AppConfig.load()
       engine = create_engine(cfg.secrets.database_url)
       session_factory = create_session_factory(engine)
       try:
           async with session_factory() as session:
               for inst in cfg.instruments:
                   row = await InstrumentRepo(session).get_by_symbol(inst.symbol)
                   if row is None:
                       continue
                   df = _candles_to_df(
                       await CandleRepo(session).latest_n(row.id, Timeframe.H1, 5000)
                   )
                   if len(df) < 600:
                       continue
                   df = compute_indicators(df)
                   detector = HMMRegimeDetector()
                   detector.train(df)
                   out = Path("models")
                   out.mkdir(exist_ok=True)
                   joblib.dump(detector, out / f"hmm_{inst.symbol}.joblib")
                   logger.info("hmm trained", symbol=inst.symbol)
       finally:
           await engine.dispose()
   ```
   (import yang dibutuhkan dari pipeline/persistence — perhatikan jangan circular import:
   `_candles_to_df`/`compute_indicators` boleh diimport dari `rtrade.pipeline.scan` dan
   `rtrade.indicators.engine`.)
5. `scheduler/main.py` — daftarkan: `CronTrigger(day_of_week="sun", hour="2", minute="0")`,
   id `hmm_train`.
6. Tambah `models/` ke `.gitignore`.

**Test**: `_hmm_shadow_classify("XAUUSD", df)` return None bila file tidak ada (jalankan di
tmp cwd via monkeypatch `Path` TIDAK perlu — cukup pastikan models/hmm_XAUUSD.joblib tidak ada
di repo test env); HMM save/load roundtrip: train detector di df sintetis 600 bar →
joblib dump/load (tmp_path) → classify jalan (tambah di test_hmm_regime.py).
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "_hmm_shadow_classify"` ≥ 2;
`Select-String -Path src/rtrade/scheduler/main.py -Pattern "hmm_train"` ≥ 1.
**Commit**: `feat(regime): HMM shadow classification + weekly training job (W8)`

---

## W9 — Sisa F3: alert kegagalan scan di jobs.py (saat ini 0 match)

`scheduler/jobs.py` — implementasi PERSIS spec T13 langkah 4 dokumen pertama:
`_scan_failures: dict[str, int]`, `_get_alerts()` (AlertManager dari config, enabled bila
token ada), try/except di `scan_job` → `alert_scan_failed(symbol, str(exc), count)` saat
beruntun ≥ 3, reset saat sukses.

**BUKTI**: `Select-String -Path src/rtrade/scheduler/jobs.py -Pattern "alert_scan_failed|AlertManager"` ≥ 2.
**Commit**: `feat(ops): scan failure alerts wired into scheduler jobs (W9)`

---

## W10 — Endpoint analytics (/analytics/exits, /excursion, /failures)

`delivery/api/routes.py` — 3 GET endpoint (tanpa auth — read-only seperti /signals):
1. `/analytics/exits`: ambil semua Signal yang payload-nya punya `virtual_exits`
   (`select(Signal).where(Signal.outcome_r.is_not(None))`, filter di Python), agregasi
   per kebijakan: `{"policy": {"avg_r": .., "n": ..}}`.
2. `/analytics/excursion`: per strategi — rata-rata `excursion.mae_r` dan `mfe_r` untuk
   winners (outcome_r>0) vs losers, plus persentil-90 MAE winners
   (`numpy.percentile`) sebagai `suggested_sl_review`.
3. `/analytics/failures`: distribusi `payload.coroner.failure_mode` per strategi.
Semua: loop Python biasa atas hasil query (JANGAN JSONB SQL rumit), return dict.

**Test**: fungsi agregasi diekstrak pure (`_aggregate_exits(payloads: list[dict])`,
`_aggregate_excursion(...)`, `_aggregate_failures(...)`) di routes.py dan di-unit-test
dengan list dict sintetis (3 test, tanpa DB).
**BUKTI**: `Select-String -Path src/rtrade/delivery/api/routes.py -Pattern "analytics"` ≥ 3.
**Commit**: `feat(api): analytics endpoints — exits, excursion, failures (W10)`

---

## CHECKLIST AKHIR
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
git log --oneline -15   # harus ada 10 commit baru W1..W10
```
Lalu jalankan SEMUA blok BUKTI W1–W10 dan lampirkan output mentahnya di laporan.
Laporan WAJIB menyertakan: per task → status, output BUKTI, nama test baru, deviasi.
JANGAN menandai task selesai bila BUKTI-nya nol match.
