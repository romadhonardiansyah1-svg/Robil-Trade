"""Centralized logging setup with secret redaction (S2).

Call `configure_logging()` at every entrypoint (scheduler, API, CLI bot).
"""

from __future__ import annotations

import structlog

from rtrade.core.logging_redact import redact_processor


def configure_logging() -> None:
    """Configure structlog with secret redaction processor."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            redact_processor,  # S2: BEFORE renderer
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
