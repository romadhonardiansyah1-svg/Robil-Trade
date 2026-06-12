"""Netralkan teks tak-tepercaya sebelum masuk prompt LLM (S4).

Defence against prompt injection from untrusted text fields
(economic event names, free-text fields from providers).
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS = re.compile(
    r"(ignore|abaikan|disregard|forget).{0,20}(previous|above|prior|instruction|sebelum)"
    r"|system\s*prompt|you are now|kamu sekarang|override|jailbreak"
    r"|confidence\s*[:=]\s*[01]\.\d|verdict\s*[:=]\s*(CONFIRM|VETO)",
    re.IGNORECASE,
)


def sanitize_untrusted(text: str, *, max_len: int = 120) -> str:
    """Pangkas, buang kontrol char, tandai upaya injeksi."""
    text = "".join(ch for ch in text if ch.isprintable())[:max_len]
    if _INJECTION_PATTERNS.search(text):
        return "[REDACTED:suspicious]"
    return text


def contains_injection(text: str) -> bool:
    """Return True if text contains suspicious injection patterns."""
    return bool(_INJECTION_PATTERNS.search(text))
