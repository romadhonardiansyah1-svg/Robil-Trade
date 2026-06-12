# IMPLEMENTATION TASKS 2 — Perbaikan Wiring + Gelombang Berikutnya

> Lanjutan dari `docs/IMPLEMENTATION_TASKS.md`. Baca Section 0 dokumen itu lagi — SEMUA aturan
> masih berlaku. Dokumen ini punya 2 bagian: **Milestone 3.5** (perbaikan hasil verifikasi —
> WAJIB duluan) dan **Milestone 4** (fitur baru). Kerjakan berurutan F1→F7 lalu T20→T30.

---

## 0. HASIL VERIFIKASI PEKERJAAN SEBELUMNYA (kenapa Milestone 3.5 ada)

Yang DITERIMA (bagus, jangan diutak-atik lagi): T1–T11, T14, T15 — mapping currency, fail-closed,
GR-12 filter PUBLISHED, cost_per_token, forming-bar drop, ingest incremental, valid_until per TF,
equity config, replay_signal, scheduler dari config, pip_size costs, dual-format strptime,
backfill CLI, context pack kompak. Test 242 hijau.

Yang GAGAL — pola kegagalannya SAMA: **modul dibuat, test modul hijau, tapi TIDAK PERNAH
dipanggil oleh pipeline runtime**. Modul yatim = task GAGAL, sehijau apa pun testnya:

| Masalah | Bukti |
|---|---|
| T16 SALAH BESAR: scan.py:451–479 memanggil 1 LLM call mentah (system prompt generik, user prompt = pesan Telegram), hasil mentahnya langsung jadi `rationale` ke user. `run_llm_pipeline` (Analyst→Critic→Verifier) TIDAK dipakai. Tidak ada VETO, tidak ada verifier anti-halusinasi, tidak ada GR-09/11. | `grep run_llm_pipeline src/rtrade/pipeline/scan.py` → kosong |
| T16 juga TIDAK PERNAH JALAN: settings.yaml masih `analyst_model: trading-analyst` (alias litellm yang tidak resolve di library mode) → call selalu exception → selalu fallback. | settings.yaml |
| T12 wiring hilang: tidak ada `is_enabled`/`set_state`/audit/`hard_block`/`high_impact_within` di scan.py. Modulnya ada, scan tidak memanggilnya. | grep di scan.py → kosong |
| T13 wiring hilang: `send_signal` masih `-> None`, `mark_delivery` tidak pernah dipanggil, jobs.py tidak menyentuh AlertManager. | telegram_bot.py:123, jobs.py |
| T17 cascade.py yatim + desain rapuh (regex confidence dari teks mentah). | grep cascade di scan → kosong |
| T18 smart_exit.py yatim — `run_backtest` tidak punya parameter exit policy. | grep smart_exit di engine.py → kosong |
| T19 grading.py yatim — tidak dipanggil scan/formatter, tidak ada risk multiplier. | grep grading di scan → kosong |
| Git: TIDAK ada commit per task — semua menumpuk di working tree. | `git log` |
| `LLMSettings.verifier_model` ditambahkan — KELIRU KONSEP: verifier itu deterministik (bukan LLM). | config.py:93 |

### ATURAN BARU (tambahan, wajib):
1. **BUKTI WIRING**: setiap task wiring di dokumen ini diakhiri blok `BUKTI` berisi perintah grep
   dan hasil minimal yang diharapkan. Task belum selesai sebelum BUKTI terpenuhi.
2. **Commit per task** — kali ini benar-benar lakukan. Sebelum mulai F1, buat commit baseline:
   ```powershell
   git add -A; git commit -m "feat: milestone 1-3 implementation baseline (T1-T19, wiring pending)"
   ```
3. Branch worktree `Romadhon/sharp-bhabha-c98d9d` BIARKAN — jangan merge/hapus.

---

# MILESTONE 3.5 — PERBAIKAN WIRING (F1–F7)

## F1 — Ganti stub LLM dengan pipeline Analyst→Critic→Verifier yang benar (PALING PENTING)

**File**: `src/rtrade/pipeline/scan.py`, `config/settings.yaml`, `src/rtrade/core/config.py`,
`src/rtrade/llm/pipeline.py`

**Langkah**:
1. `config/settings.yaml` — ganti 2 baris:
   ```yaml
   analyst_model: gemini/gemini-3.1-flash-lite
   critic_model: gemini/gemini-3.1-flash-lite
   ```
2. `core/config.py` — HAPUS field `verifier_model` dari `LLMSettings` (verifier deterministik,
   bukan LLM). Sebelum hapus, pastikan tidak direferensi: `grep -r verifier_model src tests` harus
   hanya menemukan definisinya.
3. `src/rtrade/llm/pipeline.py` — tambah kwargs ke `run_llm_pipeline` dan teruskan ke agen:
   ```python
   analyst_model: str = "gemini/gemini-3.1-flash-lite",
   critic_model: str = "gemini/gemini-3.1-flash-lite",
   ```
   → `run_analyst(client, pack, model=analyst_model)` dan
   `run_critic(client, pack, assessment, model=critic_model)`.
