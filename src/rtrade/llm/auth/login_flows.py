"""Tiga gaya login OAuth. Tidak ada yang menyentuh sesi/token tool consumer lain."""

from __future__ import annotations

import os
from enum import StrEnum


class LoginFlow(StrEnum):
    LOOPBACK = "loopback"
    PASTE_URL = "paste_url"
    DEVICE_CODE = "device_code"


def auto_flow(preferred: str | None) -> LoginFlow:
    """Pilih flow: --flow eksplisit > headless→paste_url > ada DISPLAY→loopback."""
    if preferred:
        return LoginFlow(preferred)
    headless = (
        not (os.environ.get("DISPLAY") or os.environ.get("BROWSER"))
        or os.environ.get("SSH_CONNECTION") is not None
    )
    return LoginFlow.PASTE_URL if headless else LoginFlow.LOOPBACK
