"""structlog processor: redaksi nilai sensitif sebelum ditulis (S2).

Melindungi dari kebocoran API key/token/secret ke log, baik di key maupun di value.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|refresh_token|access_token)",
    re.IGNORECASE,
)
_PATTERNS = [
    re.compile(r"(apikey=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9\-]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{10,}\b"),  # Google API key shape
]


def redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: redact sensitive values before rendering."""
    for k, v in list(event_dict.items()):
        if _SENSITIVE_KEYS.search(k):
            event_dict[k] = "***REDACTED***"
        elif isinstance(v, str):
            s = v
            for pat in _PATTERNS:
                if pat.groups:
                    s = pat.sub(r"\1***", s)
                else:
                    s = pat.sub("***", s)
            event_dict[k] = s
    return event_dict