4. `pipeline/scan.py` — tambah 2 fungsi module-level (letakkan sebelum `_run_strategies`):
   ```python
   def _build_pack(
       instrument: InstrumentConfig,
       candidate: SignalCandidate,
       df_1h: pd.DataFrame,
       sr_levels: list[Any],
       gap_zones: list[Any],
       regime: Any,
       event_dicts: list[dict[str, object]],
       session_active: bool,
   ) -> ContextPack:
       from rtrade.indicators.engine import snapshot as indicator_snapshot

       snap = indicator_snapshot(df_1h)
       e = candidate.levels.entry_limit
       sl = candidate.levels.stop_loss
       tp = candidate.levels.take_profit
       rr = abs(tp - e) / abs(e - sl)
       swings = detect_swing_points(df_1h.tail(200))
       highs = [{"price": p.price, "ts": p.ts.isoformat()} for p in swings if p.is_high][-3:]
       lows = [{"price": p.price, "ts": p.ts.isoformat()} for p in swings if not p.is_high][-3:]
       return build_context_pack(
           symbol=instrument.symbol,
           market=instrument.market.value,
           timeframe=candidate.timeframe,
           session_active=session_active,
           action=candidate.action.value,
           entry=e,
           sl=sl,
           tp=tp,
           rr=rr,
           valid_until=candidate.valid_until.isoformat(),
           strategy=candidate.strategy,
           confluence_breakdown=candidate.confluence_breakdown.model_dump(),
           snapshot=snap,
           swing_highs=highs,
           swing_lows=lows,
           sr_levels=[
               {"price": l.price, "strength": l.strength, "is_resistance": l.is_resistance}
               for l in sr_levels
           ],
           gap_zones=[
               {"high": g.high, "low": g.low, "direction": g.direction} for g in gap_zones
           ],
           regime_state=regime.regime.value,
           regime_since=regime.since.isoformat(),
           calendar_events=[
               {
                   **ev,
                   "event_time": ev["event_time"].isoformat()
                   if hasattr(ev["event_time"], "isoformat")
                   else ev["event_time"],
               }
               for ev in event_dicts
           ],
           derivatives=None,
           df_1h=df_1h,
       )


   def _status_for_decision(decision: PipelineDecision) -> SignalStatus:
       """Map pipeline decision to signal status (pure, unit-tested)."""
       if decision in (PipelineDecision.PUBLISH, PipelineDecision.FALLBACK):
           return SignalStatus.PUBLISHED
       if decision == PipelineDecision.REJECTED:
           return SignalStatus.REJECTED
       return SignalStatus.ABSTAINED
   ```
   Import yang perlu ditambah di atas file: `ContextPack, build_context_pack` dari
   `rtrade.llm.context_pack`; `PipelineDecision, run_llm_pipeline` dari `rtrade.llm.pipeline`;
   `LLMClient` dari `rtrade.llm.client`; `SignalCandidate` dari `rtrade.signals.schemas`.
