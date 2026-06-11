# ADR-002 — Engine sinyal kustom dengan pola callback Freqtrade

**Status:** ACCEPTED (2026-06-11)

## Context
Riset merekomendasikan Freqtrade, tetapi Freqtrade tergandeng erat ke exchange crypto via ccxt — XAUUSD/forex (TwelveData/MT5) tidak didukung native. Dua engine paralel = duplikasi logika. NautilusTrader dirancang untuk presisi eksekusi yang tidak dibutuhkan produk signal-only.

## Decision
Bangun `rtrade.strategies.base.Strategy` dengan interface meniru callback Freqtrade (`populate_indicators`, `entry_signal`, `custom_entry_price`, `confirm_signal`) — satu engine untuk 3 pasar, data provider pluggable.

## Consequences
- Kualitas backtester menjadi tanggung jawab kita → diimbangi test anti-look-ahead ketat (PLAN §12.3) dan validasi silang S1-crypto terhadap Freqtrade dry-run sebagai golden reference (P1-T11).
