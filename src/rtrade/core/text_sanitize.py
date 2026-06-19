"""Prompt-injection sanitization untuk text dari sumber eksternal (FR-CAL-06).

Dipakai di ingestion kalender DAN context-pack LLM (defense-in-depth).
Implementasi sendiri (ADR-A10), no GPL/AGPL source.
"""

from __future__ import annotations

import re

_MAX_LEN = 200
_INJECTION_PATTERNS = re.compile(
    r"(?i)\b(ignore (all|previous|the) (instructions?|prompts?)|"
    r"system\s*[:\-]|assistant\s*[:\-]|you are (now )?a|"
    r"<\/?(system|prompt|instruction)|```\w*)"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\r\n\t]+")


def sanitize_event_text(raw: str, *, max_len: int = _MAX_LEN) -> str:
    """Remove control characters, injection patterns, and truncate."""
    if not raw:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", raw).strip()
    cleaned = _INJECTION_PATTERNS.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]
