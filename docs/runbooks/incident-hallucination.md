# Runbook: Incident — Halusinasi LLM

## Definisi

Halusinasi terjadi ketika LLM menghasilkan informasi yang tidak sesuai fakta dalam output pipeline sinyal.

## Klasifikasi

| Jenis | Severity | Contoh | Dampak |
|-------|----------|--------|--------|
| **GR-10 Violation** | CRITICAL | LLM mengubah entry/SL/TP | Angka sinyal SALAH → harus impossible (arsitektur bug) |
| **Sumber palsu** | High | Source ID tidak ada di context pack | Rationale tidak bisa diverifikasi |
| **Symbol salah** | High | Analisis EURUSD tapi context XAUUSD | Sinyal sepenuhnya salah |
| **Fakta salah** | Medium | "RSI oversold" padahal RSI 65 | Rationale menyesatkan |
| **Overconfidence** | Low | Confidence 0.9 untuk setup lemah | Kalibrasi melenceng |

## Deteksi

### Otomatis (Verifier — GR-09, 10, 11)
- Cek harga di output = harga di LevelSet (GR-10)
- Cek symbol konsisten (GR-11)
- Cek source_id valid (GR-09)
- **Jika terdeteksi → sinyal ABSTAIN otomatis**

### Manual
- Review `signal_audits` di database
- Cek ratio ABSTAIN vs PUBLISHED

## Prosedur Penanganan

### 1. Pelanggaran GR-10 (CRITICAL — Bug Arsitektur)

> **Ini SEHARUSNYA TIDAK TERJADI.** Angka sinyal dihitung deterministik
> dan di-inject ke LLM output oleh verifier. Jika GR-10 terlanggar,
> ada bug di kode, bukan di LLM.

```bash
# IMMEDIATE: Stop pipeline
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop app

# Audit: Cari semua sinyal yang terlanggar
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT signal_id, stage, detail
    FROM signal_audits
    WHERE ok = false AND detail::text LIKE '%GR-10%'
    ORDER BY created_at DESC LIMIT 20;
  "
```

**Setelah fix:**
1. Fix bug di verifier/gate
2. Deploy ulang
3. Re-run eval halusinasi (`scripts/eval_hallucination.py`)
4. Pastikan trap pack abstain rate ≥80%
5. Tulis ADR menjelaskan bug dan fix

### 2. Halusinasi Non-GR-10

```bash
# Check audit trail
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT sa.signal_id, sa.stage, sa.ok, sa.detail->>'reason' as reason
    FROM signal_audits sa
    WHERE sa.ok = false
    ORDER BY sa.created_at DESC LIMIT 20;
  "

# Check abstain rate
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT status, COUNT(*) as count
    FROM signals
    WHERE created_at > NOW() - INTERVAL '7 days'
    GROUP BY status;
  "
```

### 3. Perbaikan Prompt

Jika halusinasi pola baru terdeteksi:

1. Tambahkan kasus ke trap pack (`scripts/eval_hallucination.py`)
2. Update prompt di `src/rtrade/llm/prompts/`
3. Re-run eval: `uv run python scripts/eval_hallucination.py`
4. Pastikan abstain rate ≥80% pada trap pack
5. Deploy prompt baru

## Metrik Kalibrasi

Cek mingguan:

```bash
# Confidence calibration
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT
      CASE
        WHEN confidence BETWEEN 0.55 AND 0.65 THEN '0.55-0.65'
        WHEN confidence BETWEEN 0.65 AND 0.75 THEN '0.65-0.75'
        WHEN confidence > 0.75 THEN '>0.75'
      END as bucket,
      COUNT(*) as total,
      SUM(CASE WHEN status = 'TP_HIT' THEN 1 ELSE 0 END) as wins,
      ROUND(SUM(CASE WHEN status = 'TP_HIT' THEN 1 ELSE 0 END)::numeric
            / NULLIF(COUNT(*), 0) * 100, 1) as win_rate_pct
    FROM signals
    WHERE status IN ('TP_HIT', 'SL_HIT')
    GROUP BY 1 ORDER BY 1;
  "
```

**Target**: confidence 0.70 → win rate ~70% (±15 poin).

## Prinsip

- **GR-10 violation = arsitektur bug** → pipeline STOP sampai fix
- **Abstain rate tinggi = BAGUS** → LLM yang ragu-ragu lebih baik dari LLM yang percaya diri tapi salah
- **Negative result = valid** → jika LLM tidak meningkatkan kualitas sinyal, matikan dan gunakan deterministic-only
