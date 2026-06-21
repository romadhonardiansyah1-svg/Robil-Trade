"""structlog processor: redaksi nilai sensitif sebelum ditulis (S2).

Melindungi dari kebocoran API key/token/secret ke log, baik di key maupun di value.
Bekerja rekursif: dict/list bersarang ikut diredaksi.
"""

from __future__ import annotations

from collections.abc import MutableMapping
import re
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|authorization"
    r"|refresh_token|access_token)",
    re.IGNORECASE,
)
_PATTERNS = [
    # URL query-string secrets: token=, api_key=, apikey=, access_token=, refresh_token=
    re.compile(
        r"((?:access_token|refresh_token|api[_-]?key|apikey|token)=)[^&\s]+",
        re.IGNORECASE,
    ),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9\-]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{10,}\b"),  # Google API key shape
]

_REDACTED = "***REDACTED***"


def _redact_str(value: str) -> str:
    """Apply value-pattern redaction to a single string."""
    s = value
    for pat in _PATTERNS:
        if pat.groups:
            s = pat.sub(r"\1***", s)
        else:
            s = pat.sub("***", s)
    return s


def _redact_value(key: str, value: Any) -> Any:
    """Redact a value given its (possibly empty) key context, recursing into
    nested dict/list structures."""
    if key and _SENSITIVE_KEYS.search(key):
        return _REDACTED
    if isinstance(value, MutableMapping):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, str):
        return _redact_str(value)
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact sensitive values before rendering."""
    for k, v in list(event_dict.items()):
        event_dict[k] = _redact_value(str(k), v)
    return event_dict
