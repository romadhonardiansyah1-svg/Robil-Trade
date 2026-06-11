"""Exception hierarchy. Every module raises subclasses of RTradeError so callers
can catch domain failures without swallowing programming errors.
"""


class RTradeError(Exception):
    """Base class for all domain errors."""


class ConfigError(RTradeError):
    """Invalid, missing, or guardrail-weakening configuration."""


class DataValidationError(RTradeError):
    """Market/calendar data failed validation (bad OHLC, naive datetime, gap)."""


class StaleDataError(DataValidationError):
    """Data is older than the freshness limit (GR-06)."""


class ProviderError(RTradeError):
    """Upstream data provider failure (HTTP, schema drift, auth)."""


class RateLimitExceeded(ProviderError):
    """Local token bucket exhausted — caller must back off, not retry-loop."""


class StorageError(RTradeError):
    """Database/Redis failure wrapped with context."""


class LLMOutputError(RTradeError):
    """LLM returned output that failed schema validation after retry."""


class LLMUnavailableError(RTradeError):
    """All LLM providers failed (timeout, auth, rate-limit)."""


class GuardrailViolation(RTradeError):
    """A guardrail gate failed. Carries the gate id (e.g. 'GR-04')."""

    def __init__(self, gate_id: str, reason: str) -> None:
        self.gate_id = gate_id
        self.reason = reason
        super().__init__(f"{gate_id}: {reason}")
