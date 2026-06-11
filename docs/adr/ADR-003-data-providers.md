# ADR-003 — Data provider: ccxt, TwelveData, Finnhub, Binance Futures public

**Status:** ACCEPTED (2026-06-11)

## Decision
- Crypto OHLCV: ccxt (Binance public, tanpa API key).
- XAUUSD + forex: TwelveData (free tier: 8 req/min, 800 req/hari).
- Kalender ekonomi: Finnhub → fallback Trading Economics.
- Funding/OI crypto: Binance Futures public REST.
Semua di balik interface abstrak `MarketDataProvider` / `CalendarProvider` / `DerivativesProvider`.

## Consequences
Provider bisa ditukar tanpa menyentuh engine. Scheduler harus hemat request (token bucket Redis). Jika provider berubah/berbayar → implementasi alternatif lewat interface + ADR baru, bukan penggantian diam-diam.
