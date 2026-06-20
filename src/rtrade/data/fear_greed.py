"""Crypto Fear & Greed index provider (alternative.me, keyless).

Low-dependency async httpx JSON client for the public alternative.me Fear & Greed
endpoint (https://api.alternative.me/fng/). No API key required; usage is
permitted under alternative.me's published Terms of Service (attribution to
alternative.me). No third-party GPL/AGPL code copied (GI-4 / ADR-A10); parsing is
re-implemented from the public endpoint shape.

CRYPTO-ONLY semantics: the Fear & Greed index is a crypto-market sentiment gauge.
This provider is symbol-agnostic — it just returns the latest index value. The
*caller* is responsible for only applying it to crypto instruments.

Default-DISABLED / shadow-only (PLAN P3-4): nothing in the running scan path
constructs this provider by default. The pure helper ``fear_greed_risk_multiplier``
is the intended "soft de-risk macro slot, only reduce" — it returns a multiplier in
(0, 1] that ONLY reduces risk at sentiment extremes and never increases it above 1.0.

Intended wire-in point (NOT wired here to avoid touching the hot path):
``rtrade.pipeline.scan`` position sizing — multiply the computed risk fraction by
``fear_greed_risk_multiplier(value)`` for crypto symbols only, behind an explicit
feature flag, after the P1 backtest gate proves it (ADR-A08). Until then this module
is available but unused.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rtrade.core.errors import ProviderError, RateLimitExceeded

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.alternative.me"
_DEFAULT_TIMEOUT = 15.0

# --- Soft de-risk thresholds (documented; used only by the pure helper) ------
# The index runs 0..100. alternative.me labels <=25 "Extreme Fear" and >=75
# "Extreme Greed". We treat the inner band [LOWER_EXTREME, UPPER_EXTREME] as
# neutral (multiplier == 1.0, i.e. no change) and only de-risk *outside* it.
_LOWER_EXTREME = 25
_UPPER_EXTREME = 75
# Floor keeps the multiplier strictly > 0 (never zero/negative). At the most
# extreme readings (0 or 100) we reduce sizing to 50% of nominal.
_MIN_MULTIPLIER = 0.5


@dataclass(frozen=True, slots=True)
class FearGreedValue:
    """Latest crypto Fear & Greed reading.

    Attributes:
        value: Index value, 0..100 (0 = extreme fear, 100 = extreme greed).
        classification: Human label as reported upstream (e.g. "Extreme Fear").
        timestamp: Reading time, timezone-aware UTC.
    """

    value: int
    classification: str
    timestamp: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 100:
            raise ValueError(f"fear/greed value out of range: {self.value}")
        if self.timestamp.tzinfo is None:
            raise ValueError("fear/greed timestamp must be timezone-aware UTC")


def fear_greed_risk_multiplier(value: int) -> float:
    """Pure soft de-risk multiplier in (0, 1] for a Fear & Greed reading.

    Returns 1.0 (no change) inside the neutral band [25, 75]. Outside the band we
    *only reduce* risk, scaling linearly down to ``_MIN_MULTIPLIER`` (0.5) at the
    extremes (0 = extreme fear, 100 = extreme greed). The result is never > 1.0 and
    never <= 0 — it can only shrink position sizing, never grow it.

    Args:
        value: Fear & Greed index value. Clamped to 0..100 defensively.

    Returns:
        A multiplier in (0, 1].
    """
    v = max(0, min(100, value))

    if _LOWER_EXTREME <= v <= _UPPER_EXTREME:
        return 1.0

    if v < _LOWER_EXTREME:
        # Extreme fear: 1.0 at LOWER_EXTREME -> _MIN_MULTIPLIER at 0.
        frac = v / _LOWER_EXTREME
    else:
        # Extreme greed: 1.0 at UPPER_EXTREME -> _MIN_MULTIPLIER at 100.
        frac = (100 - v) / (100 - _UPPER_EXTREME)

    multiplier = _MIN_MULTIPLIER + (1.0 - _MIN_MULTIPLIER) * frac
    # Defensive clamp: guarantee (0, 1].
    return max(_MIN_MULTIPLIER, min(1.0, multiplier))


def _parse_latest(body: object) -> FearGreedValue | None:
    """Defensively parse the alternative.me /fng/ payload to a typed value.

    Expected shape::

        {"data": [{"value": "40", "value_classification": "Fear",
                   "timestamp": "1551157200"}], ...}

    Returns None when the payload has no usable row.
    """
    if not isinstance(body, dict):
        logger.warning("fear/greed: unexpected payload type", got=type(body).__name__)
        return None

    rows = body.get("data")
    if not isinstance(rows, list) or not rows:
        logger.info("fear/greed: empty data array")
        return None

    row = rows[0]
    if not isinstance(row, dict):
        logger.warning("fear/greed: row not an object")
        return None

    raw_value = row.get("value")
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        logger.warning("fear/greed: unparseable value", raw_value=raw_value)
        return None
    value = max(0, min(100, value))

    classification = str(row.get("value_classification") or "").strip() or "Unknown"

    raw_ts = row.get("timestamp")
    try:
        ts = datetime.fromtimestamp(int(str(raw_ts).strip()), tz=UTC)
    except (TypeError, ValueError):
        logger.warning("fear/greed: unparseable timestamp, using now", raw_ts=raw_ts)
        ts = datetime.now(UTC)

    return FearGreedValue(value=value, classification=classification, timestamp=ts)


class FearGreedProvider:
    """Keyless async client for the alternative.me crypto Fear & Greed index.

    Transient-only retry (matches the calendar providers). Maps 429 ->
    ``RateLimitExceeded`` and other 4xx/5xx -> ``ProviderError``. Standalone class
    (no relevant ABC in ``rtrade.data.base``).
    """

    def __init__(self, *, http_timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=http_timeout,
            headers={
                "User-Agent": "robil-trade/1.0 (+fear-greed; signal-only)",
                "Accept": "application/json",
            },
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        return await self._http.get(path, params=params)

    async def fetch_latest(self) -> FearGreedValue | None:
        """Fetch the latest Fear & Greed reading, or None when unavailable.

        Raises:
            RateLimitExceeded: on HTTP 429.
            ProviderError: on other HTTP errors or transport failure.
        """
        try:
            resp = await self._get("/fng/", {"limit": "1", "format": "json"})
        except httpx.HTTPError as exc:
            raise ProviderError(f"fear/greed HTTP error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("fear/greed 429")
        if resp.status_code >= 400:
            raise ProviderError(f"fear/greed HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"fear/greed invalid JSON: {exc}") from exc

        result = _parse_latest(body)
        if result is not None:
            logger.info(
                "fear/greed fetched",
                value=result.value,
                classification=result.classification,
            )
        return result

    async def close(self) -> None:
        await self._http.aclose()