5. **HAPUS SELURUH blok lama** scan.py baris 451–479 (`# --- T16: LLM pipeline...` sampai
   `except Exception ... )`. Ganti dengan:
   ```python
   # --- LLM pipeline: Analyst → Critic → Verifier (F1) ---
   if cfg.settings.llm.enabled:
       pack = _build_pack(
           instrument, candidate, df_1h, sr_levels, gap_zones,
           regime, event_dicts, _session_active(instrument, now),
       )
       client = LLMClient(
           api_key=cfg.secrets.gemini_api_key_1,
           timeout=cfg.settings.llm.timeout_seconds,
           temperature=cfg.settings.llm.temperature,
       )
       pres = await run_llm_pipeline(
           candidate,
           pack,
           client,
           confidence_min=cfg.settings.signal.confidence_min,
           analyst_model=cfg.settings.llm.analyst_model,
           critic_model=cfg.settings.llm.critic_model,
       )
       status = _status_for_decision(pres.decision)
       if status != SignalStatus.PUBLISHED:
           await session_repo.add(
               _signal_model(
                   candidate,
                   instrument_id,
                   status=status,
                   confidence=Decimal(str(pres.confidence)),
                   payload={
                       "candidate": candidate.model_dump(mode="json"),
                       "llm": {
                           "decision": pres.decision.value,
                           "rationale": pres.rationale,
                           "key_risks": pres.key_risks,
                           "latency_ms": pres.pipeline_latency_ms,
                       },
                   },
               )
           )
           return ScanResult(
               symbol=instrument.symbol,
               timeframe=candidate.timeframe.value,
               status="rejected_llm" if status == SignalStatus.REJECTED else "abstained",
               signal_id=candidate.candidate_id,
               detail={"decision": pres.decision.value, "confidence": pres.confidence},
           )
       confidence = Decimal(str(pres.confidence))
       rationale = pres.rationale
       key_risks = pres.key_risks or key_risks
       sources = pres.sources or ["deterministic_pipeline"]
       llm_used = pres.llm_used
   ```
   CATATAN: variabel `confidence/rationale/key_risks/sources/llm_used` deterministik yang sudah
   dideklarasikan di atasnya (baris 445–449) tetap menjadi default ketika `llm.enabled: false`.
6. JANGAN set `llm.enabled: true` di settings.yaml (tetap false; user yang menyalakan).

**Test baru** (`tests/unit/test_scan_llm.py`):
- `test_status_for_decision_mapping()`: 4 assert — PUBLISH→PUBLISHED, FALLBACK→PUBLISHED,
  REJECTED→REJECTED, ABSTAIN→ABSTAINED.
- `test_build_pack_basic()`: df sintetis 250 bar (pakai pola pembuatan df dari
  `tests/unit/test_signals.py`) + candidate dummy valid → pack.source_ids non-kosong,
  `pack.candidate["entry_limit"] == candidate.levels.entry_limit`,
  `pack.instrument["symbol"] == "XAUUSD"`.

**BUKTI** (jalankan, tempel hasil di commit message):
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "run_llm_pipeline"   # >= 2 baris
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "format_candidate_deterministic\(\s*candidate" | Measure-Object  # stub lama (LLM prompt dari pesan telegram) sudah TIDAK ada di blok llm
```
**Commit**: `fix(llm): replace raw LLM stub with full Analyst→Critic→Verifier pipeline (F1)`

---

## F2 — Wiring T12: strategy state, audit trail, S2 hard-block, macro 12h

**File**: `src/rtrade/pipeline/scan.py` (+ `run_scan` yang membangun repo)

**Langkah** (semua di `_run_strategies`; tambah parameter
`state_repo: StrategyStateRepo, audit_repo: AuditRepo` — dibangun di `run_scan` dari `session`
yang sama dengan `SignalRepo`, diteruskan saat memanggil `_run_strategies`):
1. Setelah `strategy = strategy_cls()` dan cek regime, tambah:
   ```python
   if not await state_repo.is_enabled(strategy_name):
       logger.info("strategy disabled, skipping", strategy=strategy_name)
       continue
   ```
2. Setelah `strategy_cfg = _load_strategy_config(...)`:
   ```python
   hard_block_h = strategy_cfg.get_int("news.hard_block_hours", 0)
   if hard_block_h > 0 and high_impact_within(
       event_dicts, instrument.related_currencies, now, hours=hard_block_h
   ):
       logger.info("news hard-block, skipping strategy", strategy=strategy_name)
       continue
   ```
   (import `high_impact_within` dari `rtrade.risk.news_filter` — fungsinya SUDAH ADA.)
3. Pada `generate_candidate(...)` ganti `has_high_impact_event=in_news_blackout` menjadi:
   ```python
   has_high_impact_event=high_impact_within(
       event_dicts, instrument.related_currencies, now, hours=12
   ),
   ```
4. Setelah `if candidate is None: continue` → audit kandidat:
   ```python
   await audit_repo.add(
       stage=AuditStage.CANDIDATE.value,
       ok=True,
       signal_id=candidate.candidate_id,
       detail={
           "symbol": instrument.symbol,
           "strategy": candidate.strategy,
           "confluence": candidate.confluence_score,
       },
   )
   ```
   (import `AuditStage` dari `rtrade.core.constants`, `AuditRepo, StrategyStateRepo` dari
   `rtrade.persistence.repositories`.)
5. Setelah `gate = run_gate(...)` → audit gate:
   ```python
   await audit_repo.add(
       stage=AuditStage.GATE.value,
       ok=gate.passed,
       signal_id=candidate.candidate_id,
       detail={"failures": [f"{f.gate_id}: {f.reason}" for f in gate.failures]},
   )
   ```
6. Di dalam blok `if not gate.passed:`, SEBELUM `return`:
   ```python
   if any(f.gate_id == "GR-13" for f in gate.failures):
       await state_repo.set_state(
           candidate.strategy, enabled=False, reason="GR-13 negative expectancy"
       )
   ```

**Test baru** (`tests/unit/test_news_filter.py`, tambah jika belum ada dari pekerjaan lalu):
- `test_high_impact_within_12h_true_inside_false_outside()` (lihat spesifikasi T12 lama).

**BUKTI**:
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "is_enabled|set_state|AuditStage|high_impact_within" | Measure-Object   # Count >= 6
```
**Commit**: `feat(pipeline): wire strategy state, audit trail, S2 hard-block, 12h macro (F2)`

---

## F3 — Wiring T13: delivery jujur + alert scan gagal

**Langkah**:
1. `src/rtrade/delivery/telegram_bot.py` — `send_signal` return `bool`
   (spesifikasi PERSIS di IMPLEMENTATION_TASKS.md → T13 langkah 1).
2. `src/rtrade/pipeline/scan.py` — blok delivery di `run_scan`:
   ```python
   sent = await telegram.send_signal(result.message)
   ```
   lalu setelah `finally: await telegram.close()`, buka session baru:
   ```python
   async with session_factory() as session:
       await SignalRepo(session).mark_delivery(
           result.signal_id or "",
           sent=sent,
           error=None if sent else "telegram send failed",
           at=datetime.now(UTC),
       )
       await AuditRepo(session).add(
           stage=AuditStage.DELIVERY.value,
           ok=sent,
           signal_id=result.signal_id,
           detail={"sent": sent},
       )
       await session.commit()
   ```
   (method `mark_delivery` SUDAH ADA di repositories.py:281 — tinggal dipanggil.)
3. `src/rtrade/scheduler/jobs.py` — bungkus `scan_job` dengan alert
   (spesifikasi PERSIS di T13 langkah 4: `_scan_failures` dict, `_get_alerts()`, try/except).

**Test**: spesifikasi T13 lama (`test_delivery_status.py` + tambahan test_alerts).

**BUKTI**:
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "mark_delivery"           # >= 1
Select-String -Path src/rtrade/scheduler/jobs.py -Pattern "alert_scan_failed"      # >= 1
Select-String -Path src/rtrade/delivery/telegram_bot.py -Pattern "-> bool"         # >= 1 (send_signal)
```
**Commit**: `feat(ops): wire honest delivery status + scan failure alerts (F3)`

---

## F4 — Wiring T19: grading masuk publish path + Telegram

**File**: `src/rtrade/signals/grading.py`, `src/rtrade/pipeline/scan.py`,
`src/rtrade/delivery/formatter.py`

**Langkah**:
1. `grading.py` — tambah konstanta + helper di bawah `GradeResult`:
   ```python
   RISK_MULT: dict[Grade, float] = {Grade.A: 1.0, Grade.B: 0.5, Grade.C: 0.25}


   def risk_multiplier(grade: Grade) -> float:
       return RISK_MULT[grade]
   ```
2. `scan.py` — di publish path (setelah blok LLM F1, sebelum membuat `TradingSignal`):
   ```python
   grade_res = grade_signal(
       confluence_score=candidate.confluence_score,
       regime_match=True,  # strategi sudah digerbangi regime di atas
       edge_quality_score=None,
       has_high_impact_event=high_impact_within(
           event_dicts, instrument.related_currencies, now, hours=12
       ),
       confidence=float(confidence),
   )
   ```
   lalu setelah `signal.model_dump(mode="json")` → sisipkan ke payload sebelum `session_repo.add`:
   ```python
   payload = signal.model_dump(mode="json")
   payload["grade"] = {
       "grade": grade_res.grade.value,
       "reasons": grade_res.reasons,
       "risk_mult": risk_multiplier(grade_res.grade),
       "scaled_size": round(candidate.position_size * risk_multiplier(grade_res.grade), 4),
   }
   ```
   (ubah `_signal_model(... payload=...)` agar menerima `payload` variabel ini.)
3. `formatter.py` — `format_signal_telegram` dan `format_candidate_deterministic` tambah
   parameter opsional `grade: str | None = None, scaled_size: float | None = None`; jika tidak
   None, sisipkan SETELAH baris Confidence:
   ```python
   if grade is not None:
       lines.insert(
           7, f"Grade       : {grade}" + (f"  ·  size saran {scaled_size}" if scaled_size else "")
       )
   ```
   (insert index 7 = setelah baris Confidence; verifikasi manual urutan `lines`.)
   `scan.py` meneruskan `grade=grade_res.grade.value, scaled_size=payload["grade"]["scaled_size"]`
   ke pemanggilan formatter.

**Test**: `tests/unit/test_grading.py` tambah `test_risk_multiplier()`; test formatter
`test_message_contains_grade()` (buat TradingSignal dummy, panggil format dengan grade="A" →
`"Grade" in message`).

**BUKTI**:
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "grade_signal"   # >= 1
Select-String -Path src/rtrade/delivery/formatter.py -Pattern "grade"     # >= 2
```
**Commit**: `feat(signals): wire A/B/C grading into publish path and Telegram (F4)`

---

## F5 — Perbaiki cascade: eskalasi di LEVEL PIPELINE, bukan regex teks

**Masalah desain cascade.py sekarang**: ekstraksi confidence pakai regex dari teks mentah (rapuh),
dan eskalasi hanya mengulang 1 call — bukan pipeline penuh. Confidence final sistem ini dihitung
DETERMINISTIK oleh `compute_confidence()`, bukan oleh teks LLM.

**Langkah**:
1. `core/config.py` — tambah ke `LLMSettings`:
   ```python
   escalation_low: float = Field(default=0.48, ge=0.0, le=1.0)
   escalation_high: float = Field(default=0.63, ge=0.0, le=1.0)
   ```
2. **TULIS ULANG** `src/rtrade/llm/cascade.py` — hapus `cascade_complete` dan `_extract_confidence`,
   ganti dengan:
   ```python
   """LLM cascade — escalate uncertain pipeline results to the flagship model.

   Tier 1 (cheap model) runs the full Analyst→Critic→Verifier pipeline. Only when
   the DETERMINISTIC confidence lands in the doubt band do we pay for the flagship
   re-run. VETO/REJECTED is never escalated (a firm no stays no).
   """

   from __future__ import annotations

   from rtrade.llm.pipeline import PipelineDecision, PipelineResult


   def should_escalate(
       result: PipelineResult,
       *,
       low: float,
       high: float,
       flagship_model: str,
   ) -> bool:
       """True only for uncertain PUBLISH/ABSTAIN results with a flagship configured."""
       if not flagship_model:
           return False
       if result.decision not in (PipelineDecision.PUBLISH, PipelineDecision.ABSTAIN):
           return False
       return low <= result.confidence <= high
   ```
3. `pipeline/scan.py` — di blok LLM (hasil F1), setelah mendapat `pres` pertama:
   ```python
   if should_escalate(
       pres,
       low=cfg.settings.llm.escalation_low,
       high=cfg.settings.llm.escalation_high,
       flagship_model=cfg.settings.llm.flagship_model,
   ):
       logger.info("escalating to flagship", confidence=pres.confidence)
       pres = await run_llm_pipeline(
           candidate,
           pack,
           client,
           confidence_min=cfg.settings.signal.confidence_min,
           analyst_model=cfg.settings.llm.flagship_model,
           critic_model=cfg.settings.llm.flagship_model,
       )
   ```
   (import `should_escalate` dari `rtrade.llm.cascade`.)
4. **TULIS ULANG** `tests/unit/test_cascade.py` untuk `should_escalate`:
   - dalam pita (0.55) → True; di bawah (0.40) / di atas (0.80) → False;
   - decision REJECTED dengan confidence 0.55 → False;
   - flagship_model "" → False.
   (bangun `PipelineResult` langsung — dataclass frozen, semua field bisa diisi manual.)

**BUKTI**:
```powershell
Select-String -Path src/rtrade/pipeline/scan.py -Pattern "should_escalate"   # >= 1
Select-String -Path src/rtrade/llm/cascade.py -Pattern "_extract_confidence" # 0 baris
```
**Commit**: `fix(llm): cascade escalates full pipeline on doubt band, drop regex parsing (F5)`

---

## F6 — Wiring T18: smart exit masuk `run_backtest`

**File**: `src/rtrade/backtest/engine.py` (+ `smart_exit.py` bila perlu penyesuaian)

**Langkah**:
1. Tambah parameter `smart_exit: SmartExitConfig | None = None` pada `run_backtest()`.
   `None` → perilaku lama BYTE-IDENTIK (semua test lama harus tetap hijau).
2. Saat `smart_exit` di-set, fase exit per trade memakai logika `apply_smart_exit`/`ExitState`
   dari `rtrade.backtest.smart_exit`. Kontrak hasil WAJIB memenuhi 4 skenario uji di bawah —
   kalau API modul yang ada tidak pas, REFAKTOR modulnya (testnya yang jadi kontrak, bukan
   implementasi lama):
   - Urutan worst-case per bar: cek SL dulu → partial → TP → update trailing (berlaku bar berikut).
   - Breakeven aktif MULAI BAR BERIKUTNYA setelah partial.
   - R akhir = `realized_r + size_remaining × exit_r`, pembagi SELALU jarak SL AWAL.
3. **Test integrasi baru** `tests/unit/test_smart_exit_engine.py` (BUY entry=100, SL=98, TP=104,
   partial 0.5 @1R, breakeven on):
   - `test_none_is_backward_compatible`: hasil identik tanpa smart_exit.
   - `test_partial_be_then_sl`: bar naik ke 102 lalu jatuh ke 98 → r = +0.5.
   - `test_partial_then_tp`: 102 lalu 104 → r = 1.5.
   - `test_sl_same_bar_worst_case`: bar low 97.9 & high 102.5 → r = −1.0.

**BUKTI**:
```powershell
Select-String -Path src/rtrade/backtest/engine.py -Pattern "SmartExitConfig"  # >= 2
```
**Commit**: `feat(backtest): run_backtest supports SmartExitConfig exit policies (F6)`

---

## F7 — Telegram polling entrypoint + command nyata (pelunasan janji bot)

**File**: `src/rtrade/delivery/telegram_bot.py`, baru: `src/rtrade/cli/bot.py`

**Langkah**:
1. `telegram_bot.py` — `/enable_strategy` HARUS benar-benar mengubah DB:
   constructor menerima opsional `session_factory: async_sessionmaker | None = None`; handler:
   ```python
   if self._session_factory is None:
       await message.answer("DB tidak terkonfigurasi.")
       return
   async with self._session_factory() as session:
       await StrategyStateRepo(session).set_state(strategy_name, enabled=True, reason="manual /enable_strategy")
       await session.commit()
   await message.answer(f"✅ Strategi {strategy_name} diaktifkan kembali.")
   ```
   `/signals` → query 5 sinyal terakhir via `SignalRepo.recent(5)` dan format ringkas
   (`symbol action status entry`); `/calibration` → hitung dari Signal 30 hari
   (copy logika dari `delivery/api/routes.py::calibration`); `/status` → pakai `HealthChecker`.
2. `src/rtrade/cli/bot.py` — entrypoint polling:
   ```python
   """Run Telegram bot polling: python -m rtrade.cli.bot"""
   import asyncio

   from rtrade.core.config import AppConfig
   from rtrade.delivery.telegram_bot import TelegramDelivery
   from rtrade.persistence.db import create_engine, create_session_factory


   def main() -> None:
       cfg = AppConfig.load()
       engine = create_engine(cfg.secrets.database_url)
       bot = TelegramDelivery(
           cfg.secrets.telegram_bot_token,
           cfg.secrets.telegram_chat_id,
           session_factory=create_session_factory(engine),
       )
       asyncio.run(bot.start_polling())


   if __name__ == "__main__":
       main()
   ```

**Test**: handler diuji ringan — `/enable_strategy` tanpa session_factory menjawab pesan error
(monkeypatch `message.answer` perekam). Jangan test polling.

**BUKTI**: `Select-String -Path src/rtrade/delivery/telegram_bot.py -Pattern "StrategyStateRepo"` ≥ 1.
**Commit**: `feat(telegram): real bot commands + polling entrypoint (F7)`

---

# MILESTONE 4 — GELOMBANG BERIKUTNYA (T20–T30). Mulai HANYA setelah F1–F7 + BUKTI lengkap.

## T20 — Wire derivatives: funding rate + OI → confluence & context pack

1. `pipeline/scan.py` `run_scan`: untuk instrumen `derivatives: true` dan provider ccxt:
   ```python
   derivatives_data: dict[str, Any] | None = None
   funding_extreme = False
   if instrument.derivatives and isinstance(provider, CcxtProvider):
       try:
           funding = await provider.fetch_funding_rate(instrument.provider_symbol)
           oi = await provider.fetch_open_interest(instrument.provider_symbol)
           rate = float(funding.funding_rate)
           funding_extreme = abs(rate) >= 0.0005  # |0.05%| per 8h ≈ ekstrem
           derivatives_data = {
               "funding_rate": rate,
               "funding_extreme_flag": funding_extreme,
               "oi_change_24h": None,
               "open_interest": float(oi.open_interest),
           }
       except ProviderError as exc:
           logger.warning("derivatives fetch failed", error=str(exc))
   ```
   Simpan snapshot: `session.add(DerivativesSnapshot(instrument_id=inst_row.id, ts=now,
   funding_rate=funding.funding_rate, open_interest=oi.open_interest))` (import model).
2. Teruskan `funding_extreme=funding_extreme` ke `_run_strategies` → `generate_candidate(...)`
   (ganti `funding_extreme=False` hardcode), dan `derivatives_data` → `_build_pack` →
   `build_context_pack(derivatives=derivatives_data, ...)` (ubah signature `_build_pack`).
3. Threshold `0.0005` taruh sebagai konstanta module `FUNDING_EXTREME_ABS = 0.0005` dgn komentar.

**Test**: pure helper `_is_funding_extreme(rate: float) -> bool` (ekstrak) — 0.0006→True,
−0.0006→True, 0.0001→False.
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "fetch_funding_rate"` ≥ 1.
**Commit**: `feat(data): wire funding/OI into confluence, pack, and snapshots (T20)`

## T21 — Live spread → edge quality (EQ-02 hidup)

1. `data/base.py` — tambah method opsional di `MarketDataProvider`:
   ```python
   async def fetch_spread(self, symbol: str) -> float | None:
       """Bid/ask spread in price units; None when unsupported."""
       return None
   ```
   (BUKAN abstract — default None agar subclass lama tidak wajib.)
2. `ccxt_provider.py` — override: `fetch_ticker`, `spread = ask - bid` bila keduanya ada,
   else None. Rate-limit bucket sama.
3. `twelvedata_provider.py` — TIDAK override (free tier tidak andal) → None.
4. `scan.py` — sebelum `_run_strategies`:
   ```python
   spread: float | None = None
   try:
       spread = await provider.fetch_spread(instrument.provider_symbol)
   except Exception as exc:  # spread bersifat best-effort
       logger.warning("spread fetch failed", error=str(exc))
   ```
   teruskan `spread=spread` → `generate_candidate(..., spread=spread)` (parameter SUDAH ADA).
   Catat juga ke payload audit kandidat (`detail={"spread": spread, ...}`).
5. Fallback statis: jika `spread is None` dan market bukan crypto, hitung dari costs.yaml:
   `spread = cost_model.spread_pips_rt * pip_size / 2`? — TIDAK. Jangan estimasi; biarkan None
   (EQ-02 nonaktif untuk forex sampai ada sumber nyata). Tulis komentar TODO yang jelas.

**Test**: ccxt fetch_spread dengan ticker mock (monkeypatch `_exchange.fetch_ticker` return
`{"bid": 100.0, "ask": 100.2}`) → 0.2 (approx); bid None → None.
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "fetch_spread"` ≥ 1.
**Commit**: `feat(data): live spread wired into edge-quality EQ-02 (T21)`

## T22 — Timeframe M1 + resolusi ambiguitas 1 menit (replay jujur)

1. `core/constants.py` — tambah member enum (ADITIF, jangan ubah yang lama):
   `M1 = "1m"`, `M5 = "5m"`, `M15 = "15m"`.
   `core/timeutil.py` `_TIMEFRAME_DURATION` += M1/M5/M15. `_TF_MAP` di ccxt & twelvedata += M1
   (`"1m"` / `"1min"`), M5 (`"5m"`/`"5min"`), M15 (`"15m"`/`"15min"`).
2. `papertrack/tracker.py` — saat replay menemukan bar AMBIGU (SL dan TP tersentuh di bar yang
   sama, atau fill+SL satu bar), JANGAN langsung worst-case kalau resolusi tersedia:
   - Ubah `replay_signal` agar menerima parameter opsional
     `resolve_minute: Callable[[datetime, datetime], list[CandleBar]] | None = None`
     (async tidak perlu — lihat poin 3).
   - Pada bar ambigu: jika `resolve_minute` None → worst-case (perilaku sekarang).
     Jika ada → panggil `resolve_minute(bar_open_ts, bar_close_ts)` → dapat list bar 1m →
     telusuri berurutan: mana yang tersentuh duluan (SL atau TP) → hasil eksak.
     List kosong → worst-case.
   - Tandai hasil: `PaperTradeUpdate` tambah field `resolution: str = "bar"`
     (`"bar"` | `"minute"` | `"worst_case"`).
3. Karena `replay_signal` sync dan fetch async: di `track_paper_signals` (scan.py), PRE-FETCH —
   jalankan replay pertama TANPA resolver; jika hasilnya SL_HIT/TP_HIT dengan `worst_case` dan
   instrumen crypto, fetch 1m candle range bar ambigu via provider
   (`fetch_ohlcv(symbol, Timeframe.M1, since=bar_open, limit=70)`), bangun fungsi resolver dari
   list itu (closure dict), jalankan replay KEDUA dengan resolver. Forex/metals: tetap worst-case
   (hemat credit TwelveData) — komentar TODO on-demand.
4. Simpan `resolution` ke payload signal (`payload["resolution"]`).

**Test** (`tests/unit/test_minute_resolution.py`):
- Bar 1H ambigu (low<SL, high>TP). Resolver mengembalikan 1m bars: TP tersentuh di menit 10,
  SL menit 40 → hasil TP_HIT, resolution="minute".
- Mirror: SL duluan → SL_HIT.
- Resolver return [] → SL_HIT worst_case.
**Commit**: `feat(papertrack): 1-minute exact resolution for ambiguous bars (T22)`

## T23 — Virtual exit ensemble (1 entry = N exit bayangan)

1. `papertrack/tracker.py` — fungsi pure baru:
   ```python
   def evaluate_virtual_exits(
       action: str,
       entry: float,
       stop_loss: float,
       take_profit: float,
       atr_at_signal: float,
       candles: list[CandleBar],
   ) -> dict[str, dict[str, object]]:
       """Hasil per kebijakan exit: fixed_2r, partial_be, time_stop_12, wide_tp_3r."""
   ```
   Kebijakan v1 (semua dievaluasi dari candle yang sama, worst-case SL-first per bar):
   - `fixed_2r`: SL/TP apa adanya (baseline — harus sama dgn outcome nyata).
   - `partial_be`: 50% @ +1R lalu SL→entry; sisa ke TP. R = 0.5×1 + 0.5×(hasil sisa).
   - `time_stop_12`: jika belum kena SL/TP setelah 12 bar → exit di close bar ke-12
     (`CandleBar` perlu field `close: float` — TAMBAHKAN field ini; update pemanggil scan).
   - `wide_tp_3r`: TP digeser ke entry±3R (SL tetap).
   Return per kebijakan: `{"status": "TP_HIT|SL_HIT|OPEN|TIME_EXIT", "outcome_r": float | None}`.
2. `scan.py track_paper_signals` — saat sinyal RESOLVED (status berubah ke TP/SL/EXPIRED),
   panggil `evaluate_virtual_exits` dgn candle range yang sama; simpan ke
   `payload["virtual_exits"]` via pola `mark_delivery` (read-modify-write payload —
   buat method generik `SignalRepo.merge_payload(signal_id, key, value)`).
3. Endpoint laporan: `GET /analytics/exits` di routes.py — agregasi rata-rata `outcome_r` per
   kebijakan dari semua signal yang punya `payload.virtual_exits` (loop Python biasa, tidak perlu
   SQL JSONB rumit).

**Test**: 4 kebijakan × skenario (naik 1R lalu jatuh ke SL → fixed=−1, partial_be=+0.5;
langsung TP → fixed=+2, wide_tp masih OPEN; 12 bar datar → time_stop exit ~0R).
**Commit**: `feat(papertrack): virtual exit ensemble per filled signal (T23)`

## T24 — MAE/MFE capture + analytics

1. `tracker.py` — `replay_signal` (atau fungsi pembungkusnya) sambil jalan hitung:
   MAE = ekskursi terburuk dalam R sejak fill; MFE = terbaik dalam R. Return via field baru
   `PaperTradeUpdate.mae_r: float | None = None`, `mfe_r: float | None = None`
   (hitung hanya bila fill terjadi).
2. `scan.py` — simpan ke payload (`payload["excursion"] = {"mae_r": ..., "mfe_r": ...}`)
   dan kolom? TIDAK — payload saja (tanpa migrasi).
3. `GET /analytics/excursion` — per strategi: rata-rata MAE losers vs winners + saran kasar
   ("SL bisa diketatkan ke X×ATR" = persentil-90 MAE winners; HANYA tampilkan angka, tanpa
   auto-tuning).

**Test**: BUY entry 100, SL 98 (risk 2): path 99 → 103 → TP: MAE = −0.5R, MFE = +1.5R… (hitung
manual, assert approx).
**Commit**: `feat(analytics): MAE/MFE excursion capture and report (T24)`

## T25 — Permutation test (kalahkan keberuntungan dulu)

1. File baru `src/rtrade/backtest/permutation.py`:
   ```python
   def permutation_pvalue(
       r_multiples: list[float],
       n_bars: int,
       n_permutations: int = 1000,
       seed: int = 42,
   ) -> float:
       """P(expectancy acak >= expectancy nyata) via bootstrap penempatan acak.

       Pendekatan v1 (sederhana & jujur): tiap permutasi mengambil sampel acak
       len(r) outcome dari distribusi {+rr, -1} dgn winrate teracak binomial p=base_wr
       TIDAK — itu salah. Gunakan: resample dengan penggantian dari r_multiples
       yang TANDANYA diacak ulang (sign-flip test) — H0: tidak ada edge arah.
       p = proporsi permutasi dengan mean >= mean asli.
       """
   ```
   Implementasi: numpy RNG(seed); 1000×: `signs = rng.choice([1,-1], size=n)`,
   `perm_mean = np.mean(np.abs(r) * signs)`; p = mean(perm_mean >= actual_mean).
2. Integrasi: `backtest/validation.py run_validation_gates` tambah gate opsional
   `permutation_p: float | None = None` → `gates["permutation_p <= 0.05"]` bila diberikan.

**Test**: r 30 trade semuanya +2 → p < 0.01; r acak simetris (sama banyak +1/−1) → p > 0.2;
seed deterministik (2× panggil = hasil sama).
**Commit**: `feat(validation): sign-flip permutation test gate (T25)`

## T26 — Bayesian Kelly (lower-bound posterior)

1. `risk/sizing.py` — fungsi baru:
   ```python
   def bayesian_kelly_fraction(
       wins: int,
       losses: int,
       avg_win_r: float,
       avg_loss_r: float,
       *,
       fraction: float = 0.25,
       credible_quantile: float = 0.25,
   ) -> float | None:
       """Kelly dari batas bawah kredibel winrate: Beta(wins+1, losses+1).ppf(quantile).

       scipy.stats.beta.ppf — scipy sudah ada di dependencies. Return None jika
       hasil <= 0 atau total sampel < 30.
       """
   ```
2. Wire: `scan.py` publish path — jika resolved paper trades strategi ≥ 30
   (query `recent_outcomes` limit 200): hitung wins/losses/avg dan masukkan
   `payload["kelly"] = {"bayes_fraction": ..., "n": ...}` (informasional, TIDAK mengubah sizing).

**Test**: wins=60, losses=40, avg_win 2, avg_loss 1 → fraction antara 0 dan full-Kelly mean;
wins=5, losses=3 (n<30) → None; wins=10, losses=90 → None (negatif).
**Commit**: `feat(risk): Bayesian lower-bound Kelly suggestion (T26)`

## T27 — Equity-curve risk throttle (deterministik)

1. `core/config.py` `RiskSettings` += `throttle_enabled: bool = True`,
   `throttle_window: int = Field(default=10, ge=5)`,
   `throttle_mult: float = Field(default=0.5, gt=0.0, lt=1.0)`.
   settings.yaml += 3 key di `risk:`.
2. `risk/limits.py` — fungsi pure:
   ```python
   def throttled_risk_pct(
       base_risk_pct: float,
       recent_outcomes: list[float],
       *,
       window: int,
       mult: float,
   ) -> float:
       """Risk dikalikan mult bila rolling expectancy window terakhir < 0."""
   ```
3. `scan.py` — sebelum `generate_candidate`: `risk_pct = throttled_risk_pct(...)` pakai
   `paper_outcomes`… HATI-HATI: `paper_outcomes` di-query SETELAH candidate dibuat. PINDAHKAN
   query `recent_outcomes` ke SEBELUM `generate_candidate` dan pakai untuk keduanya.

**Test**: 10 outcome rata-rata −0.2 → risk 1.0 jadi 0.5; positif → tetap 1.0;
data < window → tetap 1.0; hasil tidak pernah > base.
**BUKTI**: `Select-String -Path src/rtrade/pipeline/scan.py -Pattern "throttled_risk_pct"` ≥ 1.
**Commit**: `feat(risk): equity-curve risk throttle (T27)`

## T28 — HMM regime shadow mode

1. `scan.py run_scan` — setelah `regime = RegimeClassifier()...`, blok try best-effort:
   ```python
   try:
       hmm_state = _hmm_shadow_classify(symbol, df_1h)   # None bila model belum ada
       if hmm_state is not None:
           await AuditRepo(session).add(
               stage="regime_shadow", ok=hmm_state.regime == regime.regime,
               detail={"rule": regime.regime.value, "hmm": hmm_state.regime.value,
                       "prob": hmm_state.probability},
           )
   except Exception as exc:
       logger.warning("hmm shadow failed", error=str(exc))
   ```
   CATATAN: stage di sini string literal `"regime_shadow"` — TAMBAHKAN member enum
   `AuditStage.REGIME_SHADOW = "regime_shadow"` (aditif, aman).
2. `_hmm_shadow_classify` (module-level scan.py): muat model dari path `models/hmm_{symbol}.joblib`
   bila ada (cache dict module-level), train TIDAK dilakukan di scan.
3. Job training mingguan: `scheduler/jobs.py` + `main.py` — `hmm_train_job()` (Minggu 02:00 UTC):
   per instrumen ambil 5000 bar 1H dari DB → `HMMRegimeDetector().train(df)` → simpan joblib ke
   `models/`. (`joblib` sudah terinstal sebagai dependency sklearn.)

**Test**: `_hmm_shadow_classify` return None bila file model tidak ada (tmp_path);
training job logic di-unit-test lewat fungsi pure `_train_hmm_for_df(df) -> bytes`? — cukup
test bahwa `HMMRegimeDetector` train+classify jalan di df sintetis 600 bar (sudah ada
test_hmm_regime.py — tambah test save/load joblib roundtrip).
**Commit**: `feat(regime): HMM shadow classification with weekly training job (T28)`

## T29 — Case-based memory: setup serupa historis masuk context pack

1. `persistence/repositories.py` — `SignalRepo.resolved_with_features(strategy, limit=500)`:
   sinyal status TP_HIT/SL_HIT dgn payload, return list dict
   `{confluence breakdown..., outcome_r, bar_ts}` (ambil dari payload["candidate"]).
2. File baru `src/rtrade/ml/similar.py`:
   ```python
   def find_similar_setups(
       current: dict[str, float],   # keys: trend, momentum, structure, volume, macro, hour
       history: list[dict[str, Any]],
       k: int = 12,
   ) -> dict[str, Any]:
       """k-NN Euclidean ternormalisasi → {"n": k, "wins": x, "losses": y,
       "win_rate": .., "avg_r": ..} atau {"n": 0} bila history < 30."""
   ```
   Normalisasi: bagi tiap fitur dgn rentang maksnya (trend/25, momentum/20, structure/20,
   volume/15, macro/20, hour/23).
3. `context_pack.py` — `build_context_pack` += parameter
   `similar_setups: dict[str, Any] | None = None`, masuk `to_dict()` dgn source_id
   `mem:similar:{symbol}:{tf}:{bar_ts}` (tambah ke source_ids bila tidak None).
4. `scan.py _build_pack` — panggil `resolved_with_features` + `find_similar_setups` dan teruskan.
   History < 30 → None (jangan kirim noise ke LLM).

**Test**: history sintetis 50 setup (30 mirip-win, 20 beda-loss) → query mirip kelompok pertama →
win_rate > 0.7; history 10 → `{"n": 0}` dan pack tanpa field similar.
**Commit**: `feat(llm): case-based memory — similar historical setups in context pack (T29)`

## T30 — LLM Coroner: otopsi otomatis setiap SL_HIT

1. `core/config.py` `LLMSettings` += `coroner_enabled: bool = False`.
   settings.yaml `llm:` += `coroner_enabled: false`.
2. File baru `src/rtrade/llm/coroner.py`:
   - Taksonomi tetap: `FAILURE_MODES = ("false_breakout", "news_spike", "regime_flip",
     "sl_too_tight", "bad_fill", "unknown")`.
   - Schema pydantic `CoronerReport(failure_mode: str (pattern join taksonomi),
     explanation_id: str (min 30), confidence: float 0..1)`.
   - `async def run_coroner(client, *, model, candidate_payload: dict, price_path: list[dict])
     -> CoronerReport` — system prompt Bahasa Indonesia: "klasifikasikan sebab SL ke salah satu
     taksonomi; JANGAN menyebut angka di luar data"; user prompt = JSON candidate + 12 bar
     OHLC sesudah fill (dari DB, bukan provider).
3. `scan.py track_paper_signals` — saat update SL_HIT dan `coroner_enabled` dan `llm.enabled`:
   panggil run_coroner (try/except best-effort), simpan `payload["coroner"]` via merge_payload.
4. `GET /analytics/failures` — hitung distribusi failure_mode per strategi.

**Test**: schema validasi menolak failure_mode di luar taksonomi; `run_coroner` dgn client mock
(monkeypatch complete → JSON valid) return report; scan path tidak meledak saat coroner exception.
**Commit**: `feat(llm): coroner — automatic SL post-mortem classification (T30)`

---

## CHECKLIST AKHIR (sebelum lapor selesai)
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format --check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
git log --oneline -25   # harus terlihat >= 17 commit baru (baseline + F1..F7 + T20..T30)
```
Lalu jalankan SEMUA perintah BUKTI dari tiap task F/T dan sertakan outputnya di laporan.
Laporan akhir per task: status, deviasi (jika ada), nama test baru.
